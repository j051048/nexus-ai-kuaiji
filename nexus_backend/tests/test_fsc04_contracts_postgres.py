"""FSC04: industry contracts persist, version and authorize on PostgreSQL."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate
from app.accounting.service import AccountingService, Actor
from app.fsc.contracts import ContractWrite, FscContractService
from app.fsc.profiles import FscProfileService, ProfileConfirm, ProfileWrite, REQUIRED
from scripts.pg_suite_target import load_suite_target, verify_suite_target

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = os.getenv("ACCOUNTING_TEST_DATABASE_URL")
pytestmark = []


@pytest.fixture(scope="module")
def pg():
    from scripts.migrate_accounting import migrate

    target = load_suite_target()
    if target is None:
        pytest.skip("FSC04 PG requires an explicit isolated PostgreSQL runner")
    target = verify_suite_target(target)
    assert (ADMIN_URL, APP_URL) == (target["admin"], target["app"])
    migrate(target["admin"])
    database = Database(target["app"])
    yield database
    database.close()


def _profile(db, manager, company_id: str):
    confirmations = {
        key: {
            "status": "confirmed",
            "owner": "财务负责人",
            "evidence_ref": f"客户确认/{key}",
            "confirmed_at": "2026-11-01T09:00:00+08:00",
        }
        for key in REQUIRED
    }
    payload = {key: None for key in REQUIRED}
    payload["_confirmations"] = confirmations
    profile = FscProfileService(db).create_draft(
        manager, company_id, ProfileWrite(industry="出租车", payload=payload)
    )
    return FscProfileService(db).confirm(
        manager, profile["id"], ProfileConfirm(expected_version=1)
    )


def test_pg_contract_requires_independent_confirmation_and_rls(pg):
    db = pg
    suffix = uuid4().hex[:12]
    firm = "fsc04-firm-" + suffix
    manager = Actor(user_id=f"fsc04-manager-{suffix}", firm_id=firm)
    reviewer = Actor(user_id=f"fsc04-reviewer-{suffix}", firm_id=firm)
    outsider = Actor(user_id=f"fsc04-outsider-{suffix}", firm_id="fsc04-other-" + suffix)
    import psycopg

    with psycopg.connect(ADMIN_URL) as conn:
        for actor, role, email in (
            (manager, "owner", f"manager-{suffix}@example.test"),
            (reviewer, "admin", f"reviewer-{suffix}@example.test"),
            (outsider, "owner", f"outsider-{suffix}@example.test"),
        ):
            conn.execute(
                "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
                (actor.user_id, role, email),
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
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'admin')",
            (firm, reviewer.user_id),
        )
    book = AccountingService(db).create_book(manager, BookCreate(name="虚构FSC04账套", period="2026-10"))
    _profile(db, manager, book["company_id"])
    service = FscContractService(db)
    payload = ContractWrite(
        industry_code="出租车",
        processor_code="taxi_fee",
        capabilities=["taxi_fee_import"],
        source_contracts=[{"code": "fee_rows", "fields": ["amount"], "unit": "CNY"}],
        evidence_policy={"review": "independent"},
    )
    draft = service.create_draft(manager, book["company_id"], payload)
    assert draft["status"] == "draft"
    with pytest.raises(AccountingError) as self_confirm:
        service.confirm(manager, draft["id"], 1)
    assert self_confirm.value.status == 409
    confirmed = service.confirm(reviewer, draft["id"], 1)
    assert confirmed["status"] == "confirmed"
    active = service.activate(manager, draft["id"], 1)
    assert active["status"] == "active"
    assert [row["id"] for row in service.active_matrix(manager)["items"]] == [draft["id"]]
    with pytest.raises(AccountingError) as duplicate:
        service.activate(manager, draft["id"], 1)
    assert duplicate.value.status == 409
    with pytest.raises(AccountingError) as cross:
        service.list(outsider, book["company_id"])
    assert cross.value.status in {403, 404}
    assert service.active_matrix(outsider)["items"] == []
