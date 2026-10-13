"""Current-document capabilities protect the derived OCR cache under actual RLS."""
import json
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.intelligence.cache import config_key
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_intelligence_workflow_postgres import pg_ai
from test_intelligence_workflow import create_event
from test_stage7_intelligence import identify

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要专用 PG 管理员及受限运行测试连接")


def test_pg_cache_capabilities_never_enumerate_or_disclose_another_event(pg_ai):
    first = create_event(pg_ai)
    identify(pg_ai, first)
    key = config_key(pg_ai.provider.settings, "image/png")
    for actor in (pg_ai.actors["peer"], pg_ai.manager, pg_ai.actors["outsider"]):
        with pg_ai.db.transaction(actor=actor) as conn:
            assert conn.execute("SELECT accounting.read_document_extraction(?,?)", (first["attachments"][0]["id"], key)).fetchone()[0] is None
        with pytest.raises(pg_ai.psycopg.errors.InsufficientPrivilege):
            with pg_ai.db.transaction(actor=actor) as conn:
                conn.execute("SELECT * FROM document_extractions").fetchall()
    peer = create_event(pg_ai, actor="peer")
    reused = identify(pg_ai, peer, actor="peer")
    assert reused["cache_hit"] == 1 and pg_ai.provider.extract_calls == 1
    assert reused["event_id"] == peer["id"] and first["id"] not in str(reused)


def test_pg_cache_capability_rechecks_revoked_membership_and_opt_out(pg_ai):
    event = create_event(pg_ai)
    identify(pg_ai, event)
    key = config_key(pg_ai.provider.settings, "image/png")
    with pg_ai.db.transaction(actor=pg_ai.actors["employee"]) as conn:
        assert json.loads(conn.execute("SELECT accounting.read_document_extraction(?,?)", (event["attachments"][0]["id"], key)).fetchone()[0])["documents"]
    pg_ai.intelligence.company_settings(pg_ai.manager, pg_ai.company["id"], False)
    with pg_ai.db.transaction(actor=pg_ai.actors["employee"]) as conn:
        assert conn.execute("SELECT accounting.read_document_extraction(?,?)", (event["attachments"][0]["id"], key)).fetchone()[0] is None
    pg_ai.intelligence.company_settings(pg_ai.manager, pg_ai.company["id"], True)
    pg_ai.directory.revoke_company_member(pg_ai.manager, pg_ai.company["id"], pg_ai.actors["employee"].user_id)
    with pg_ai.db.transaction(actor=pg_ai.actors["employee"]) as conn:
        assert conn.execute("SELECT accounting.read_document_extraction(?,?)", (event["attachments"][0]["id"], key)).fetchone()[0] is None
    with pytest.raises(AccountingError):
        identify(pg_ai, event)


def test_pg_cache_store_rejects_other_requester_and_failed_run(pg_ai):
    event = create_event(pg_ai)
    run = identify(pg_ai, event)
    fake_key = "forged-config-" + uuid4().hex
    with pg_ai.db.transaction(write=True, actor=pg_ai.actors["finance"]) as conn:
        conn.execute("SELECT accounting.store_document_extraction(?,?)", (run["id"], fake_key))
        assert conn.execute("SELECT accounting.read_document_extraction(?,?)", (event["attachments"][0]["id"], fake_key)).fetchone()[0] is None
    from app.intelligence.provider import ProviderError
    pg_ai.provider.failure = ProviderError("schema_invalid", "合成失败输出")
    failed = identify(pg_ai, event, force_refresh=True)
    assert failed["status"] == "failed"
    with pg_ai.db.transaction(write=True, actor=pg_ai.actors["employee"]) as conn:
        conn.execute("SELECT accounting.store_document_extraction(?,?)", (failed["id"], fake_key))
        assert conn.execute("SELECT accounting.read_document_extraction(?,?)", (event["attachments"][0]["id"], fake_key)).fetchone()[0] is None


def test_pg_cache_force_rls_without_direct_runtime_grants(pg_ai):
    with pg_ai.psycopg.connect(ADMIN_URL) as conn:
        assert conn.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='accounting.document_extractions'::regclass").fetchone() == (True, True)
        for role in ("accounting_app", "accounting_worker"):
            assert conn.execute("SELECT has_table_privilege(%s,'accounting.document_extractions','SELECT'),has_table_privilege(%s,'accounting.document_extractions','INSERT'),has_table_privilege(%s,'accounting.document_extractions','UPDATE')", (role, role, role)).fetchone() == (False, False, False)
