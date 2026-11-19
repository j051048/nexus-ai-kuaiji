"""PLT04-B: fee variance registry and close guard on PostgreSQL."""

import json

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.accounting.domain import AccountingError
from app.b04_close.service import CloseService
from app.tax.variances import FeeVarianceService
from test_n03_notification_postgres import p0_case, p0_pg  # noqa: F401
from test_p02_formal_read_postgres import formal_case  # noqa: F401


def test_pg_fee_variance_pending_blocks_close(formal_case):
    c = formal_case
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute(
            "UPDATE accounting_policies SET fee_variance_reasons_json=?::jsonb "
            "WHERE ledger_id=(SELECT ledger_id FROM accounting.ledger_periods WHERE book_id=?) "
            "AND status='active'",
            (
                json.dumps([{"code": "bank_fee", "label": "银行手续费", "blocks_close": True, "debit_account": "1002", "credit_account": "6602.02"}]),
                c.book["id"],
            ),
        )
    service = FeeVarianceService(c.db)
    item = service.record(
        c.manager,
        c.book["id"],
        kind="underpaid",
        expected_cents=10_000,
        actual_cents=9_000,
        evidence_ref="synthetic://variance/pg-under-1",
        idempotency_key="pg-variance-under-1",
    )
    assert item["status"] == "pending"
    assert item["reason_code"] == "unresolved"
    with c.db.transaction(actor=c.manager) as conn:
        with pytest.raises(AccountingError) as blocked:
            CloseService(c.db)._assert_no_open_fee_variances(
                conn, c.book["id"], c.book["period"]
            )
        assert blocked.value.status == 409
    handled = service.handle(
        c.manager,
        item["id"],
        reason_code="bank_fee",
        evidence_ref="synthetic://policy/pg-bank-fee",
    )
    assert handled["status"] == "handled"
    with c.db.transaction(actor=c.manager) as conn:
        CloseService(c.db)._assert_no_open_fee_variances(
            conn, c.book["id"], c.book["period"]
        )


def test_pg_concurrent_handle_only_one_succeeds(formal_case):
    c = formal_case
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute(
            "UPDATE accounting_policies SET fee_variance_reasons_json=?::jsonb "
            "WHERE ledger_id=(SELECT ledger_id FROM accounting.ledger_periods WHERE book_id=?) "
            "AND status='active'",
            (
                json.dumps([{"code": "bank_fee", "label": "银行手续费", "blocks_close": True, "debit_account": "1002", "credit_account": "6602.02"}]),
                c.book["id"],
            ),
        )
    service = FeeVarianceService(c.db)
    item = service.record(
        c.manager,
        c.book["id"],
        kind="underpaid",
        expected_cents=10_000,
        actual_cents=9_000,
        evidence_ref="synthetic://variance/pg-concurrent-1",
        idempotency_key="pg-variance-concurrent-1",
    )
    barrier = threading.Barrier(2)

    def work():
        barrier.wait()
        try:
            service.handle(
                c.manager,
                item["id"],
                reason_code="bank_fee",
                evidence_ref="synthetic://policy/pg-concurrent",
            )
            return 200
        except AccountingError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(lambda _: work(), range(2)))
    assert statuses == [200, 409]
    with c.db.transaction(actor=c.manager) as conn:
        row = conn.execute(
            "SELECT status,draft_id FROM accounting.fee_receivable_variances WHERE id=?",
            (item["id"],),
        ).fetchone()
        assert row["status"] == "handled"
        assert row["draft_id"]
        sources = conn.execute(
            "SELECT count(*) FROM accounting.sources WHERE external_id=?",
            (f"fee-variance:{item['id']}",),
        ).fetchone()[0]
        assert sources == 1
