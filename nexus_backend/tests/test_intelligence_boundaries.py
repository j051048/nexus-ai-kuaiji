"""Late responses, duplicate costs and grants are checked with real local transactions."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.accounting.seed import LOCAL_ACTOR
from app.accounting.models import CompanyCreate
from app.business.demo import PERSONAS
from test_intelligence_http import client, uploaded_event, auth


def enable(client, event):
    result = client.put(f"/api/companies/{event['company_id']}/intelligence/settings", headers=auth(client, "accountant"), json={"enabled": True})
    assert result.status_code == 200, result.text


def body(event, key="boundary-extract"):
    return {"attachment_id": event["attachments"][0]["id"], "expected_version": event["version"], "idempotency_key": key}


@pytest.mark.parametrize("change", ["revoked", "disabled"])
def test_late_ocr_after_scope_or_company_setting_change_never_publishes(client, change):
    employee, event = uploaded_event(client)
    enable(client, event)
    original = client.supplier.extract_invoice
    def response(pages, **kwargs):
        result = original(pages, **kwargs)
        if change == "revoked":
            client.app.state.directory.revoke_company_member(LOCAL_ACTOR, event["company_id"], PERSONAS["employee"]["user_id"])
        else:
            client.app.state.intelligence.company_settings(LOCAL_ACTOR, event["company_id"], False)
        return result
    client.supplier.extract_invoice = response
    response = client.post(f"/api/business/events/{event['id']}/ocr", headers=employee, json=body(event))
    assert response.status_code in {403, 404}, response.text
    with client.app.state.service.db.transaction(actor=LOCAL_ACTOR) as conn:
        run = conn.execute("SELECT status,result_json FROM ai_runs WHERE event_id=?", (event["id"],)).fetchone()
    assert run["status"] == "stale" and run["result_json"] is None


def test_parallel_same_key_calls_supplier_once_and_returns_pending_conflict(client):
    employee, event = uploaded_event(client)
    enable(client, event)
    entered, release = Event(), Event()
    original = client.supplier.extract_invoice
    def delayed(pages, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(pages, **kwargs)
    client.supplier.extract_invoice = delayed
    path = f"/api/business/events/{event['id']}/ocr"
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(client.post, path, headers=employee, json=body(event))
        try:
            assert entered.wait(10)
            duplicate = client.post(path, headers=employee, json=body(event))
            assert duplicate.status_code == 409, duplicate.text
        finally:
            release.set()
        assert pending.result(timeout=10).json()["status"] == "succeeded"
    assert client.supplier.extractions == 1


def test_read_only_views_do_not_create_findings(client):
    employee, event = uploaded_event(client)
    enable(client, event)
    for path in [f"/api/business/events/{event['id']}/intelligence", "/api/intelligence/exceptions"]:
        response = client.get(path, headers=employee)
        assert response.status_code == 200, response.text
        assert response.json()["findings"] and response.json()["findings"][0]["id"] is None
    with client.app.state.service.db.transaction(actor=LOCAL_ACTOR) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_findings").fetchone()[0] == 0
    result = client.post(f"/api/business/events/{event['id']}/reconcile", headers=employee)
    assert result.status_code == 200 and all(item["id"] for item in result.json()["findings"])


def test_explicitly_paused_company_keeps_previous_flow_and_no_supplier_cost(client):
    employee, event = uploaded_event(client)
    assert client.put(f"/api/companies/{event['company_id']}/intelligence/settings", headers=auth(client, "accountant"), json={"enabled": False}).status_code == 200
    detail = client.get(f"/api/business/events/{event['id']}/intelligence", headers=employee).json()
    assert detail["enabled"] is False and detail["runs"] == [] and detail["findings"] == []
    result = client.post(f"/api/business/events/{event['id']}/ocr", headers=employee, json=body(event))
    assert result.status_code == 403 and client.supplier.extractions == 0


def test_config_company_ids_are_filtered_even_for_sqlite(client):
    employee, event = uploaded_event(client)
    companies = client.app.state.directory.list_companies(LOCAL_ACTOR)
    for company in companies["companies"]:
        client.app.state.intelligence.company_settings(LOCAL_ACTOR, company["id"], True)
    hidden = client.app.state.directory.create_company(LOCAL_ACTOR, CompanyCreate(name="没有授予员工的新企业"))
    client.app.state.intelligence.company_settings(LOCAL_ACTOR, hidden["id"], True)
    config = client.get("/api/intelligence/config", headers=employee).json()
    assert set(config["enabled_company_ids"]) == {company["id"] for company in companies["companies"]}
    assert hidden["id"] not in config["enabled_company_ids"]


def test_expired_run_by_other_requester_is_not_mutated_or_executed(client):
    employee, event = uploaded_event(client)
    enable(client, event)
    service = client.app.state.intelligence
    from app.accounting.domain import Actor
    actor = Actor(PERSONAS["employee"]["user_id"], LOCAL_ACTOR.firm_id)
    with service.db.transaction(write=True, actor=actor) as conn:
        current = service.business._event(conn, actor, event["id"])
        run, _ = service._claim(conn, actor, current, "ocr", "other-expired", "input", "vision-test", "v1", attachment_id=event["attachments"][0]["id"])
    # created_at is immutable; use a clock boundary instead of editing history.
    import app.intelligence.service as module
    class FarFuture(module.datetime):
        @classmethod
        def now(cls, tz=None):
            return super().now(tz) + module.timedelta(days=1)
    original_datetime = module.datetime
    module.datetime = FarFuture
    try:
        finance = Actor("local-business-finance", LOCAL_ACTOR.firm_id)
        with service.db.transaction(write=True, actor=finance) as conn:
            with pytest.raises(module.AccountingError, match="其他人员"):
                service._claim(conn, finance, current, "ocr", "other-expired", "input", "vision-test", "v1", attachment_id=event["attachments"][0]["id"])
    finally:
        module.datetime = original_datetime
    with service.db.transaction(actor=actor) as conn:
        assert conn.execute("SELECT status FROM ai_runs WHERE id=?", (run["id"],)).fetchone()[0] == "running"
