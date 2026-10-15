"""Defaults are explicit settings, never an authorization fallback."""

import json
import sqlite3
from uuid import uuid4

import pytest
import httpx

from app.accounting.ai_defaults import ensure_company_ai_default
from app.accounting.database import Database, migration_files
from app.accounting.domain import Actor, AccountingError
from app.accounting.models import BookCreate, CompanyCreate, CompanyMemberGrant
from app.assistant.service import AssistantService
from app.intelligence.provider import AISettings, OpenAICompatibleProvider
from app.concurrency.provider import _slot, _release, provider_capacity, provider_scope
from app.intelligence.provider import ProviderError
from test_directory import case
from test_intelligence_workflow import ai_case, create_event
from app.intelligence.requests import OCRRequest


def test_all_company_creation_paths_enable_ai_without_automation_or_budget_changes(case):
    c = case
    explicit = c.directory.create_company(c.actor, CompanyCreate(name="默认启用企业"))
    legacy = c.service.create_book(c.actor, BookCreate(name="隐式新企业账套", period="2026-10"))
    for company in (c.company["id"], explicit["id"], legacy["company_id"]):
        with c.db.transaction(actor=c.actor) as conn:
            settings = conn.execute(
                "SELECT * FROM ai_company_settings WHERE firm_id=? AND company_id=?",
                (c.actor.firm_id, company),
            ).fetchone()
            assert settings["enabled"] == 1 and settings["updated_by"] == c.actor.user_id
            events = conn.execute(
                "SELECT * FROM audit_events WHERE action='admin.ai.default_enabled' AND company_id=?",
                (company,),
            ).fetchall()
            assert len(events) == 1 and json.loads(events[0]["details"])["enabled"] is True
            assert not conn.execute(
                "SELECT * FROM queue_automation_policies WHERE company_id=?", (company,)
            ).fetchall()
            assert not conn.execute(
                "SELECT * FROM model_company_policies WHERE company_id=?", (company,)
            ).fetchall()


def test_explicit_pause_survives_default_retry_new_month_and_service_restart(case):
    c = case
    with c.db.transaction(write=True, actor=c.actor) as conn:
        conn.execute(
            "UPDATE ai_company_settings SET enabled=0,updated_at='explicit-pause' WHERE company_id=?",
            (c.company["id"],),
        )
        assert ensure_company_ai_default(conn, c.actor, c.company["id"]) is False
    c.service.create_book(
        c.actor, BookCreate(name="下个月账套", period="2026-11", company_id=c.company["id"])
    )
    restarted = Database(c.db.path)
    with restarted.transaction(actor=c.actor) as conn:
        row = conn.execute(
            "SELECT enabled,updated_at FROM ai_company_settings WHERE company_id=?",
            (c.company["id"],),
        ).fetchone()
        assert row["enabled"] == 0 and row["updated_at"] == "explicit-pause"


def test_default_helper_does_not_grant_employee_configuration_access(case):
    c = case
    employee = c.user("default-employee")
    c.directory.grant_company_member(
        c.actor, c.company["id"], CompanyMemberGrant(email=employee.email, role="employee")
    )
    with pytest.raises(AccountingError):
        with c.db.transaction(write=True, actor=Actor(employee.user_id, c.actor.firm_id)) as conn:
            ensure_company_ai_default(
                conn, Actor(employee.user_id, c.actor.firm_id), c.company["id"]
            )


def test_default_on_does_not_make_an_unconfigured_supplier_available(case):
    c = case
    service = AssistantService(
        c.db, c.service, None, provider=OpenAICompatibleProvider(AISettings())
    )
    with c.db.transaction(actor=c.actor) as conn:
        availability = service._availability(conn, c.actor, c.company["id"])
        assert availability["company_enabled"] is True
        assert (
            availability["supplier_configured"] is False
            and availability["model_available"] is False
        )
        conn.execute("SELECT 1")
    with c.db.transaction(write=True, actor=c.actor) as conn:
        conn.execute(
            "UPDATE ai_company_settings SET enabled=0 WHERE company_id=?", (c.company["id"],)
        )
    with c.db.transaction(actor=c.actor) as conn:
        service.provider = type(
            "ConfiguredProvider", (), {"available": True, "settings": AISettings()}
        )()
        availability = service._availability(conn, c.actor, c.company["id"])
        assert not availability["model_available"] and "管理员已暂停" in availability["notice"]


def test_pause_is_rechecked_at_actual_transport_boundary_without_slot_or_attempt(case):
    c = case
    # The domain may already have allowed/prepared this request before the
    # administrator commits a pause; transport must read the current setting.
    with c.db.transaction(actor=c.actor) as conn:
        assert (
            conn.execute(
                "SELECT enabled FROM ai_company_settings WHERE company_id=?", (c.company["id"],)
            ).fetchone()[0]
            == 1
        )
    with c.db.transaction(write=True, actor=c.actor) as conn:
        conn.execute(
            "UPDATE ai_company_settings SET enabled=0 WHERE company_id=?", (c.company["id"],)
        )
    with provider_scope(c.db, c.actor, c.company["id"]):
        with pytest.raises(ProviderError) as caught:
            with provider_capacity("https://synthetic.test/v1", 1, 5):
                pytest.fail("Paused company must not enter the supplier transport")
    assert caught.value.code == "processing_paused_before_send" and caught.value.status == 403
    assert caught.value.llm_attempted is False
    with c.db.transaction(actor=c.actor) as conn:
        assert conn.execute("SELECT count(*) FROM provider_request_slots").fetchone()[0] == 0
    with c.db.transaction(write=True, actor=c.actor) as conn:
        conn.execute(
            "UPDATE ai_company_settings SET enabled=1 WHERE company_id=?", (c.company["id"],)
        )
    token = uuid4().hex
    assert _slot(c.db, c.actor, c.company["id"], "synthetic.test", token, 30)
    _release(c.db, c.actor, token)


def test_pause_after_budget_preparation_sends_nothing_and_releases_cost(ai_case, monkeypatch):
    c = ai_case
    event = create_event(c)
    sent = []

    def transport(request):
        sent.append(request)
        pytest.fail("Paused processing must never reach HTTP transport")

    c.intelligence.provider = OpenAICompatibleProvider(
        AISettings(
            base_url="https://synthetic.test/v1",
            api_key="synthetic-key",
            vision_model="synthetic-vision",
            journal_model="synthetic-journal",
        ),
        httpx.Client(transport=httpx.MockTransport(transport)),
    )
    import app.concurrency.provider as transport_gate

    original = transport_gate._slot

    def pause_then_acquire(*args, **kwargs):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "UPDATE ai_company_settings SET enabled=0 WHERE company_id=?", (c.company["id"],)
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(transport_gate, "_slot", pause_then_acquire)
    with pytest.raises(AccountingError):
        c.intelligence.extract(
            c.actors["employee"],
            event["id"],
            OCRRequest(
                attachment_id=event["attachments"][0]["id"],
                expected_version=event["version"],
                idempotency_key="pause-at-actual-transport",
            ),
        )
    assert sent == []
    with c.db.transaction(actor=c.manager) as conn:
        cost = conn.execute(
            "SELECT state,estimated_micro FROM model_costs WHERE event_id=?", (event["id"],)
        ).fetchone()
        assert cost["state"] == "released" and cost["estimated_micro"] == 0
        assert conn.execute("SELECT count(*) FROM provider_request_slots").fetchone()[0] == 0


def legacy_rows(conn, prefix=""):
    conn.execute(
        f"INSERT INTO {prefix}users(id,name,email) VALUES('default-owner','归属负责人','default-owner@example.test')"
    )
    conn.execute(
        f"INSERT INTO {prefix}users(id,name,email) VALUES('company-owner','企业负责人','company-owner@example.test')"
    )
    for firm in ("default-firm", "fallback-firm", "orphan-firm"):
        conn.execute(f"INSERT INTO {prefix}firms(id,name) VALUES(?,?)", (firm, firm))
    conn.execute(
        f"INSERT INTO {prefix}memberships(firm_id,user_id,role) VALUES('default-firm','default-owner','owner')"
    )
    for company, firm, status in (
        ("default-missing", "default-firm", "active"),
        ("default-paused", "default-firm", "active"),
        ("default-enabled", "default-firm", "active"),
        ("default-archived", "default-firm", "archived"),
        ("default-fallback", "fallback-firm", "active"),
        ("default-orphan", "orphan-firm", "active"),
    ):
        conn.execute(
            f"INSERT INTO {prefix}companies(id,name,status) VALUES(?,?,?)",
            (company, company, status),
        )
        conn.execute(
            f"INSERT INTO {prefix}firm_clients(firm_id,company_id,status) VALUES(?,?,?)",
            (firm, company, "suspended" if status == "archived" else "active"),
        )
    conn.execute(
        f"INSERT INTO {prefix}company_memberships(company_id,user_id,role) VALUES('default-fallback','company-owner','admin')"
    )
    for company, enabled in (("default-paused", 0), ("default-enabled", 1)):
        conn.execute(
            f"INSERT INTO {prefix}ai_company_settings(firm_id,company_id,enabled,updated_by,updated_at) VALUES('default-firm',?,?,'default-owner','historical-explicit')",
            (company, enabled),
        )


def test_sqlite_upgrade_backfills_only_active_unconfigured_attributable_companies(tmp_path):
    path = tmp_path / "legacy-defaults.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    from app.accounting.database import migration_digest

    for migration in migration_files("sqlite"):
        if int(migration.name[:3]) >= 67:
            break
        conn.executescript(migration.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO schema_migrations(version,checksum) VALUES(?,?)",
            (int(migration.name[:3]), migration_digest(migration)),
        )
        conn.commit()
    legacy_rows(conn)
    conn.commit()
    conn.close()
    db = Database(path)
    with db.transaction() as conn:
        rows = {
            row["company_id"]: dict(row)
            for row in conn.execute("SELECT * FROM ai_company_settings")
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
        audits = conn.execute(
            "SELECT company_id,user_id FROM audit_events WHERE action='admin.ai.default_enabled'"
        ).fetchall()
        assert {row["company_id"] for row in audits} == {"default-missing", "default-fallback"}
    # Re-running the migration inventory does not enable a pause or duplicate audits.
    db._migrate_sqlite()
    with db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE action='admin.ai.default_enabled'"
            ).fetchone()[0]
            == 2
        )
