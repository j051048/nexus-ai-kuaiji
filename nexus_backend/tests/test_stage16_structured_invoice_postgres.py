"""The same local parser must keep PostgreSQL RLS and immutable original scope."""
import pytest

from app.accounting.domain import AccountingError
from app.intelligence.provider import AISettings
from app.intake.models import IntakeRecognize
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_intelligence_workflow_postgres import pg_ai
from test_intelligence_workflow import extract
from test_stage12_intake import intake, create_intake, confirmed
from test_stage16_structured_invoice import xml_event, xml_bytes

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 管理员及受限运行测试连接")


def test_pg_local_xml_model_free_scoped_original_and_cache(pg_ai):
    c = pg_ai
    c.provider.settings = AISettings()
    event = xml_event(c)
    run = extract(c, event)
    assert run["status"] == "succeeded" and run["verification_status"] == "not_verified"
    assert extract(c, event)["id"] == run["id"] and c.provider.extract_calls == 0
    with c.db.transaction(actor=c.actors["employee"]) as conn:
        assert conn.execute("SELECT id FROM business_attachments WHERE event_id=?", (event["id"],)).fetchone()[0] == event["attachments"][0]["id"]
    with c.db.transaction(actor=c.actors["outsider"]) as conn:
        assert conn.execute("SELECT * FROM business_attachments WHERE event_id=?", (event["id"],)).fetchall() == []
        assert conn.execute("SELECT * FROM ai_runs WHERE id=?", (run["id"],)).fetchall() == []
    with pytest.raises(AccountingError):
        extract(c, event, actor="outsider")
    with c.psycopg.connect(ADMIN_URL) as conn:
        assert conn.execute("SELECT count(*) FROM accounting.model_costs WHERE run_id=%s", (run["id"],)).fetchone()[0] == 0
    second = xml_event(c)
    assert extract(c, second, key="same-company-xml")["cache_hit"] == 1


def test_pg_xml_intake_mime_and_transfer_retain_original_without_fee(pg_ai):
    c = pg_ai
    c.provider.settings = AISettings()
    service = intake(c)
    draft = create_intake(c, service)
    draft = service.add_attachment(c.actors["employee"], draft["id"], draft["version"], c.storage.put("虚构数电票.xml", "application/xml", xml_bytes()))
    result = service.recognize(c.actors["employee"], draft["id"], draft["attachments"][0]["id"], IntakeRecognize(expected_version=draft["version"], idempotency_key="pg-xml-intake"))
    assert result["status"] == "succeeded" and result["verification_status"] == "not_verified"
    with c.db.transaction(actor=c.actors["peer"]) as conn:
        assert conn.execute("SELECT * FROM intake_attachments WHERE intake_id=?", (draft["id"],)).fetchall() == []
    event = service.complete(c.actors["employee"], draft["id"], confirmed(draft))["event"]
    assert c.provider.extract_calls == 0
    run = c.intelligence.detail(c.actors["employee"], event["id"])["runs"][0]
    assert run["extraction_method"] == "structured_xml" and run["provider"] == "local-structured-parser"
    with c.psycopg.connect(ADMIN_URL) as conn:
        assert conn.execute("SELECT count(*) FROM accounting.model_costs WHERE intake_run_id=%s OR run_id=%s", (result["id"], run["id"])).fetchone()[0] == 0
        assert conn.execute("SELECT media_type FROM accounting.intake_attachments WHERE id=%s", (draft["attachments"][0]["id"],)).fetchone()[0] == "application/xml"
