"""费用报销单与声明式工作流引擎的同事务绑定（Batch 10-2.1）。

覆盖：提交即绑定生效版本、重复提交不重复建实例、两人并发审批只有一方成功、
审批同事务推进原单与实例、退回补件挂起与重新提交恢复、中途发版存量单锁旧版本、
以及企业没有发布定义时完全保持既有默认审批流。
"""

from types import SimpleNamespace

import pytest

from app.accounting.domain import AccountingError
from app.business.models import BusinessEventCreate
from app.workflow import engine
from app.workflow import binding as workflow_binding
from app.workflow.models import DefinitionWrite
from app.workflow.service import WorkflowService
from test_business import business  # noqa: F401  真实建库/事务所/企业/账套夹具


def _payload(**changes):
    base = {
        "occurred_on": "2026-10-03",
        "description": "垫付办公耗材",
        "counterparty": "耗材商店",
        "amount": "3000.00",
        "direction": "expense",
        "payment_status": "unpaid",
        "category": "office",
    }
    base.update(changes)
    return base


def _publish(case, *, process_kind="expense_reimbursement", changes=None):
    template = engine.builtin_template(process_kind)
    payload = {
        "company_id": case.company["id"],
        "process_kind": process_kind,
        "nodes": template["nodes"],
        "routing": template["routing"],
        "note": "",
        "publish": True,
    }
    payload.update(changes or {})
    return case.workflow.create_version(case.manager, DefinitionWrite(**payload))


def _event(case, **changes):
    return case.service.create(
        case.actors["employee"],
        BusinessEventCreate(
            company_id=case.company["id"],
            template_id="reimbursement",
            payload=_payload(**changes),
        ),
    )


def _attach(case, event):
    return case.service.add_attachment(
        case.actors["employee"],
        event["id"],
        event["version"],
        {
            "id": "attachment-" + event["id"],
            "storage_key": event["id"],
            "filename": "办公发票.pdf",
            "media_type": "application/pdf",
            "sha256": "a" * 64,
            "size_bytes": 400,
        },
    )


def _submitted(case, **changes):
    event = _attach(case, _event(case, **changes))
    return case.service.submit(case.actors["employee"], event["id"], event["version"])


def _finish(case, event):
    step = case.service.approve_department(case.actors["approver"], event["id"], event["version"])
    return case.service.approve_finance(case.actors["finance"], event["id"], step["version"])


@pytest.fixture
def bound(business):  # noqa: F811
    business.workflow = WorkflowService(business.db)
    return business


def _instance(case, event):
    with case.db.transaction(actor=case.manager) as conn:
        row = workflow_binding.instance_for(conn, case.manager.firm_id, event["id"])
    return dict(row) if row else None


def test_submit_binds_the_active_version_in_the_same_transaction(bound):
    case = bound
    definition = _publish(case)
    event = _submitted(case)

    instance = _instance(case, event)
    assert instance is not None
    assert instance["definition_id"] == definition["id"]
    assert instance["definition_version"] == 1
    assert instance["subject_type"] == "business_event"
    assert instance["subject_id"] == event["id"]
    assert instance["subject_version"] == event["submitted_version"]
    assert instance["book_id"] is None
    assert instance["amount_cents"] == 300_000
    assert instance["status"] == "running"
    # 低金额走「提交 → 财务复核」路径，部门节点不存在时实例停在财务复核
    assert instance["current_node"] == "finance_review"
    summary = event["workflow_instance"]
    assert summary["id"] == instance["id"]
    assert summary["definition_version"] == 1
    assert summary["current_node"] == "finance_review"
    assert summary["path"] == ["draft", "finance_review"]


def test_repeated_submit_never_creates_a_second_instance(bound):
    case = bound
    _publish(case)
    event = _submitted(case)
    with case.db.transaction(actor=case.manager) as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM workflow_instances WHERE firm_id=? AND subject_id=?",
            (case.manager.firm_id, event["id"]),
        ).fetchone()[0]
    assert rows == 1

    # 已提交单据再次提交被版本校验拦下，实例数量不变
    with pytest.raises(AccountingError) as failure:
        case.service.submit(case.actors["employee"], event["id"], event["version"])
    assert failure.value.status in {403, 409, 422}
    with case.db.transaction(actor=case.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM workflow_instances WHERE subject_id=?", (event["id"],)
            ).fetchone()[0]
            == 1
        )


def test_concurrent_department_approval_lets_only_one_win(bound):
    case = bound
    _publish(case, changes={"routing": {**engine.builtin_template("expense_reimbursement")["routing"], "auto_approve_below_cents": 0, "dual_approval_from_cents": 500_000}})
    event = _submitted(case, amount="6000.00")  # 走 draft → manager_review → finance_review
    instance = _instance(case, event)
    assert instance["current_node"] == "manager_review"

    stale_version = event["version"]
    first = case.service.approve_department(case.actors["approver"], event["id"], stale_version)
    assert first["status"] == "department_approved"
    with pytest.raises(AccountingError) as failure:
        case.service.approve_department(case.actors["approver"], event["id"], stale_version)
    assert failure.value.status in {403, 409, 422}

    after = _instance(case, event)
    assert after["current_node"] == "finance_review"
    assert len((after["approvals_json"] if isinstance(after["approvals_json"], list) else [])) == 0


def test_approvals_advance_event_and_instance_together(bound):
    case = bound
    _publish(case)
    event = _submitted(case)

    approved = _finish(case, event)
    assert approved["status"] == "finance_approved"
    instance = _instance(case, approved)
    assert instance["status"] == "completed"
    assert instance["current_node"] == "finance_review"
    assert approved["workflow_instance"]["status"] == "completed"

    with case.db.transaction(actor=case.manager) as conn:
        actions = [
            row["action"]
            for row in conn.execute(
                "SELECT action FROM workflow_step_logs WHERE instance_id=? ORDER BY created_at,id",
                (instance["id"],),
            )
        ]
    assert actions == ["start", "approve", "approve"]


def test_return_suspends_and_resubmit_resumes_the_same_instance(bound):
    case = bound
    _publish(case)
    event = _submitted(case)
    instance = _instance(case, event)

    from app.business.models import ReturnRequest

    returned = case.service.return_event(
        case.actors["approver"],
        event["id"],
        ReturnRequest(expected_version=event["version"], reason="缺发票原件，请补件"),
    )
    assert returned["status"] == "returned"
    suspended = _instance(case, returned)
    assert suspended["id"] == instance["id"]
    assert suspended["status"] == "suspended"
    assert "缺发票原件" in suspended["suspended_reason"]

    resubmitted = case.service.submit(
        case.actors["employee"], event["id"], returned["version"]
    )
    resumed = _instance(case, resubmitted)
    assert resumed["id"] == instance["id"]
    assert resumed["status"] == "running"
    assert resumed["current_node"] == instance["current_node"]
    with case.db.transaction(actor=case.manager) as conn:
        actions = [
            row["action"]
            for row in conn.execute(
                "SELECT action FROM workflow_step_logs WHERE instance_id=? ORDER BY created_at,id",
                (instance["id"],),
            )
        ]
    assert actions == ["start", "suspend", "resume"]


def test_existing_events_stay_on_the_version_they_started_with(bound):
    case = bound
    first_definition = _publish(case)
    first_event = _submitted(case)

    second_definition = _publish(
        case,
        changes={
            "routing": {
                **engine.builtin_template("expense_reimbursement")["routing"],
                "auto_approve_below_cents": 0,
                "dual_approval_from_cents": 900_000,
            },
            "note": "把双人审批门槛上调到 9,000 元",
        },
    )
    assert second_definition["version"] == 2

    second_event = _submitted(case, amount="6000.00")
    first_instance = _instance(case, first_event)
    second_instance = _instance(case, second_event)
    assert first_instance["definition_id"] == first_definition["id"]
    assert first_instance["definition_version"] == 1
    assert second_instance["definition_id"] == second_definition["id"]
    assert second_instance["definition_version"] == 2

    # 存量单据继续按 v1 走完，不会因为中途发版而跳到新路径
    approved = _finish(case, first_event)
    assert approved["status"] == "finance_approved"
    assert _instance(case, approved)["status"] == "completed"


def test_company_without_a_definition_keeps_the_legacy_flow(bound):
    case = bound
    event = _submitted(case)
    assert event["workflow_instance"] is None
    assert _instance(case, event) is None

    approved = _finish(case, event)
    assert approved["status"] == "finance_approved"
    assert approved["workflow_instance"] is None


def test_binding_never_writes_definitions_from_the_employee_transaction(bound):
    case = bound
    with case.db.transaction(actor=case.manager) as conn:
        before = conn.execute("SELECT COUNT(*) FROM workflow_definitions").fetchone()[0]
    _submitted(case)
    with case.db.transaction(actor=case.manager) as conn:
        after = conn.execute("SELECT COUNT(*) FROM workflow_definitions").fetchone()[0]
    assert after == before == 0


def test_auto_approved_amount_completes_without_blocking_business_approvals(bound):
    case = bound
    _publish(case)
    event = _submitted(case, amount="0.30")
    instance = _instance(case, event)
    assert instance["status"] == "completed"
    assert instance["current_node"] == "draft"

    approved = _finish(case, event)
    assert approved["status"] == "finance_approved"
    assert _instance(case, approved)["status"] == "completed"


def test_resubmit_with_changed_amount_reroutes_instead_of_reusing_low_tier_path(bound):
    """退回补件时改大金额跨过双人审批门槛，必须作废旧挂起实例并重路由走部门+财务双人路径。"""

    from app.business.models import BusinessEventUpdate, ReturnRequest

    case = bound
    _publish(case)
    event = _submitted(case, amount="3000.00")
    old_instance = _instance(case, event)
    assert old_instance["path_json"] in ('["draft", "finance_review"]', ["draft", "finance_review"])

    returned = case.service.return_event(
        case.actors["approver"],
        event["id"],
        ReturnRequest(expected_version=event["version"], reason="报销金额有漏项，请修改重提"),
    )
    updated = case.service.update(
        case.actors["employee"],
        event["id"],
        BusinessEventUpdate(expected_version=returned["version"], payload=_payload(amount="6000.00")),
    )
    resubmitted = case.service.submit(case.actors["employee"], event["id"], updated["version"])
    new_instance = _instance(case, resubmitted)
    assert new_instance["id"] != old_instance["id"]
    assert new_instance["amount_cents"] == 600_000
    assert new_instance["current_node"] == "manager_review"
    with case.db.transaction(actor=case.manager) as conn:
        old_status = conn.execute(
            "SELECT status FROM workflow_instances WHERE id=?", (old_instance["id"],)
        ).fetchone()["status"]
    assert old_status == "rejected"


def test_dual_approval_rejects_same_person_approving_both_department_and_finance(bound):
    """同一人同时兼任部门审批人与财务审批人时，超额报销不得由一人审完所有节点。"""

    case = bound
    _publish(case)
    # 给财务人员临时兼任该部门审批人，验证工作流硬拦截而非靠前端隐藏。
    case.service.grant_department_approver(
        case.manager, case.company["id"], case.department["id"], case.actors["finance"].user_id
    )
    event = _submitted(case, amount="6000.00")
    step = case.service.approve_department(case.actors["finance"], event["id"], event["version"])
    with pytest.raises(AccountingError) as failure:
        case.service.approve_finance(case.actors["finance"], event["id"], step["version"])
    assert failure.value.status == 403
    assert "两位不同审批人" in failure.value.detail
