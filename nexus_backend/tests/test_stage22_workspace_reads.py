"""Home reads retain live scopes while removing repeated SQL projections."""

import json

import pytest

from app.accounting.domain import AccountingError
from test_business import attach, business, create
from test_intelligence_workflow import ai_case
from test_queue_workflow import create_event, enqueue, queue_case
from test_stage12_workspace import workspace
from test_stage14_agent_workspace import services
from test_stage19_request_reads import count_statements


def test_home_task_page_and_totals_share_one_authorized_projection(business, monkeypatch):
    c, service = business, workspace(business)
    action = create(c, description="本人待填写")
    waiting = attach(c, create(c, description="本人待审批"))
    c.service.submit(c.actors["employee"], waiting["id"], waiting["version"])
    hidden = create(c, actor="peer", description="其他员工私密资料")
    actor = c.actors["employee"]

    with count_statements(c.db, monkeypatch) as statements:
        first = service.list(actor, bucket="all", limit=1)
    assert len(statements) == 2  # Live actor/role, then one authorized projection.
    assert first["pagination"] == {"total": 2, "limit": 1, "offset": 0, "has_more": True}
    assert first["counts"] == {"action": 1, "waiting": 1}
    assert hidden["id"] not in json.dumps(first)
    assert all(not key.startswith("_") for key in first["tasks"][0])

    empty = service.list(actor, bucket="all", offset=20, limit=1)
    assert empty["tasks"] == [] and empty["pagination"]["total"] == 2
    assert empty["counts"] == first["counts"]
    searched = service.list(actor, bucket="all", q="待填写")
    assert searched["pagination"]["total"] == 1
    assert searched["tasks"][0]["ref"] == action["id"]
    assert searched["counts"] == first["counts"]
    outside = service.list(actor, company_id=c.other["id"], bucket="all")
    assert outside["tasks"] == [] and outside["pagination"]["total"] == 0
    assert outside["counts"] == first["counts"]

    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute("DELETE FROM company_memberships WHERE user_id=?", (actor.user_id,))
    revoked = service.list(actor, bucket="all")
    assert revoked["tasks"] == [] and revoked["counts"] == {}
    assert revoked["pagination"]["total"] == 0


def test_client_tasks_prune_heavy_branches_without_widening_visibility(business):
    """代账包干制下客户端首页只编译必要分支；事务所视图保持完整且授权不变。"""
    c, service = business, workspace(business)
    create(c, description="本人待填写")
    employee = c.actors["employee"]
    with c.db.transaction(actor=employee) as conn:
        query, _ = service._query(conn, employee)
    assert (
        "SELECT 'intake' AS kind" in query
        and "SELECT 'case' AS kind" in query
        and "SELECT 'material' AS kind" in query
    )
    for heavy in (
        "SELECT 'review' AS kind",
        "SELECT 'export' AS kind",
        "SELECT 'delivery' AS kind",
        "SELECT 'proposal' AS kind",
        "SELECT 'alert' AS kind",
        "SELECT 'finance' AS kind",
        "SELECT 'approval' AS kind",
    ):
        assert heavy not in query, heavy
    assert "migration_task" not in query
    manager = c.manager
    with c.db.transaction(actor=manager) as conn:
        full_query, _ = service._query(conn, manager)
    for kept in (
        "SELECT 'review' AS kind",
        "SELECT 'export' AS kind",
        "SELECT 'proposal' AS kind",
        "SELECT 'alert' AS kind",
    ):
        assert kept in full_query, kept
    assert "migration_task" in full_query
    tasks = service.list(employee, bucket="all")
    assert any(task["kind"] == "business" for task in tasks["tasks"])


def test_connection_helpers_share_snapshot_but_not_authorization_between_requests(
    business, monkeypatch
):
    c, service = business, workspace(business)
    actor = c.actors["employee"]
    create(c)
    with count_statements(c.db, monkeypatch) as statements:
        with c.db.transaction(actor=actor) as conn:
            profile = service.profile_in_connection(conn, actor)
            tasks = service.list_in_connection(conn, actor)
    assert len(statements) == 3
    assert profile["can_submit"] and tasks["pagination"]["total"] == 1
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute("UPDATE users SET active=0 WHERE id=?", (actor.user_id,))
    for read in (service.profile, service.list):
        with pytest.raises(AccountingError) as blocked:
            read(actor)
        assert blocked.value.status == 403


def test_agent_summary_reuses_live_role_check_without_leaking_hidden_jobs(queue_case, monkeypatch):
    c = queue_case
    _, agent = services(c)
    own = create_event(c)
    peer = create_event(c, actor="peer")
    enqueue(c, [own, peer], actor="finance")
    actor = c.actors["employee"]
    with count_statements(c.db, monkeypatch) as statements:
        summary = agent.summary(actor)
    assert len(statements) == 2
    assert summary["counts"] == {"active": 1, "attention": 0, "done": 0, "total": 1}
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute("UPDATE users SET active=0 WHERE id=?", (actor.user_id,))
    for read in (agent.runs, agent.summary, agent.companies):
        with pytest.raises(AccountingError) as blocked:
            read(actor)
        assert blocked.value.status == 403
