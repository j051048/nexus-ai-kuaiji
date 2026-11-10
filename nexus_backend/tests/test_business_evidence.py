"""Evidence relationships explain existing checks; they never authorize a write."""

from copy import deepcopy

import pytest

from app.accounting.domain import AccountingError
from test_intelligence_workflow import ai_case, create_event, extract
from test_business_context_card import read
from test_stage14_agent_workspace import services


def test_live_card_links_original_extraction_and_amount_difference(ai_case):
    c = ai_case
    c.business.intelligence = c.intelligence
    event = create_event(c, amount="0.40")
    extract(c, event)
    _, agent = services(c)
    card = read(c, agent, event)
    graph = card["relationships"]
    assert graph["version"] == 1
    assert len(graph["original_roots"]) == 1
    assert any(edge["kind"] == "derived_from" for edge in graph["edges"])
    issue = next(row for row in graph["issues"] if row["code"] == "document_business_amount")
    assert issue["comparison"]["state"] == "conflict"
    assert issue["comparison"]["left"] == "0.40"
    assert issue["comparison"]["right"] == "0.30"
    assert issue["comparison"]["delta"] == "0.10"
    assert issue["target"] == "differences" and issue["evidence_ids"]
    assert c.provider.extract_calls == 1 and c.provider.proposal_calls == 0


@pytest.mark.parametrize("change", ["currency", "period", "basis", "object_id"])
def test_different_semantics_are_not_false_conflicts(change):
    from app.agent_workspace.business_evidence import compare_amounts

    left = dict(object_id="event", period="2026-10", currency="CNY", basis="gross", value="120.00")
    right = {**left, "value": "100.00", change: "different"}
    assert compare_amounts(left, right)["state"] == "not_comparable"


def test_comparison_is_decimal_and_unknown_is_not_zero():
    from app.agent_workspace.business_evidence import compare_amounts

    left = dict(object_id="event", period="2026-10", currency="CNY", basis="gross", value="0.30")
    assert compare_amounts(left, {**left, "value": "0.20"})["delta"] == "0.10"
    for bad in (None, True, -1, 0.3, "NaN", "Infinity", "-0.01", "0.001"):
        assert compare_amounts(left, {**left, "value": bad})["state"] == "not_comparable"


def test_same_original_is_one_root_and_payment_is_not_gross_conflict(ai_case):
    from app.agent_workspace.business_evidence import relationships

    c = ai_case
    event = create_event(c)
    extract(c, event)
    with c.db.transaction(actor=c.actors["finance"]) as conn:
        raw = c.business._event(conn, c.actors["finance"], event["id"])
        assessment = c.intelligence._assessment(conn, c.actors["finance"], raw)
    duplicate = {**event["attachments"][0], "id": "duplicate-original"}
    value = {**event, "attachments": [*event["attachments"], duplicate]}
    assessment = deepcopy(assessment)
    assessment["findings"].append(
        dict(
            code="bank_link_amount",
            severity="blocking",
            resolved=False,
            payload={"message": "已关联流水合计与事项金额不一致"},
        )
    )
    graph = relationships(value, assessment, "企业财务", True)
    assert len(graph["original_roots"]) == 1
    assert any(edge["kind"] == "same_source" for edge in graph["edges"])
    issue = next(row for row in graph["issues"] if row["code"] == "bank_link_amount")
    assert issue["comparison"]["state"] == "not_comparable"
    assert "分次" in issue["next_action"]


def test_card_rejects_evidence_change_without_event_version_change(ai_case, monkeypatch):
    c = ai_case
    c.business.intelligence = c.intelligence
    event = create_event(c)
    _, agent = services(c)
    original = c.intelligence._assessment
    calls = 0

    def changed(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        if calls > 1:
            result["findings"][0]["resolved"] = True
        return result

    monkeypatch.setattr(c.intelligence, "_assessment", changed)
    with pytest.raises(AccountingError) as caught:
        read(c, agent, event)
    assert caught.value.status == 409


def test_duplicate_findings_have_one_stable_issue_and_unresolved_wins():
    from app.agent_workspace.business_evidence import relationships, projection_findings

    event = dict(
        id="event-a",
        version=1,
        payload={"occurred_on": "2026-10-01", "amount": "0.30"},
        attachments=[],
    )
    issue = dict(
        code="document_1_original_unverified",
        severity="blocking",
        payload={"message": "请独立核对原件", "document_index": 0},
        resolved=False,
    )
    assessment = {"findings": [issue, {**issue, "resolved": True}]}
    value = relationships(event, assessment, "企业财务", True)
    assert len(value["issues"]) == 1 and value["issues"][0]["resolved"] is False
    assert len(projection_findings(assessment)) == 1
