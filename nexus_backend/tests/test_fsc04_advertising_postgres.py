"""FSC04 advertising revenue drafts on PostgreSQL."""

from __future__ import annotations

import hashlib
import os
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate
from app.accounting.service import AccountingService, Actor
from app.fsc.advertising import AdvertisingDraftWrite, AdvertisingRevenueService
from app.fsc.contracts import ContractWrite, FscContractService
from app.fsc.imports import FscImportService, ImportEnvelopeWrite
from app.fsc.profiles import FscProfileService, ProfileConfirm, ProfileWrite, REQUIRED
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


def test_pg_advertising_draft_uses_confirmed_envelope(pg):
    db, psycopg = pg
    suffix = uuid4().hex[:12]
    firm = "fsc04-adv-firm-" + suffix
    manager = Actor(user_id=f"fsc04-adv-manager-{suffix}", firm_id=firm)
    reviewer = Actor(user_id=f"fsc04-adv-reviewer-{suffix}", firm_id=firm)
    outsider = Actor(user_id=f"fsc04-adv-out-{suffix}", firm_id="fsc04-adv-other-" + suffix)
    with psycopg.connect(ADMIN_URL) as conn:
        for actor, role in ((manager, "owner"), (reviewer, "admin"), (outsider, "owner")):
            conn.execute(
                "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
                (actor.user_id, role, f"{actor.user_id}@example.test"),
            )
        for firm_id, owner in ((firm, manager.user_id), (outsider.firm_id, outsider.user_id)):
            conn.execute(
                "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
                (firm_id, "虚构FSC04广告事务所", owner),
            )
        for actor, role in ((manager, "owner"), (reviewer, "admin"), (outsider, "owner")):
            conn.execute(
                "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,%s)",
                (actor.firm_id, actor.user_id, role),
            )
    book = AccountingService(db).create_book(manager, BookCreate(name="虚构FSC04广告账套", period="2026-10"))
    confirmations = {
        key: {"status": "confirmed", "owner": "财务负责人", "evidence_ref": f"客户确认/{key}",
              "confirmed_at": "2026-11-01T09:00:00+08:00"}
        for key in REQUIRED
    }
    payload = {key: None for key in REQUIRED}
    payload["_confirmations"] = confirmations
    profiles = FscProfileService(db)
    profile = profiles.create_draft(manager, book["company_id"], ProfileWrite(industry="广告", payload=payload))
    profiles.confirm(manager, profile["id"], ProfileConfirm(expected_version=1))
    contracts = FscContractService(db)
    draft = contracts.create_draft(manager, book["company_id"], ContractWrite(
        industry_code="广告",
        processor_code="advertising_project_revenue",
        capabilities=["advertising_project_import", "advertising_revenue_draft"],
        source_contracts=[{"code": "ad_project", "object": "project_revenue", "fields": ["external_id", "date", "amount"], "unit": "CNY"}],
        evidence_policy={"source_original": "required"},
    ))
    contracts.confirm(reviewer, draft["id"], draft["version"])
    contracts.activate(manager, draft["id"], draft["version"])
    imports = FscImportService(db)
    envelope = imports.import_envelope(manager, book["id"], ImportEnvelopeWrite(
        source_system="advertising_project", source_object="project_revenue", external_id="PG-ADV-1",
        source_version=1, filename="广告项目.xlsx", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        sha256=hashlib.sha256(b"pg-fsc04-adv").hexdigest(), size_bytes=1024,
        mapping_version="map-v1", rule_version="rule-v1", unit="CNY", currency="CNY",
        rows=[{"external_id": "R1", "date": "2026-10-02", "amount_cents": 660000, "quantity_milli": 0,
               "unit": "CNY", "currency": "CNY", "target_kind": "project", "target_id": "P1"}],
    ))
    imports.confirm(manager, envelope["id"], 1)
    service = AdvertisingRevenueService(db)
    output = service.generate(manager, book["id"], AdvertisingDraftWrite(envelope_id=envelope["id"]))
    assert output["total_cents"] == 660000
    assert output["lines"][0]["project_id"] == "P1"
    assert len(service.list(manager, book["id"])["items"]) == 1
    with pytest.raises(AccountingError) as cross:
        service.list(outsider, book["id"])
    assert cross.value.status in {403, 404}
