"""Single-source reads must not scale with the whole firm's handoff backlog."""

from contextlib import contextmanager

import pytest

from app.accounting.domain import AccountingError
from test_business import business  # noqa: F401
from test_next04_handoff_adapters import handoff, failed_batch, unknown_notification  # noqa: F401


def record_queries(monkeypatch, db):
    statements = []
    original = db.transaction

    class ObservedConnection:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, *args, **kwargs):
            statements.append(sql)
            return self.conn.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.conn, name)

    @contextmanager
    def transaction(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            yield ObservedConnection(conn)

    monkeypatch.setattr(db, "transaction", transaction)
    return statements


@pytest.mark.parametrize("kind", ["batch_item", "notification_investigation"])
def test_exact_source_reads_only_its_domain_and_preserves_projection(handoff, monkeypatch, kind):
    c = handoff
    failed_batch(c)
    unknown_notification(c)
    item = next(row for row in c.handoff.queue(c.manager)["items"] if row["source"]["kind"] == kind)
    statements = record_queries(monkeypatch, c.db)
    actual = c.handoff.source(
        c.manager,
        kind,
        item["source"]["id"],
        item["source"]["version"],
        item["source"]["fingerprint"],
    )
    assert actual == item
    assert not any(
        "FROM tax_workpapers" in sql or "FROM monthly_delivery_packages" in sql
        for sql in statements
    )
    other = "FROM delivery_notifications" if kind == "batch_item" else "FROM batch_operation_items"
    assert not any(other in sql for sql in statements)


def test_one_source_does_not_authorize_every_other_backlog_item(handoff, monkeypatch):
    c = handoff
    failed_batch(c)
    with c.db.transaction(write=True, actor=c.manager) as conn:
        for index in range(20):
            conn.execute(
                "INSERT INTO batch_operations(id,firm_id,kind,status,idempotency_key,request_json,total,succeeded,skipped,failed,created_by,created_at,updated_at) "
                "SELECT ?,firm_id,kind,status,?,request_json,total,succeeded,skipped,failed,created_by,created_at,updated_at FROM batch_operations WHERE id='batch-n04'",
                (f"p2-batch-{index}", f"p2-key-{index}"),
            )
            conn.execute(
                "INSERT INTO batch_operation_items(id,batch_id,firm_id,company_id,company_name,sequence_no,status,attempt,max_attempts,available_at,result_json,error_code,error_detail,created_at,updated_at) "
                "SELECT ?,?,firm_id,company_id,company_name,sequence_no,status,attempt,max_attempts,available_at,result_json,error_code,error_detail,created_at,updated_at FROM batch_operation_items WHERE id='item-n04'",
                (f"p2-item-{index}", f"p2-batch-{index}"),
            )
    item = next(
        row for row in c.handoff.queue(c.manager)["items"] if row["source"]["id"] == "item-n04"
    )
    original = c.handoff.access.book
    checks = []

    def book(*args, **kwargs):
        checks.append(args[2])
        return original(*args, **kwargs)

    monkeypatch.setattr(c.handoff.access, "book", book)
    result = c.handoff.source(c.manager, "batch_item", "item-n04", 2, item["source"]["fingerprint"])
    assert result == item
    assert checks == [c.book["id"]]


def test_targeted_read_does_not_cache_permissions_or_publish_stale_sources(handoff):
    c = handoff
    failed_batch(c)
    item = next(
        row for row in c.handoff.queue(c.manager)["items"] if row["source"]["id"] == "item-n04"
    )
    for actor, fingerprint, status in [
        (c.actors["employee"], item["source"]["fingerprint"], 404),
        (c.manager, "f" * 64, 409),
    ]:
        with pytest.raises(AccountingError) as error:
            c.handoff.source(actor, "batch_item", "item-n04", 2, fingerprint)
        assert error.value.status == status
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute("UPDATE batch_operation_items SET status='succeeded' WHERE id='item-n04'")
    with pytest.raises(AccountingError) as error:
        c.handoff.source(c.manager, "batch_item", "item-n04", 2, item["source"]["fingerprint"])
    assert error.value.status == 404
