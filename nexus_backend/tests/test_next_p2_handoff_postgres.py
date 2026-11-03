"""PG16/17 targeted source reads retain dynamic authorization and native guards."""

import pytest

from app.accounting.domain import AccountingError, Actor
from app.handoff.service import HandoffService
from test_tax_workpaper_postgres import pg, case, ADMIN_URL, _insert_paper  # noqa: F401
from test_dcp_r123_postgres import worker_db  # noqa: F401
from test_next_p1_handoff_postgres import export_failure
from test_next_p2_handoff_reads import record_queries
from test_n03_notification_postgres import p0_pg, p0_case, package_case, notification_case  # noqa: F401
from test_n03_worker_postgres import claim, begin, finish


TABLES = {
    "batch_item": "FROM batch_operation_items",
    "notification_investigation": "FROM delivery_notifications",
    "tax_workpaper": "FROM tax_workpapers",
    "delivery_package": "FROM monthly_delivery_packages",
}


def exact_read(c, actor, kind, source_id, monkeypatch):
    service = HandoffService(c.db)
    expected = next(
        item for item in service.queue(actor)["items"] if item["source"]["id"] == source_id
    )
    with monkeypatch.context() as patch:
        statements = record_queries(patch, c.db)
        actual = service.source(
            actor, kind, source_id, expected["source"]["version"], expected["source"]["fingerprint"]
        )
    assert actual == expected
    assert sum(TABLES[kind] in sql for sql in statements) == 1
    assert not any(
        table in sql for sql in statements for key, table in TABLES.items() if key != kind
    )
    with pytest.raises(AccountingError) as error:
        service.source(actor, kind, source_id, actual["source"]["version"], "f" * 64)
    assert error.value.status == 409
    with pytest.raises(AccountingError) as error:
        service.source(
            Actor(actor.user_id, "another-firm"),
            kind,
            source_id,
            actual["source"]["version"],
            actual["source"]["fingerprint"],
        )
    assert error.value.status == 404
    return actual


def test_exact_tax_read_retains_scope_and_superseded_version(case, monkeypatch):
    c = case
    actual = exact_read(c, c.reviewer, "tax_workpaper", c.paper_id, monkeypatch)
    _insert_paper(c, c.maker, version=2)
    with pytest.raises(AccountingError) as error:
        HandoffService(c.db).source(
            c.reviewer, "tax_workpaper", c.paper_id, 1, actual["source"]["fingerprint"]
        )
    assert error.value.status == 404


def test_exact_read_rechecks_revocation_between_requests(case, monkeypatch):
    c = case
    actual = exact_read(c, c.reviewer, "tax_workpaper", c.paper_id, monkeypatch)
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
            (c.book["id"], c.reviewer.user_id),
        )
    with pytest.raises(AccountingError) as error:
        HandoffService(c.db).source(
            c.reviewer, "tax_workpaper", c.paper_id, 1, actual["source"]["fingerprint"]
        )
    assert error.value.status == 404


def test_exact_batch_read_keeps_frozen_scope(case, worker_db, monkeypatch):
    c = case
    item_id = export_failure(c, worker_db)
    actual = exact_read(c, c.maker, "batch_item", item_id, monkeypatch)
    assert actual["scope"]["book_id"] == c.book["id"]


def test_exact_unknown_notification_keeps_attempt(notification_case, monkeypatch):
    c = notification_case
    sent = claim(c)
    assert begin(c, sent)
    assert finish(c, sent, "unknown")
    actual = exact_read(c, c.reviewer, "notification_investigation", sent["id"], monkeypatch)
    assert "不自动重发" in actual["next_action"]
    with c.db.transaction(actor=c.reviewer) as conn:
        row = conn.execute(
            "SELECT state,attempts FROM delivery_notifications WHERE id=?", (sent["id"],)
        ).fetchone()
    assert row["state"] == "unknown" and row["attempts"] == 1


def test_exact_delivery_keeps_assignee_visibility(package_case, monkeypatch):
    c = package_case
    package = c.monthly.create(c.reviewer, c.book["id"], c.request)
    service = HandoffService(c.db)
    service.assign(
        c.manager,
        package["id"],
        package["version"],
        c.reviewer.user_id,
        None,
        "继续核对指定交付版本",
        kind="delivery_package",
    )
    actual = exact_read(c, c.reviewer, "delivery_package", package["id"], monkeypatch)
    assert actual["source"]["fingerprint"] == package["manifest_sha256"]
