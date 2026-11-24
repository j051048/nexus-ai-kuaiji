"""FSC04 import envelopes persist on PostgreSQL with firm isolation."""

from __future__ import annotations

import hashlib
import os
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate
from app.accounting.service import AccountingService, Actor
from app.fsc.imports import FscImportService, ImportEnvelopeWrite
from scripts.pg_suite_target import load_suite_target, verify_suite_target

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = os.getenv("ACCOUNTING_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="需要显式 AK05 四角色隔离 PostgreSQL 运行器",
)


@pytest.fixture(scope="module")
def pg():
    import psycopg
    from scripts.migrate_accounting import migrate

    target = load_suite_target()
    if target is None:
        pytest.skip("FSC04 PG requires an explicit isolated PostgreSQL runner")
    target = verify_suite_target(target)
    assert (ADMIN_URL, APP_URL) == (target["admin"], target["app"])
    migrate(target["admin"])
    database = Database(target["app"])
    yield database, psycopg
    database.close()


def test_pg_import_envelope_persists_and_is_firm_scoped(pg):
    db, psycopg = pg
    suffix = uuid4().hex[:12]
    firm = "fsc04-firm-" + suffix
    manager = Actor(user_id=f"fsc04-manager-{suffix}", firm_id=firm)
    outsider = Actor(user_id=f"fsc04-outsider-{suffix}", firm_id="fsc04-other-" + suffix)
    with psycopg.connect(ADMIN_URL) as conn:
        for actor in (manager, outsider):
            conn.execute(
                "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
                (actor.user_id, "FSC04", f"{actor.user_id}@example.test"),
            )
        for firm_id, owner in ((firm, manager.user_id), (outsider.firm_id, outsider.user_id)):
            conn.execute(
                "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
                (firm_id, "虚构FSC04事务所", owner),
            )
            conn.execute(
                "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
                (firm_id, owner),
            )
    book = AccountingService(db).create_book(manager, BookCreate(name="虚构FSC04账套", period="2026-10"))
    service = FscImportService(db)
    payload = ImportEnvelopeWrite(
        source_system="advertising_project",
        source_object="project_revenue",
        external_id="PG-BATCH-1",
        source_version=1,
        filename="广告项目.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        sha256=hashlib.sha256(b"pg-fsc04").hexdigest(),
        size_bytes=1024,
        mapping_version="map-v1",
        rule_version="rule-v1",
        unit="CNY",
        currency="CNY",
        rows=[{
            "external_id": "R1", "date": "2026-10-02", "amount_cents": 660000,
            "quantity_milli": 1000, "unit": "CNY", "currency": "CNY",
        }],
    )
    draft = service.import_envelope(manager, book["id"], payload)
    assert draft["row_count"] == 1 and draft["total_cents"] == 660000
    confirmed = service.confirm(manager, draft["id"], 1)
    assert confirmed["status"] == "confirmed"
    detail = service.detail(manager, draft["id"])
    assert detail["rows"][0]["external_id"] == "R1"
    receipt = service.row_receipt(manager, draft["id"], 1)
    assert len(receipt["receipt_fingerprint"]) == 64

    with pytest.raises(AccountingError) as cross:
        service.list(outsider, book["id"])
    assert cross.value.status in {403, 404}

    with pytest.raises(AccountingError) as period:
        service.import_envelope(manager, book["id"], ImportEnvelopeWrite(
            **{**payload.model_dump(), "source_version": 2, "rows": [{
                "external_id": "R2", "date": "2026-09-30", "amount_cents": 1,
                "quantity_milli": 0, "unit": "CNY", "currency": "CNY",
            }]},
        ))
    assert period.value.status == 422
