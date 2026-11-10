"""Current-item assistant is a read projection, never an approval or model run."""

import json

import pytest

from app.accounting.domain import AccountingError
from app.business.models import BusinessEventUpdate
from test_business import business, create, submitted
from test_stage14_agent_workspace import services


def read(c, agent, event, actor="finance", **changes):
    scope = dict(
        company_id=event["company_id"], period="2026-10", expected_version=event["version"]
    )
    return agent.business_context(c.actors[actor], event["id"], **(scope | changes))


def test_card_current_scope_and_originals_are_read_only(business):
    c = business
    event = submitted(c)
    event = c.service.approve_department(c.actors["approver"], event["id"], event["version"])
    _, agent = services(c)
    before = c.service.get_event(c.actors["finance"], event["id"])
    card = read(c, agent, event)
    assert card["mode"] == "evidence_summary"
    assert card["scope"] == dict(
        firm_id=c.manager.firm_id,
        company_id=c.company["id"],
        period="2026-10",
        event_id=event["id"],
        event_version=event["version"],
        fact_version=event["fact_version"],
    )
    assert card["next_step"]["label"] == "核对后办理"
    assert card["next_step"]["target"] == "actions"
    assert card["evidence"][0]["attachment_id"] == event["attachments"][0]["id"]
    assert card["evidence"][0]["sha256"] == event["attachments"][0]["sha256"]
    assert "未经" in card["coverage"]
    assert c.service.get_event(c.actors["finance"], event["id"]) == before


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"company_id": "another-company"}, 404),
        ({"period": "2026-11"}, 404),
        ({"expected_version": 99}, 409),
        ({"expected_version": True}, 422),
        ({"expected_version": 1.5}, 422),
    ],
)
def test_card_rejects_wrong_scope_and_version(business, changes, status):
    c = business
    event = create(c)
    _, agent = services(c)
    with pytest.raises(AccountingError) as error:
        read(c, agent, event, **changes)
    assert error.value.status == status


def test_card_does_not_invent_review_or_originals(business):
    c = business
    event = submitted(c)
    _, agent = services(c)
    card = read(c, agent, event, actor="employee")
    assert card["next_step"]["target"] is None
    assert card["next_step"]["owner"] == "部门审批人"
    with pytest.raises(AccountingError) as error:
        read(c, agent, event, actor="peer")
    assert error.value.status == 404
    draft = create(c)
    assert read(c, agent, draft, actor="employee")["evidence"] == []
    assert "补充原件" in read(c, agent, draft, actor="employee")["coverage"]


def test_card_stale_and_revoked_cannot_reuse_previous_result(business):
    c = business
    event = create(c)
    _, agent = services(c)
    read(c, agent, event)
    c.service.update(
        c.actors["employee"],
        event["id"],
        BusinessEventUpdate(
            expected_version=event["version"], payload={**event["payload"], "amount": "0.31"}
        ),
    )
    with pytest.raises(AccountingError) as error:
        read(c, agent, event)
    assert error.value.status == 409
    c.directory.revoke_company_member(c.manager, c.company["id"], "finance")
    with pytest.raises(AccountingError) as error:
        read(c, agent, event)
    assert error.value.status == 404


def test_card_preserves_current_differences_without_calling_provider(ai_case):
    from test_intelligence_workflow import create_event

    c = ai_case
    c.business.intelligence = c.intelligence
    _, agent = services(c)
    event = create_event(c)
    with c.db.transaction(actor=c.actors["finance"]) as conn:
        expected = c.intelligence._assessment(
            conn, c.actors["finance"], c.business._event(conn, c.actors["finance"], event["id"])
        )
    card = read(c, agent, event)
    assert card["fingerprint"] == expected["fingerprint"]
    assert card["differences"] == [
        dict(code=f["code"], severity=f["severity"], message=f["payload"]["message"])
        for f in expected["findings"]
        if not f.get("resolved")
    ]
    assert c.provider.extract_calls == c.provider.proposal_calls == 0
    assert "api_key" not in json.dumps(card)


from test_intelligence_workflow import ai_case  # noqa: E402,F401
from test_stage14_agent_workspace_api import client  # noqa: E402,F401


def test_http_card_requires_identity_scope_and_current_version(client):
    from test_business_api import auth

    headers = auth(client, "employee")
    context = client.get("/api/business/context", headers=headers).json()
    company = next(
        row for row in context["companies"] if row["can_submit"] and row["department_id"]
    )
    response = client.post(
        "/api/business/events",
        headers=headers,
        json={
            "company_id": company["id"],
            "template_id": "reimbursement",
            "payload": {
                "occurred_on": "2026-10-03",
                "description": "只读办理卡片",
                "counterparty": "虚构供方",
                "amount": "12.34",
                "direction": "expense",
                "payment_status": "unpaid",
                "category": "office",
            },
        },
    )
    assert response.status_code == 201
    event = response.json()
    path = f"/api/agent-workspace/context/events/{event['id']}"
    scope = dict(company_id=company["id"], period="2026-10", expected_version=event["version"])
    assert client.get(path, params=scope).status_code == 401
    assert client.get(path, headers=headers).status_code == 422
    assert (
        client.get(path, headers=headers, params=scope | {"expected_version": "true"}).status_code
        == 422
    )
    assert (
        client.get(path, headers=headers, params=scope | {"period": "2026-11"}).status_code == 404
    )
    value = client.get(path, headers=headers, params=scope)
    assert value.status_code == 200 and value.json()["next_step"]["label"] == "补齐并提交"
    assert (
        client.get(f"/api/business/events/{event['id']}", headers=headers).json()["status"]
        == "draft"
    )
