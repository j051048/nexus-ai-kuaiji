"""Coverage is a declaration backed by frozen originals, then independently reviewed."""

import hashlib
import json
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.invariants import money_to_cents, signed_money_to_cents, signed_cents_to_money
from ..accounting.service import now
from ..import_wizard.storage import ImportOriginalStore


def _open(conn, book):
    if (
        book["period_status"] != "open"
        or conn.execute(
            "SELECT 1 FROM internal_period_locks WHERE book_id=? AND period=?",
            (book["id"], book["period"]),
        ).fetchone()
    ):
        raise AccountingError("已关闭期间不能修改银行覆盖", 409)


def _evidence(service, conn, actor, request):
    book, _ = service._book(conn, actor, request.book_id, write=True)
    if request.period != book["period"]:
        raise AccountingError("银行覆盖期间与当前账套期间不一致", 422)
    account = conn.execute(
        "SELECT * FROM bank_accounts WHERE id=? AND book_id=? AND active IS TRUE",
        (request.account_id, book["id"]),
    ).fetchone()
    if not account:
        raise AccountingError("银行账户不存在或无权访问", 404)
    if not request.source_file_id or not request.source_preview_id or not request.source_sha256:
        raise AccountingError("覆盖须绑定已确认的导入批次、原件及SHA-256", 422)
    source = conn.execute(
        "SELECT f.* FROM bank_import_files f JOIN bank_import_previews p "
        "ON p.file_id=f.id AND p.book_id=f.book_id AND p.firm_id=f.firm_id AND p.company_id=f.company_id "
        "JOIN bank_import_confirmations c ON c.preview_id=p.id AND c.book_id=p.book_id "
        "AND c.firm_id=p.firm_id AND c.company_id=p.company_id "
        "WHERE f.id=? AND p.id=? AND f.book_id=? AND f.firm_id=? AND f.company_id=?",
        (
            request.source_file_id,
            request.source_preview_id,
            book["id"],
            book["firm_id"],
            book["company_id"],
        ),
    ).fetchone()
    if not source:
        raise AccountingError("银行导入原件或确认批次不存在或无权访问", 404)
    if source["sha256"] != request.source_sha256:
        raise AccountingError("银行原件摘要不匹配", 409)
    canonical = "bank_import_file:" + source["id"]
    if request.source_ref and request.source_ref != canonical:
        raise AccountingError("银行证据引用须与服务端原件一致", 422)
    preview = conn.execute(
        "SELECT payload_json FROM bank_import_previews WHERE id=?", (request.source_preview_id,)
    ).fetchone()
    payload = json.loads(preview["payload_json"])
    if payload["request"]["bank_account"] != account["id"]:
        raise AccountingError("导入批次与银行账户不一致", 409)
    net = 0
    for row in payload["rows"]:
        fact = row["source"]
        if row["errors"] or fact["date"][:7] != request.period:
            raise AccountingError("银行批次包含错误或其他期间流水", 409)
        net += money_to_cents(fact["amount"]) * (1 if fact["direction"] == "income" else -1)
    if signed_money_to_cents(request.opening) + net != signed_money_to_cents(request.closing):
        raise AccountingError("银行期初加批次收支与期末不一致", 409)
    return book, dict(source), canonical


def _read_original(service, source):
    storage = getattr(service, "storage", None)
    if storage is None:
        raise AccountingError("银行原件存储未配置，不能核验覆盖证据", 503)
    storage.clone(ImportOriginalStore).read(source)


def declare_coverage(service, actor, request):
    # Slow storage reads happen before the write lock. Sources/previews are
    # immutable; authorize and rebind them again in the final transaction.
    with service.db.transaction(actor=actor) as conn:
        book, source, _ = _evidence(service, conn, actor, request)
        company_id = book["company_id"]
    _read_original(service, source)
    facts = request.model_dump(exclude={"source_ref"})
    digest = hashlib.sha256(
        json.dumps(facts, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    with service.db.transaction(
        write=True, actor=actor, write_scope="company", company_id=company_id
    ) as conn:
        book, _, canonical = _evidence(service, conn, actor, request)
        prior = conn.execute(
            "SELECT * FROM bank_statement_coverages WHERE account_id=? AND period=? AND fingerprint=? ORDER BY created_at,id LIMIT 1",
            (request.account_id, request.period, digest),
        ).fetchone()
        if prior:
            return dict(prior)
        _open(conn, book)
        if conn.execute(
            "SELECT 1 FROM bank_statement_coverages WHERE account_id=? AND period=? AND state IN ('pending_review','verified')",
            (request.account_id, request.period),
        ).fetchone():
            raise AccountingError("该账户本期已有待复核或已核验覆盖，不能覆盖原声明", 409)
        identifier, stamp = uuid4().hex, now()
        conn.execute(
            "INSERT INTO bank_statement_coverages(id,account_id,book_id,period,opening_cents,closing_cents,source_ref,"
            "verified_by,verified_at,created_at,state,version,source_file_id,source_preview_id,source_sha256,created_by,reason,fingerprint) "
            "VALUES(?,?,?,?,?,?,?,NULL,NULL,?,'pending_review',1,?,?,?,?,?,?)",
            (
                identifier,
                request.account_id,
                book["id"],
                request.period,
                signed_money_to_cents(request.opening),
                signed_money_to_cents(request.closing),
                canonical,
                stamp,
                request.source_file_id,
                request.source_preview_id,
                request.source_sha256,
                actor.user_id,
                request.reason,
                digest,
            ),
        )
        service._audit(
            conn,
            actor,
            book["id"],
            "bank.coverage.declare",
            identifier,
            {
                "account_id": request.account_id,
                "source_file_id": request.source_file_id,
                "fingerprint": digest,
            },
        )
        return dict(
            conn.execute(
                "SELECT * FROM bank_statement_coverages WHERE id=?", (identifier,)
            ).fetchone()
        )


def _request(row):
    from .models import BankCoverageCreate

    return BankCoverageCreate(
        book_id=row["book_id"],
        account_id=row["account_id"],
        period=row["period"],
        opening=signed_cents_to_money(row["opening_cents"]),
        closing=signed_cents_to_money(row["closing_cents"]),
        source_ref=row["source_ref"],
        source_file_id=row["source_file_id"],
        source_preview_id=row["source_preview_id"],
        source_sha256=row["source_sha256"],
        reason=row["reason"],
    )


def review_coverage(service, actor, coverage_id, request):
    with service.db.transaction(actor=actor) as conn:
        row = conn.execute(
            "SELECT * FROM bank_statement_coverages WHERE id=?", (coverage_id,)
        ).fetchone()
        if not row:
            raise AccountingError("银行覆盖不存在或无权访问", 404)
        book, _ = service._book(conn, actor, row["book_id"], write=True)
        company_id = book["company_id"]
        if actor.user_id == row["created_by"]:
            raise AccountingError("覆盖提交人不能复核本人声明", 403)
        if row["state"] == "legacy_unverified":
            raise AccountingError("历史覆盖须重新提交真实证据，不能直接核验", 409)
        _, source, _ = _evidence(service, conn, actor, _request(row))
    if request.decision == "verified":
        _read_original(service, source)
    with service.db.transaction(
        write=True, actor=actor, write_scope="company", company_id=company_id
    ) as conn:
        row = conn.execute(
            "SELECT * FROM bank_statement_coverages WHERE id=?", (coverage_id,)
        ).fetchone()
        book, _ = service._book(conn, actor, row["book_id"], write=True)
        _evidence(service, conn, actor, _request(row))
        if actor.user_id == row["created_by"]:
            raise AccountingError("覆盖提交人不能复核本人声明", 403)
        if (
            row["state"] == request.decision
            and row["verified_by"] == actor.user_id
            and row["version"] == request.expected_version + 1
            and row["review_reason"] == request.reason
        ):
            return dict(row)
        _open(conn, book)
        if row["state"] != "pending_review" or row["version"] != request.expected_version:
            raise AccountingError("覆盖状态或版本已变化，请刷新", 409)
        changed = conn.execute(
            "UPDATE bank_statement_coverages SET state=?,version=version+1,verified_by=?,verified_at=?,review_reason=? WHERE id=? AND state='pending_review' AND version=?",
            (
                request.decision,
                actor.user_id,
                now(),
                request.reason,
                coverage_id,
                request.expected_version,
            ),
        )
        if changed.rowcount != 1:
            raise AccountingError("覆盖已被另一复核人处理", 409)
        service._audit(
            conn,
            actor,
            book["id"],
            "bank.coverage.review",
            coverage_id,
            {
                "decision": request.decision,
                "reviewed_version": request.expected_version,
                "reason": request.reason,
            },
        )
        return dict(
            conn.execute(
                "SELECT * FROM bank_statement_coverages WHERE id=?", (coverage_id,)
            ).fetchone()
        )
