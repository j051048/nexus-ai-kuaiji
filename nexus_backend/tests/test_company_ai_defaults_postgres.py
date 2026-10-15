"""Real PostgreSQL default rows preserve explicit pauses and native AI gates."""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.accounting.ai_defaults import ensure_company_ai_default
from app.accounting.models import BookCreate, CompanyCreate
from app.accounting.directory import DirectoryService
from app.concurrency.provider import _slot, _release
from app.intelligence.provider import ProviderError
from scripts.migrate_accounting import admin_connection
from test_company_ai_defaults import legacy_rows
from test_postgres_integration import ADMIN_URL, APP_URL, case, postgres

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL, reason="需要隔离的本机 PostgreSQL 测试库"
)


def test_pg_creation_defaults_and_existing_pause_survive_new_book(case):
    c = case
    directory = DirectoryService(c.db)
    explicit = directory.create_company(c.actor, CompanyCreate(name="默认AI公司"))
    legacy = c.service.create_book(c.actor, BookCreate(name="默认AI隐式公司", period="2026-10"))
    for company in (c.company, explicit["id"], legacy["company_id"]):
        with c.db.transaction(actor=c.actor) as conn:
            row = conn.execute(
                "SELECT * FROM ai_company_settings WHERE firm_id=? AND company_id=?",
                (c.actor.firm_id, company),
            ).fetchone()
            assert row["enabled"] == 1 and row["updated_by"] == c.actor.user_id
            audits = conn.execute(
                "SELECT * FROM audit_events WHERE company_id=? AND action='admin.ai.default_enabled'",
                (company,),
            ).fetchall()
            assert len(audits) == 1 and json.loads(audits[0]["details"])["enabled"] is True
            assert not conn.execute(
                "SELECT * FROM queue_automation_policies WHERE company_id=?", (company,)
            ).fetchall()
    with c.db.transaction(write=True, actor=c.actor) as conn:
        conn.execute(
            "UPDATE ai_company_settings SET enabled=0,updated_at='historical-pause' WHERE company_id=?",
            (c.company,),
        )
        assert not ensure_company_ai_default(conn, c.actor, c.company)
    c.service.create_book(
        c.actor, BookCreate(name="后续月份", period="2026-11", company_id=c.company)
    )
    with c.db.transaction(actor=c.actor) as conn:
        row = conn.execute(
            "SELECT enabled,updated_at FROM ai_company_settings WHERE company_id=?", (c.company,)
        ).fetchone()
        assert row["enabled"] == 0 and row["updated_at"] == "historical-pause"


def test_pg_backfill_attribution_preserves_pauses_archived_and_orphans(postgres):
    migration = (
        Path(__file__).resolve().parents[1] / "migrations/postgres/084_company_ai_default_on.sql"
    )
    with admin_connection(ADMIN_URL) as conn:
        with conn.transaction(force_rollback=True):

            class Adapter:
                def execute(self, statement, params=()):
                    return conn.execute(statement.replace("?", "%s"), params)

            legacy_rows(Adapter(), "accounting.")
            conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
            rows = {
                row["company_id"]: row
                for row in conn.execute(
                    "SELECT * FROM accounting.ai_company_settings WHERE company_id LIKE 'default-%%'"
                )
            }
            assert (
                rows["default-missing"]["enabled"] == 1
                and rows["default-missing"]["updated_by"] == "default-owner"
            )
            assert (
                rows["default-fallback"]["enabled"] == 1
                and rows["default-fallback"]["updated_by"] == "company-owner"
            )
            assert (
                rows["default-paused"]["enabled"] == 0
                and rows["default-paused"]["updated_at"] == "historical-explicit"
            )
            assert rows["default-enabled"]["updated_at"] == "historical-explicit"
            assert "default-archived" not in rows and "default-orphan" not in rows
            before = conn.execute(
                "SELECT count(*) AS n FROM accounting.audit_events WHERE company_id LIKE 'default-%%' AND action='admin.ai.default_enabled'"
            ).fetchone()["n"]
            conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
            after = conn.execute(
                "SELECT count(*) AS n FROM accounting.audit_events WHERE company_id LIKE 'default-%%' AND action='admin.ai.default_enabled'"
            ).fetchone()["n"]
            assert before == after == 2


def test_pg_transport_rechecks_pause_without_acquiring_supplier_slot(case):
    c = case
    with c.db.transaction(write=True, actor=c.actor) as conn:
        conn.execute("UPDATE ai_company_settings SET enabled=0 WHERE company_id=?", (c.company,))
    with pytest.raises(ProviderError) as caught:
        _slot(c.db, c.actor, c.company, "synthetic.test", uuid4().hex, 30)
    assert (
        caught.value.code == "processing_paused_before_send" and caught.value.llm_attempted is False
    )
    with admin_connection(ADMIN_URL) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM accounting.provider_request_slots WHERE company_id=%s",
                (c.company,),
            ).fetchone()["n"]
            == 0
        )
    with c.db.transaction(write=True, actor=c.actor) as conn:
        conn.execute("UPDATE ai_company_settings SET enabled=1 WHERE company_id=?", (c.company,))
    token = uuid4().hex
    assert _slot(c.db, c.actor, c.company, "synthetic.test", token, 30)
    _release(c.db, c.actor, token)
