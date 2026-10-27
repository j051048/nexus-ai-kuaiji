"""Explicit real review steps for pre-policy accounting regression fixtures.

No role grant, SQL receipt insertion, policy waiver or service monkeypatch.
The existing accounting assertions still execute against production services.
"""

from app.accounting.access import AccessControl
from app.accounting.domain import Actor, AccountingError
from app.accounting.review_policy import ReviewPolicyService
from app.accounting.review_policy_models import ManualReviewApproval
from app.posting.manual_review import ManualReviewService
from app.posting.models import ManualVoucher, ManualLine
from app.accounting.models import cents_to_money


def independent_reviewer(db, actor, book_id):
    with db.transaction(actor=actor) as conn:
        users = conn.execute(
            "SELECT id FROM users WHERE active=1 AND id<>? ORDER BY id", (actor.user_id,)
        ).fetchall()
        for row in users:
            candidate = Actor(row["id"], actor.firm_id)
            try:
                AccessControl().book(conn, candidate, book_id, "review")
            except AccountingError:
                continue
            return candidate
    raise AssertionError("Synthetic fixture must supply an existing independent reviewer")


def reviewed_manual(posting, actor, request):
    service = ManualReviewService(posting.db)
    prepared = service.prepare(actor, request)["request"]
    if prepared["status"] == "pending":
        reviewer = independent_reviewer(posting.db, actor, request.book_id)
        version = ReviewPolicyService(posting.db).get(reviewer, request.book_id)["policy"][
            "version"
        ]
        service.approve(
            reviewer,
            prepared["id"],
            ManualReviewApproval(
                expected_version=prepared["version"],
                expected_policy_version=version,
                reason="隔离回归：不同会计逐行复核虚构凭证",
            ),
        )
    return request.model_copy(update={"review_request_id": prepared["id"]})


def post_reviewed_manual(posting, actor, request):
    return posting.post_manual(actor, reviewed_manual(posting, actor, request))


def reverse_reviewed(posting, actor, voucher_id, request):
    original = posting.voucher_detail(actor, voucher_id)
    payload = ManualVoucher(
        command_key=request.command_key,
        book_id=original["voucher"]["book_id"],
        voucher_date=request.voucher_date,
        voucher_type=original["voucher"]["voucher_type"],
        description="冲销 "
        + original["voucher"]["voucher_type"]
        + "-"
        + str(original["voucher"]["voucher_no"]),
        relation_kind="reversal",
        original_voucher_id=voucher_id,
        reason=request.reason,
        lines=[
            ManualLine(
                account_id=r["account_id"],
                account_version=r["account_version"],
                debit=cents_to_money(r["credit_cents"]),
                credit=cents_to_money(r["debit_cents"]),
                auxiliary_values=r.get("auxiliary_values", {}),
                source_ref=r["source_ref"],
            )
            for r in original["lines"]
        ],
    )
    reviewed = reviewed_manual(posting, actor, payload)
    return posting.reverse_voucher(
        actor,
        voucher_id,
        request.model_copy(update={"review_request_id": reviewed.review_request_id}),
    )


def seed_pre_review_manual_voucher(posting, actor, request):
    """Only a pre-100 SQLite migration fixture, never a current runtime bypass.

    This writes genuine legacy evidence under the old schema, so migration
    tests can prove that its missing independent-review proof stays missing.
    """
    from uuid import uuid4
    import json

    assert not posting.db.is_postgres
    with posting.db.transaction(write=True, actor=actor) as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] < 100
        book, _ = posting._book(conn, actor, request.book_id, write=True)
        binding = posting._binding(conn, book["id"])
        lines, _ = posting._validate_lines(conn, [line.model_dump() for line in request.lines])
        number = posting._next_number(conn, book["id"], request.voucher_type)
        identifier, command_id = uuid4().hex, uuid4().hex
        digest = posting._content_fingerprint(
            book["id"], request.voucher_date, request.voucher_type, request.description, lines
        )
        stamp = posting._now()
        conn.execute(
            "INSERT INTO vouchers(id,firm_id,company_id,ledger_id,book_id,period,voucher_type,voucher_no,"
            "voucher_date,status,description,source_kind,opening_binding_id,review_policy_version,"
            "content_fingerprint,command_id,posted_by,posted_at) VALUES(?,?,?,?,?,?,?,?,?,'posted',?,'manual',?,?,?,?,?,?)",
            (
                identifier,
                actor.firm_id,
                book["company_id"],
                binding["ledger_id"],
                book["id"],
                book["period"],
                request.voucher_type,
                number,
                request.voucher_date,
                request.description,
                binding["id"],
                "legacy-before-review-policy",
                digest,
                command_id,
                actor.user_id,
                stamp,
            ),
        )
        for ordinal, line in enumerate(lines, 1):
            conn.execute(
                "INSERT INTO voucher_lines(id,voucher_id,line_no,account_id,account_version,code_snapshot,name_snapshot,"
                "category_snapshot,normal_balance_snapshot,debit_cents,credit_cents,aux_json,source_ref) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    identifier,
                    ordinal,
                    *[
                        line[k]
                        for k in (
                            "account_id",
                            "account_version",
                            "code_snapshot",
                            "name_snapshot",
                            "category_snapshot",
                            "normal_balance_snapshot",
                            "debit_cents",
                            "credit_cents",
                            "aux_json",
                            "source_ref",
                        )
                    ],
                ),
            )
        conn.execute(
            "INSERT INTO posting_commands(id,firm_id,company_id,book_id,command_key,request_fingerprint,voucher_id,result_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                command_id,
                actor.firm_id,
                book["company_id"],
                book["id"],
                request.command_key,
                digest,
                identifier,
                json.dumps({"voucher_id": identifier}),
                actor.user_id,
                stamp,
            ),
        )
        return posting._voucher_view(conn, identifier)
