"""统一待办投影：与实时查询逐行一致、脏标记驱动回退、刷新幂等。"""

from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, ExportRequest
from app.accounting.review import ReviewService
from app.cases.service import CaseService
from app.workspace.projection import projection_clean, refresh_company
from app.workspace.service import WorkspaceService
from test_business import approved, business, submitted  # noqa: F401
from test_business_postgres import business_case, business_postgres  # noqa: F401
from test_company_write_scope_wave1 import tap
from test_intelligence_workflow import ai_case, create_event  # noqa: F401
from test_queue_workflow import enqueue, queue_case  # noqa: F401
from test_stage8_cases import setup as case_setup
from test_stage12_intake import create_intake, intake
from test_stage12_workspace import bank
from test_stage17_monthly_workbench import month_case  # noqa: F401
from test_stage25_service_plans import generated, plans  # noqa: F401

ACTORS = ("employee", "peer", "approver", "finance", "accountant", "manager")


def workspace(c):
    business_service = getattr(c, "business", None) or c.service
    return WorkspaceService(
        c.db,
        c.ledger,
        business_service,
        ReviewService(c.db, c.ledger),
        CaseService(c.db, business_service),
    )


def refresh_projections(c):
    with c.db.transaction() as conn:
        rows = conn.execute(
            "SELECT fc.firm_id,fc.company_id FROM firm_clients fc WHERE fc.status='active'"
        ).fetchall()
    with c.db.transaction(write=True, system=True) as conn:
        for row in rows:
            refresh_company(conn, c.db, row["firm_id"], row["company_id"])


def parity(c, service, actors=ACTORS):
    def actor_of(name):
        return c.actors[name] if name in c.actors else c.manager

    live = {
        (actor, bucket): service.list(actor_of(actor), bucket=bucket, use_projection=False)
        for actor in actors
        for bucket in ("action", "waiting", "all")
    }
    with c.db.transaction(actor=actor_of(actors[0])) as conn:
        assert projection_clean(conn, actor_of(actors[0])), "刷新后企业的投影应可直接服务"
    for (actor, bucket), expected in live.items():
        assert service.list(actor_of(actor), bucket=bucket) == expected, (actor, bucket)


def test_projection_matches_live_for_business_case_review_flow(business):
    c = business
    own = submitted(c)
    peer = submitted(c, actor="peer")
    approved_event = approved(c)  # finance_approved → handoff-finance task
    cases, case_event, _mapping, supplement = case_setup(c)  # creates draft + review task + case
    assert cases.get(c.actors["accountant"], supplement["id"])["state"] == "awaiting_info"
    service = workspace(c)
    # 任何写入之后投影都是脏的；读取路径必须先回退到实时查询。
    with c.db.transaction(actor=c.manager) as conn:
        assert not projection_clean(conn, c.manager)
    fallback = service.list(c.actors["employee"], bucket="all")
    live = service.list(c.actors["employee"], bucket="all", use_projection=False)
    if fallback != live:
        print("FALLBACK", fallback)
        print("LIVE", live)
    assert fallback == live
    refresh_projections(c)
    parity(c, service)


def test_projection_matches_live_for_intake_and_ocr(queue_case):
    c = queue_case
    service = workspace(c)
    draft = create_intake(c, intake(c))
    assert draft["fields"] is not None
    event = create_event(c, "employee")
    enqueue(c, [event])
    refresh_projections(c)
    parity(c, service, actors=("employee", "approver", "finance", "accountant"))


def test_dirty_state_tracks_mutations_and_refresh_is_idempotent(business):
    c = business
    service = workspace(c)
    refresh_projections(c)
    with c.db.transaction(actor=c.manager) as conn:
        assert projection_clean(conn, c.manager)
        baseline = conn.execute(
            "SELECT revision,built_revision FROM workspace_projection_state"
        ).fetchall()
    event = submitted(c)
    with c.db.transaction(actor=c.manager) as conn:
        state = conn.execute(
            "SELECT revision,built_revision FROM workspace_projection_state WHERE company_id=?",
            (c.company["id"],),
        ).fetchone()
    assert state["revision"] > state["built_revision"]
    # 回退：脏企业下的读取仍返回与实时一致的结果。
    assert service.list(c.actors["approver"], bucket="all") == service.list(
        c.actors["approver"], bucket="all", use_projection=False
    )
    refresh_projections(c)
    with c.db.transaction(actor=c.manager) as conn:
        refreshed = conn.execute(
            "SELECT revision,built_revision FROM workspace_projection_state WHERE company_id=?",
            (c.company["id"],),
        ).fetchone()
        rows = conn.execute(
            "SELECT COUNT(*) AS count FROM workspace_task_projections WHERE company_id=?",
            (c.company["id"],),
        ).fetchone()["count"]
    assert refreshed["built_revision"] == refreshed["revision"]
    with c.db.transaction(write=True, system=True) as conn:
        refresh_company(conn, c.db, c.manager.firm_id, c.company["id"])
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) AS count FROM workspace_task_projections WHERE company_id=?",
                (c.company["id"],),
            ).fetchone()["count"]
            == rows
        )
    assert event["id"]


def test_projection_matches_live_for_export_and_delivery(business):
    c = business
    service = workspace(c)
    actor, book = c.actors["accountant"], c.book["id"]
    draft = bank(c)
    c.ledger.approve(actor, book, draft["id"], ApprovalRequest(expected_version=draft["version"]))
    exported = c.ledger.export(
        actor, book, ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex)
    )
    refresh_projections(c)
    parity(c, service, actors=("accountant", "employee", "manager"))
    live = service.list(actor, bucket="all", use_projection=False)
    assert any(row["kind"] == "delivery" and row["ref"] == exported["id"] for row in live["tasks"])


def test_projection_matches_live_for_material_requests(plans):
    c = plans
    generated(c)
    service = WorkspaceService(
        c.db,
        c.accounting,
        c.business,
        ReviewService(c.db, c.accounting),
        CaseService(c.db, c.business),
    )
    refresh_projections(c)
    for actor in (c.employee, c.finance, c.reviewer, c.owner):
        live = service.list(actor, bucket="all", use_projection=False)
        assert service.list(actor, bucket="all") == live


def test_projection_status_and_firm_manager_rebuild(business):
    c = business
    service = workspace(c)
    submitted(c)
    status = service.projection_status(c.manager)
    assert status["total"] >= 1
    assert status["dirty"] + status["never_built"] >= 1
    result = service.rebuild_projections(c.manager, limit=10)
    assert result["rebuilt"] >= 1 and result["failed"] == 0 and result["remaining"] == 0
    after = service.projection_status(c.manager)
    assert after["dirty"] == 0 and after["never_built"] == 0 and after["failing"] == 0
    with c.db.transaction(actor=c.manager) as conn:
        built = conn.execute(
            "SELECT built_at,failures FROM workspace_projection_state WHERE company_id=?",
            (c.company["id"],),
        ).fetchone()
    assert built and built["built_at"] and built["failures"] == 0
    for actor in (c.actors["employee"], c.actors["accountant"]):
        with pytest.raises(AccountingError) as denied:
            service.projection_status(actor)
        assert denied.value.status == 403
    with pytest.raises(AccountingError, match="50"):
        service.rebuild_projections(c.manager, limit=51)


def test_projection_failure_is_recorded_and_never_blocks_reads(business, monkeypatch):
    import app.workspace.service as workspace_service

    c = business
    service = workspace(c)
    submitted(c)

    def boom(*_args, **_kwargs):
        raise RuntimeError("synthetic projection failure")

    monkeypatch.setattr(workspace_service, "refresh_company", boom)
    # 读取路径照常返回实时结果，并把连续失败落库供运维告警。
    assert service.list(c.actors["employee"], bucket="all") == service.list(
        c.actors["employee"], bucket="all", use_projection=False
    )
    with c.db.transaction(actor=c.manager) as conn:
        row = conn.execute(
            "SELECT failures FROM workspace_projection_state WHERE company_id=?",
            (c.company["id"],),
        ).fetchone()
    assert row is not None and row["failures"] >= 1
    result = service.rebuild_projections(c.manager, limit=5)
    assert result["failed"] >= 1
    status = service.projection_status(c.manager)
    assert status["failing"] >= 1


def test_projection_refresh_uses_system_company_scope(business):
    c = business
    service = workspace(c)
    submitted(c)
    calls = tap(c.db)
    service.list(c.actors["employee"], bucket="all")
    scoped = [
        call
        for call in calls
        if call.get("write") and call.get("system") and call.get("write_scope") == "company"
    ]
    assert scoped, "投影刷新必须走系统身份的企业写事务，不能再抢全库写锁"
    assert all(
        call.get("company_id") == c.company["id"] and call.get("firm_id") == c.manager.firm_id
        for call in scoped
    )
