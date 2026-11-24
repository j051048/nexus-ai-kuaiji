"""FSC13 PostgreSQL: external closing evidence is firm-scoped and idempotent."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from scripts.pg_suite_target import load_suite_target, verify_suite_target

pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="需要显式 AK05 四角色隔离 PostgreSQL 运行器",
)

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = os.getenv("ACCOUNTING_TEST_DATABASE_URL")


@pytest.fixture(scope="module")
def pg():
    import psycopg
    from scripts.migrate_accounting import migrate

    target = load_suite_target()
    if target is None:
        pytest.skip("FSC13 PG requires an explicit isolated PostgreSQL runner")
    target = verify_suite_target(target)
    assert (ADMIN_URL, APP_URL) == (target["admin"], target["app"])
    migrate(target["admin"])
    yield psycopg


def _actor(psycopg, prefix):
    from app.accounting.domain import Actor

    suffix = uuid4().hex[:10]
    actor = Actor(f"{prefix}-{suffix}", f"{prefix}-firm-{suffix}")
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
            (actor.user_id, actor.user_id, f"{actor.user_id}@example.test"),
        )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
            (actor.firm_id, actor.firm_id, actor.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
            (actor.firm_id, actor.user_id),
        )
    return actor


def _confirm(psycopg, db, actor, company_id, mode="internal"):
    from app.tax.pilot import DEPENDENCIES, PilotBoundaryService

    config = {"authority_mode": mode, "source_of_truth": "客户确认的月度资料"}
    if mode != "internal":
        config.update(
            {"tplus_version": "T+Cloud 3.0", "template_sha256": "a" * 64, "voucher_word": "记"}
        )
    deps = [
        {
            "code": code,
            "status": "not_applicable",
            "evidence_ref": "FSC13 PostgreSQL 合成确认",
        }
        for code, _ in DEPENDENCIES
    ]
    service = PilotBoundaryService(db)
    service.update(actor, company_id, 0, config, deps)
    service.confirm(actor, company_id, 1)


def test_external_closing_evidence_is_firm_scoped_and_idempotent(pg):
    psycopg = pg
    from app.accounting.database import Database
    from app.accounting.models import BookCreate
    from app.accounting.service import AccountingService
    from app.periodic.authority import ClosingAuthorityService
    from app.periodic.models import ClosingEvidenceWrite

    db = Database(APP_URL)
    owner = _actor(psycopg, "fsc13-owner")
    other = _actor(psycopg, "fsc13-other")
    book = AccountingService(db).create_book(owner, BookCreate(name="FSC13 PG", period="2026-10"))
    _confirm(psycopg, db, owner, book["company_id"])

    service = ClosingAuthorityService(db)
    payload = ClosingEvidenceWrite(
        book_id=book["id"],
        source_system="tplus",
        source_object="closing_voucher",
        source_id="PG-CLOSE-1",
        source_version="1",
        amount="100.00",
        reason="FSC13 PostgreSQL 外部回执",
        details={"voucher": "记-001"},
    )
    first = service.bind(owner, payload)
    replay = service.bind(owner, payload)
    assert first["status"] == "evidence_only"
    assert replay["replayed"] is True
    assert replay["id"] == first["id"]

    with pytest.raises(Exception) as caught:
        service.evidence(other, book["id"])
    assert getattr(caught.value, "status", None) == 404

    with psycopg.connect(ADMIN_URL) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM accounting.closing_external_evidence WHERE book_id=%s",
                (book["id"],),
            ).fetchone()[0]
            == 1
        )
