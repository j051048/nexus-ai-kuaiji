"""Native restricted-role tests of present-day rechecks on PG16/17."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, ExportRequest
from app.accounting.legacy_review import LegacyReviewService, RecheckPrepare, RecheckConfirm
from test_business_postgres import ADMIN_URL, business_postgres, business_case  # noqa: F401
from test_review_policy_postgres import _draft, _prepared_manual, _review_manual


def strip_authority(c, kind, identifier):
    # Privileged historical restore fixture, never an application operation.
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "ALTER TABLE accounting.review_authority_snapshots DISABLE TRIGGER authority_immutable"
        )
        conn.execute(
            "DELETE FROM accounting.review_authority_snapshots WHERE subject_kind=%s AND subject_id=%s",
            (kind, identifier),
        )
        conn.execute(
            "ALTER TABLE accounting.review_authority_snapshots ENABLE TRIGGER authority_immutable"
        )


def pending_recheck(c, kind="draft"):
    if kind == "draft":
        d = _draft(c)
        c.service.approve(
            c.reviewer, c.book, d["id"], ApprovalRequest(expected_version=d["version"])
        )
    elif kind == "manual":
        service, _, prepared = _prepared_manual(c)
        d = _review_manual(c, service, prepared)
    else:
        from app.periodic.service import PeriodicService
        from app.periodic.models import AccrualRule, DraftCommand
        from test_review_policy_postgres import _manual

        payload = _manual(c)
        service = PeriodicService(c.db)
        rule = service.create_accrual_rule(
            c.owner,
            AccrualRule(
                book_id=c.book,
                name="PG合成计提",
                amount="0.30",
                debit_account_id=payload.lines[0].account_id,
                debit_account_version=1,
                credit_account_id=payload.lines[1].account_id,
                credit_account_version=1,
                reason="期末旧稿补核验证",
            ),
        )
        d = service.generate_accrual(c.owner, rule["id"])["draft"]
        d = service.approve(
            c.reviewer,
            d["id"],
            DraftCommand(expected_version=d["version"], reason="原始期末人工复核"),
        )["draft"]
    strip_authority(c, kind, d["id"])
    svc = LegacyReviewService(c.db)
    source = svc.inspect(c.owner, c.book, kind, d["id"])
    request = svc.prepare(
        c.owner,
        c.book,
        kind,
        d["id"],
        RecheckPrepare(
            expected_version=source["source_version"],
            content_fingerprint=source["content_fingerprint"],
            expected_policy_version=source["policy_version"],
            reason="当前承担旧稿内容制单责任",
        ),
    )
    return svc, source, request


@pytest.mark.parametrize("kind", ["draft", "manual", "periodic"])
def test_pg_current_recheck_preserves_historical_proof(business_case, kind):
    c = business_case
    svc, source, request = pending_recheck(c, kind)
    confirm = RecheckConfirm(reason="今天独立核对该版本原件和分录")
    with pytest.raises(AccountingError):
        svc.confirm(c.owner, request["id"], confirm)
    svc.confirm(c.reviewer, request["id"], confirm)
    if kind == "draft":
        c.service.export(
            c.owner,
            c.book,
            ExportRequest(draft_ids=[source["subject_id"]], idempotency_key="legacy-pg-export"),
        )
    elif kind == "manual":
        from app.posting.manual_review import ManualReviewService
        from app.accounting.review_policy_models import ManualReviewPost

        original = source["content"]["original_request"]
        posted = ManualReviewService(c.db).post(
            c.reviewer,
            source["subject_id"],
            ManualReviewPost(
                expected_version=original["version"], command_key=original["command_key"]
            ),
        )
        assert posted["voucher"]["review_reviewer_id"] == c.reviewer.user_id
    else:
        from app.periodic.service import PeriodicService
        from app.periodic.models import DraftPost

        posted = PeriodicService(c.db).post(
            c.owner,
            source["subject_id"],
            DraftPost(
                expected_version=source["source_version"],
                command_key="pg-current-periodic-post",
                voucher_date="2026-10-31",
                reason="当前补核后记账",
            ),
        )
        assert posted["draft"]["status"] == "posted"
    with c.db.transaction(actor=c.owner) as conn:
        assert not conn.execute(
            "SELECT 1 FROM review_authority_snapshots WHERE subject_id=?", (source["subject_id"],)
        ).fetchone()
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM legacy_review_confirmations WHERE request_id=?",
                (request["id"],),
            ).fetchone()[0]
            == 1
        )


def test_pg_native_recheck_cannot_forge_scope_identity_or_timestamp(business_case):
    c = business_case
    svc, source, request = pending_recheck(c)
    for book, person, role in (
        (c.other_book, c.reviewer.user_id, "reviewer"),
        (c.book, c.owner.user_id, "owner"),
        (c.book, c.finance.user_id, "reviewer"),
    ):
        with pytest.raises(c.psycopg.Error):
            with c.db.transaction(write=True, actor=c.reviewer) as conn:
                conn.execute(
                    "INSERT INTO legacy_review_confirmations VALUES(?,?,?,?,?,'2000-01-01')",
                    (request["id"], book, person, role, "伪造身份范围不能复核"),
                )
    with c.db.transaction(write=True, actor=c.reviewer) as conn:
        conn.execute(
            "INSERT INTO legacy_review_confirmations VALUES(?,?,?,?,?,'2000-01-01')",
            (request["id"], c.book, c.reviewer.user_id, "reviewer", "当前人工核对不得回填旧日期"),
        )
    with c.db.transaction(actor=c.owner) as conn:
        assert (
            conn.execute(
                "SELECT reviewed_at FROM legacy_review_confirmations WHERE request_id=?",
                (request["id"],),
            ).fetchone()[0][:4]
            != "2000"
        )
    with c.db.transaction(actor=c.finance) as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM legacy_review_confirmations WHERE book_id=?", (c.other_book,)
            ).fetchall()
            == []
        )


def test_pg_concurrent_recheck_has_one_confirmation_and_one_export(business_case):
    c = business_case
    svc, source, request = pending_recheck(c)

    def run(_):
        return svc.confirm(
            c.reviewer, request["id"], RecheckConfirm(reason="并发重试同一项当前复核")
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert results[0] == results[1]

    def export(_):
        return c.service.export(
            c.owner,
            c.book,
            ExportRequest(draft_ids=[source["subject_id"]], idempotency_key="same-legacy-export"),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(export, range(2)))
    assert results[0] == results[1]


def test_pg_recheck_without_original_receipt_does_not_fabricate_one(business_case):
    c = business_case
    svc, source, request = pending_recheck(c)
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "ALTER TABLE accounting.draft_review_receipts DISABLE TRIGGER rr_draft_review_receipts_delete"
        )
        conn.execute(
            "DELETE FROM accounting.draft_review_receipts WHERE draft_id=%s",
            (source["subject_id"],),
        )
        conn.execute(
            "ALTER TABLE accounting.draft_review_receipts ENABLE TRIGGER rr_draft_review_receipts_delete"
        )
    svc.confirm(c.reviewer, request["id"], RecheckConfirm(reason="只证明今天核对，不补造历史凭据"))
    c.service.export(
        c.owner,
        c.book,
        ExportRequest(draft_ids=[source["subject_id"]], idempotency_key="no-old-receipt"),
    )
    with c.db.transaction(actor=c.owner) as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM draft_review_receipts WHERE draft_id=?", (source["subject_id"],)
            ).fetchall()
            == []
        )


def test_pg_recheck_rechecks_current_revocation_and_native_self_review(business_case):
    c = business_case
    svc, source, request = pending_recheck(c)
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True, actor=c.owner) as conn:
            conn.execute(
                "INSERT INTO legacy_review_confirmations VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)",
                (request["id"], c.book, c.owner.user_id, "owner", "制单人不能原生SQL自审"),
            )

    from app.accounting.directory import DirectoryService

    DirectoryService(c.db).revoke_book_member(c.owner, c.book, c.reviewer.user_id)
    with pytest.raises(AccountingError):
        svc.confirm(c.reviewer, request["id"], RecheckConfirm(reason="撤权后的旧请求不能执行"))
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True, actor=c.reviewer) as conn:
            conn.execute(
                "INSERT INTO legacy_review_confirmations VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)",
                (request["id"], c.book, c.reviewer.user_id, "reviewer", "撤权后SQL也不能确认"),
            )


def test_pg_waiting_recheck_cannot_outlive_revocation(business_case, monkeypatch):
    from threading import Event

    c = business_case
    svc, source, request = pending_recheck(c)
    checked = Event()
    original = svc.access.book

    def book(conn, actor, identifier, capability="read"):
        value = original(conn, actor, identifier, capability)
        if actor == c.reviewer and capability == "review":
            checked.set()
        return value

    monkeypatch.setattr(svc.access, "book", book)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with c.db.transaction(
            write=True, actor=c.owner, company_id=c.company, write_scope="company"
        ) as conn:
            task = pool.submit(
                svc.confirm,
                c.reviewer,
                request["id"],
                RecheckConfirm(reason="等待企业锁的补核请求"),
            )
            assert checked.wait(5)
            conn.execute(
                "DELETE FROM book_access WHERE book_id=? AND user_id=?",
                (c.book, c.reviewer.user_id),
            )
        with pytest.raises(AccountingError):
            task.result(timeout=10)
    with c.db.transaction(actor=c.owner) as conn:
        assert not conn.execute(
            "SELECT 1 FROM legacy_review_confirmations WHERE request_id=?", (request["id"],)
        ).fetchone()


def test_pg_completed_current_proof_survives_reviewer_departure(business_case):
    from app.accounting.directory import DirectoryService

    c = business_case
    svc, source, request = pending_recheck(c)
    svc.confirm(c.reviewer, request["id"], RecheckConfirm(reason="离岗前今天已完成独立核对"))
    DirectoryService(c.db).revoke_book_member(c.owner, c.book, c.reviewer.user_id)
    c.service.export(
        c.owner,
        c.book,
        ExportRequest(draft_ids=[source["subject_id"]], idempotency_key="frozen-current-proof"),
    )
