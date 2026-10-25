"""Explicit ledger bank identity and atomic residual carry-forward.

No matching by account names, no new posting, and no inferred period lock.
"""

import json
from uuid import uuid4
from ..accounting.domain import AccountingError
from ..accounting.service import now
from ..posting.service import PostingService
from .reconciliation import auxiliary, formal_buckets


def prior_period(service, conn, book, ledger_id):
    if service.db.is_postgres:
        return conn.execute(
            "SELECT * FROM accounting.c01_prior_period(?)", (book["id"],)
        ).fetchone()
    return conn.execute(
        "SELECT book_id,period FROM ledger_periods WHERE ledger_id=? AND period<? ORDER BY period DESC LIMIT 1",
        (ledger_id, book["period"]),
    ).fetchone()


def approved_bindings(conn, book_id):
    return [
        dict(x)
        for x in conn.execute(
            "SELECT b.*,a.ledger_account_id,a.aux_json FROM bank_identity_bindings b JOIN bank_identity_reviews r ON r.binding_id=b.id AND r.decision='approved' JOIN bank_accounts a ON a.id=b.bank_account_id WHERE b.book_id=?",
            (book_id,),
        )
    ]


def continuity(service, conn, actor, book_id):
    book, ledger = service._book(conn, actor, book_id)
    prior = prior_period(service, conn, book, ledger)
    banks = conn.execute("SELECT id FROM bank_accounts WHERE book_id=?", (book_id,)).fetchall()
    bound = approved_bindings(conn, book_id)
    legacy = not banks or len(bound) != len(banks)
    base = {
        "continuity_not_verified": legacy,
        "identity_status": "legacy_unbound" if legacy else "bound",
    }
    if not prior:
        try:
            binding = PostingService(service.db)._binding(conn, book_id)
        except AccountingError:
            return {**base, "status": "frozen_start_required", "passed": False}
        return {
            **base,
            "status": "frozen_start",
            "passed": True,
            "opening_binding_id": binding["id"],
            "boundary": "frozen_opening_only",
        }
    row = conn.execute(
        "SELECT p.* FROM subledger_period_bridges p JOIN subledger_bridge_completions c ON c.bridge_id=p.id WHERE p.book_id=? AND p.source_book_id=?",
        (book_id, prior["book_id"]),
    ).fetchone()
    if not row:
        return {
            **base,
            "status": "bridge_required",
            "passed": False,
            "previous_period": prior["period"],
        }
    return {
        **base,
        "status": "bridged",
        "passed": True,
        "continuity_not_verified": False,
        "bridge_id": row["id"],
        "source_book_id": row["source_book_id"],
        "fingerprint": row["request_fingerprint"],
    }


def _write_scope(service, actor, book_id):
    with service.db.transaction(actor=actor) as conn:
        book, _ = service._book(conn, actor, book_id, write=True)
        return book["company_id"]


def _insert(conn, table, value):
    conn.execute(
        "INSERT INTO "
        + table
        + "("
        + ",".join(value)
        + ") VALUES("
        + ",".join("?" for _ in value)
        + ")",
        tuple(value.values()),
    )


def create_identity(service, actor, request):
    company = _write_scope(service, actor, request.book_id)
    with service.db.transaction(
        write=True, actor=actor, write_scope="company", company_id=company
    ) as conn:
        book, ledger = service._book(conn, actor, request.book_id, write=True)
        row = dict(
            id=uuid4().hex,
            book_id=book["id"],
            firm_id=book["firm_id"],
            company_id=company,
            ledger_id=ledger,
            name=request.name,
            reason=request.reason,
            created_by=actor.user_id,
            created_at=now(),
        )
        _insert(conn, "ledger_bank_identities", row)
        service._audit(
            conn, actor, book["id"], "bank.identity.create", row["id"], {"ledger_id": ledger}
        )
        return row


def bind_identity(service, actor, request):
    from .service import _fingerprint

    company = _write_scope(service, actor, request.book_id)
    with service.db.transaction(
        write=True, actor=actor, write_scope="company", company_id=company
    ) as conn:
        book, ledger = service._book(conn, actor, request.book_id, write=True)
        fingerprint = _fingerprint(request.model_dump())
        prior = conn.execute(
            "SELECT * FROM bank_identity_bindings WHERE book_id=? AND idempotency_key=?",
            (book["id"], request.idempotency_key),
        ).fetchone()
        if prior:
            if prior["request_fingerprint"] != fingerprint:
                raise AccountingError("银行归属幂等键已用于不同内容", 409)
            return dict(prior)
        if (
            not conn.execute(
                "SELECT 1 FROM ledger_bank_identities WHERE id=? AND ledger_id=? AND company_id=?",
                (request.stable_bank_id, ledger, company),
            ).fetchone()
            or not conn.execute(
                "SELECT 1 FROM bank_accounts WHERE id=? AND book_id=? AND active IS TRUE",
                (request.bank_account_id, book["id"]),
            ).fetchone()
        ):
            raise AccountingError("长期银行身份和当期账户须属同一长期账套", 404)
        row = dict(
            id=uuid4().hex,
            book_id=book["id"],
            firm_id=book["firm_id"],
            company_id=company,
            ledger_id=ledger,
            bank_account_id=request.bank_account_id,
            stable_bank_id=request.stable_bank_id,
            idempotency_key=request.idempotency_key,
            request_fingerprint=fingerprint,
            reason=request.reason,
            created_by=actor.user_id,
            created_at=now(),
        )
        _insert(conn, "bank_identity_bindings", row)
        service._audit(
            conn,
            actor,
            book["id"],
            "bank.identity.bind",
            row["id"],
            {"stable_bank_id": request.stable_bank_id},
        )
        return row


def review_binding(service, actor, binding_id, request):
    with service.db.transaction(actor=actor) as conn:
        binding = conn.execute(
            "SELECT * FROM bank_identity_bindings WHERE id=?", (binding_id,)
        ).fetchone()
        if not binding:
            raise AccountingError("银行归属声明不存在", 404)
        book, _ = service._book(conn, actor, binding["book_id"], write=True)
    with service.db.transaction(
        write=True, actor=actor, write_scope="company", company_id=book["company_id"]
    ) as conn:
        book, _ = service._book(conn, actor, book["id"], write=True)
        binding = conn.execute(
            "SELECT * FROM bank_identity_bindings WHERE id=?", (binding_id,)
        ).fetchone()
        if binding["created_by"] == actor.user_id:
            raise AccountingError("银行归属须由另一位当前复核人员确认", 403)
        prior = conn.execute(
            "SELECT * FROM bank_identity_reviews WHERE binding_id=?", (binding_id,)
        ).fetchone()
        if prior:
            if (
                prior["decision"] != request.decision
                or prior["reason"] != request.reason
                or prior["created_by"] != actor.user_id
            ):
                raise AccountingError("已完成银行归属复核，内容不得更改", 409)
            return dict(prior)
        if request.decision == "approved" and any(
            x["bank_account_id"] == binding["bank_account_id"]
            or x["stable_bank_id"] == binding["stable_bank_id"]
            for x in approved_bindings(conn, book["id"])
        ):
            raise AccountingError("本期银行身份已有有效归属，不得重复映射", 409)
        row = dict(
            id=uuid4().hex,
            book_id=book["id"],
            firm_id=book["firm_id"],
            company_id=book["company_id"],
            binding_id=binding_id,
            decision=request.decision,
            reason=request.reason,
            created_by=actor.user_id,
            created_at=now(),
        )
        _insert(conn, "bank_identity_reviews", row)
        service._audit(
            conn,
            actor,
            book["id"],
            "bank.identity.review",
            row["id"],
            {"decision": request.decision},
        )
        return row


def create_bridge(service, actor, request):
    from .service import _fingerprint

    company = _write_scope(service, actor, request.book_id)
    with service.db.transaction(
        write=True, actor=actor, write_scope="company", company_id=company
    ) as conn:
        target, ledger = service._book(conn, actor, request.book_id, write=True)
        source, source_ledger = service._book(conn, actor, request.source_book_id, write=True)
        fingerprint = _fingerprint(request.model_dump())
        prior = conn.execute(
            "SELECT * FROM subledger_period_bridges WHERE book_id=?", (target["id"],)
        ).fetchone()
        if prior:
            if prior["request_fingerprint"] != fingerprint:
                raise AccountingError("本期已使用另一桥接请求", 409)
            return bridge_view(conn, prior["id"])
        previous = prior_period(service, conn, target, ledger)
        year, month = map(int, source["period"].split("-"))
        next_period = f"{year + (month == 12):04d}-{month % 12 + 1:02d}"
        if (
            source_ledger != ledger
            or source["company_id"] != company
            or not previous
            or previous["book_id"] != source["id"]
            or target["period"] != next_period
        ):
            raise AccountingError("只能桥接同企业同长期账套的相邻期间", 409)
        lock = conn.execute(
            "SELECT * FROM internal_period_locks WHERE book_id=?", (source["id"],)
        ).fetchone()
        if not lock:
            raise AccountingError("前期必须完成独立月结复核并锁期", 409)
        if conn.execute(
            "SELECT 1 FROM vouchers WHERE book_id=? UNION ALL SELECT 1 FROM subledger_items WHERE book_id=?",
            (target["id"], target["id"]),
        ).fetchone():
            raise AccountingError("桥接须在本期记账及录入子账前完成", 409)
        old_binding = PostingService(service.db)._binding(conn, source["id"])
        binding = PostingService(service.db)._binding(conn, target["id"])
        closing = {k: v for k, v in formal_buckets(conn, source["id"]).items() if v}
        opening = {
            k: v for k, v in formal_buckets(conn, target["id"], opening_only=True).items() if v
        }
        if closing != opening:
            raise AccountingError("本期冻结期初与前期正式结账余额逐科目逐辅助维度不一致", 409)
        source_banks = approved_bindings(conn, source["id"])
        target_banks = approved_bindings(conn, target["id"])
        source_count = conn.execute(
            "SELECT COUNT(*) FROM bank_accounts WHERE book_id=?", (source["id"],)
        ).fetchone()[0]
        target_count = conn.execute(
            "SELECT COUNT(*) FROM bank_accounts WHERE book_id=?", (target["id"],)
        ).fetchone()[0]
        if (
            len(source_banks) != source_count
            or len(target_banks) != target_count
            or {
                x["stable_bank_id"]: (x["ledger_account_id"], auxiliary(x["aux_json"]))
                for x in source_banks
            }
            != {
                x["stable_bank_id"]: (x["ledger_account_id"], auxiliary(x["aux_json"]))
                for x in target_banks
            }
        ):
            raise AccountingError("跨期银行身份缺少双方独立审核的稳定映射", 409)
        if conn.execute(
            "SELECT 1 FROM bank_statement_lines WHERE book_id=? AND bank_account_id IS NULL",
            (source["id"],),
        ).fetchone():
            raise AccountingError("前期历史银行流水尚未绑定稳定银行身份", 409)
        for check in (
            service.control_reconciliation_in_transaction(conn, actor, source["id"]),
            service.bank_reconciliation_in_transaction(conn, actor, source["id"]),
        ):
            if not check["passed"]:
                raise AccountingError("前期结账证据当前未通过复核", 409)
        bridge_id = uuid4().hex
        row = dict(
            id=bridge_id,
            book_id=target["id"],
            firm_id=target["firm_id"],
            company_id=company,
            ledger_id=ledger,
            source_book_id=source["id"],
            source_lock_id=lock["id"],
            source_binding_id=old_binding["id"],
            target_binding_id=binding["id"],
            idempotency_key=request.idempotency_key,
            request_fingerprint=fingerprint,
            reason=request.reason,
            created_by=actor.user_id,
            created_at=now(),
        )
        _insert(conn, "subledger_period_bridges", row)
        for item in conn.execute(
            "SELECT * FROM subledger_items WHERE book_id=? AND status IN('open','partial','settled') ORDER BY id",
            (source["id"],),
        ).fetchall():
            remaining = int(item["amount_cents"]) - service._remaining(conn, item["id"])
            if remaining < 0:
                raise AccountingError("前期子账存在超额核销", 409)
            if not remaining:
                continue
            imported = dict(item)
            imported.update(
                id=uuid4().hex,
                book_id=target["id"],
                period=target["period"],
                amount_cents=remaining,
                status="open",
                source_kind="opening",
                source_id=bridge_id,
                source_version=1,
                created_by=actor.user_id,
                created_at=now(),
            )
            _insert(conn, "subledger_items", imported)
            _insert(
                conn,
                "subledger_bridge_items",
                dict(
                    id=uuid4().hex,
                    book_id=target["id"],
                    firm_id=target["firm_id"],
                    company_id=company,
                    bridge_id=bridge_id,
                    source_item_id=item["id"],
                    target_item_id=imported["id"],
                    remaining_cents=remaining,
                    created_by=actor.user_id,
                    created_at=now(),
                ),
            )
        _insert(
            conn,
            "subledger_bridge_completions",
            dict(
                id=uuid4().hex,
                book_id=target["id"],
                firm_id=target["firm_id"],
                company_id=company,
                bridge_id=bridge_id,
                created_by=actor.user_id,
                created_at=now(),
            ),
        )
        service._audit(
            conn,
            actor,
            target["id"],
            "subledger.period.bridge",
            bridge_id,
            {"source_book_id": source["id"], "source_lock_id": lock["id"]},
        )
        return bridge_view(conn, bridge_id)


def bridge_view(conn, bridge_id):
    result = dict(
        conn.execute("SELECT * FROM subledger_period_bridges WHERE id=?", (bridge_id,)).fetchone()
    )
    result["items"] = [
        dict(x)
        for x in conn.execute(
            "SELECT source_item_id,target_item_id,remaining_cents FROM subledger_bridge_items WHERE bridge_id=? ORDER BY source_item_id",
            (bridge_id,),
        )
    ]
    result["status"] = (
        "complete"
        if conn.execute(
            "SELECT 1 FROM subledger_bridge_completions WHERE bridge_id=?", (bridge_id,)
        ).fetchone()
        else "incomplete"
    )
    return result
