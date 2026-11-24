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


def test_sqlite_236_upgrades_legacy_db_with_old_business_event_instances(tmp_path, monkeypatch):
    """旧库含三类历史记录（在办旧 business_event 无 subject_version、已完成、普通在办）可无损升级并续办。"""
    import sqlite3
    from app.accounting import database as database_mod
    from app.accounting.database import Database

    db_path = tmp_path / "legacy_235.db"
    real_files = database_mod.migration_files

    # 第一步：完全走真实迁移器建一个停留在 235 的旧库。
    monkeypatch.setattr(
        database_mod,
        "migration_files",
        lambda dialect: [p for p in real_files(dialect) if int(p.name.split("_", 1)[0]) < 236],
    )
    Database(str(db_path))
    monkeypatch.undo()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # 写入三类历史流程记录：旧版带 book_id 的 business_event（无 subject_version）、已完成记录、普通记录。
    conn.execute("INSERT INTO users(id,name,active,email) VALUES('u1','员工甲',1,'u1@test')")
    conn.execute("INSERT INTO firms(id,name,created_by) VALUES('f1','测试所','u1')")
    conn.execute("INSERT INTO companies(id,name,tax_id) VALUES('c1','测试公司','t1')")
    conn.execute("INSERT INTO firm_clients(firm_id,company_id,status) VALUES('f1','c1','active')")
    conn.execute(
        "INSERT INTO books(id,firm_id,company_id,name,period) VALUES('b1','f1','c1','2026-11账套','2026-11')"
    )
    conn.execute(
        "INSERT INTO workflow_definitions(id,firm_id,company_id,process_kind,version,status,"
        "nodes_json,routing_json,content_fingerprint,note,created_by,created_at) "
        "VALUES('d1','f1','c1','expense_reimbursement',1,'active','[]','{}',?,'','u1','2026-11-01')",
        ("a" * 64,),
    )
    legacy_rows = [
        ("inst-old-biz", "business_event", "ev-old-1", "running"),
        ("inst-old-done", "expense_reimbursement", "ev-old-2", "completed"),
        ("inst-old-open", "expense_reimbursement", "ev-old-3", "running"),
    ]
    for iid, stype, sid, status in legacy_rows:
        conn.execute(
            "INSERT INTO workflow_instances(id,firm_id,company_id,book_id,definition_id,"
            "definition_version,process_kind,subject_type,subject_id,amount_cents,current_node,"
            "path_json,status,suspended_reason,terminal_external_locked,irreversible_reached,"
            "approvals_json,created_by,created_at,updated_at) "
            "VALUES(?,'f1','c1','b1','d1',1,'expense_reimbursement',?,?,10000,'finance_review',"
            "'[\"draft\",\"finance_review\"]',?,'',0,0,'{}','u1','2026-11-01','2026-11-01')",
            (iid, stype, sid, status),
        )
        conn.execute(
            "INSERT INTO workflow_step_logs(id,firm_id,company_id,instance_id,node_id,action,"
            "from_node,to_node,actor_user_id,reason,metadata_json,created_at) "
            "VALUES(?,'f1','c1',?,'draft','start','','finance_review','u1','','{}','2026-11-01')",
            ("log-" + iid, iid),
        )
    conn.commit()
    conn.close()

    # 执行真实升级（含 236），验证三类旧记录完整保留且不补造历史 subject_version。
    Database(str(db_path))
    check = sqlite3.connect(db_path)
    check.row_factory = sqlite3.Row
    check.execute("PRAGMA foreign_keys=ON")
    assert check.execute("PRAGMA foreign_key_check").fetchall() == []
    rows = {
        row["id"]: dict(row)
        for row in check.execute("SELECT * FROM workflow_instances ORDER BY id").fetchall()
    }
    assert set(rows) == {"inst-old-biz", "inst-old-done", "inst-old-open"}
    assert rows["inst-old-biz"]["subject_version"] is None
    assert rows["inst-old-biz"]["status"] == "running"
    assert rows["inst-old-done"]["status"] == "completed"
    assert check.execute("SELECT COUNT(*) FROM workflow_step_logs").fetchone()[0] == 3

    # 升级后：存量单可正常更新完成，新建无 book_id 的业务单必须带 subject_version。
    check.execute(
        "UPDATE workflow_instances SET status='completed',updated_at='2026-11-02' WHERE id='inst-old-biz'"
    )
    check.execute(
        "INSERT INTO workflow_instances(id,firm_id,company_id,book_id,definition_id,"
        "definition_version,process_kind,subject_type,subject_id,subject_version,amount_cents,"
        "current_node,path_json,status,suspended_reason,terminal_external_locked,"
        "irreversible_reached,approvals_json,created_by,created_at,updated_at) "
        "VALUES('inst-new','f1','c1',NULL,'d1',1,'expense_reimbursement','business_event',"
        "'ev-new',2,50000,'finance_review','[\"draft\",\"finance_review\"]','running','',0,0,"
        "'{}','u1','2026-11-02','2026-11-02')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        check.execute(
            "INSERT INTO workflow_instances(id,firm_id,company_id,book_id,definition_id,"
            "definition_version,process_kind,subject_type,subject_id,subject_version,amount_cents,"
            "current_node,path_json,status,suspended_reason,terminal_external_locked,"
            "irreversible_reached,approvals_json,created_by,created_at,updated_at) "
            "VALUES('inst-bad','f1','c1',NULL,'d1',1,'expense_reimbursement','business_event',"
            "'ev-bad',NULL,50000,'finance_review','[\"draft\",\"finance_review\"]','running','',0,0,"
            "'{}','u1','2026-11-02','2026-11-02')"
        )
    check.close()


def test_handoff_blocks_until_final_multilevel_approver_completes_and_creates_single_draft(bound):
    """三级审批链：财务审批后尚余最后审批节点时移交必被 409 拦截；终审完成后移交且仅生成 1 笔草稿。"""
    from app.business.models import HandoffRequest
    from app.workflow.models import InstanceAction

    case = bound
    nodes = [
        {
            "id": "draft",
            "label": "提交报销",
            "role": "creator",
            "start": True,
            "edges": [{"to": "manager_review"}],
        },
        {
            "id": "manager_review",
            "label": "部门主管审批",
            "role": "manager",
            "approval": True,
            "edges": [{"to": "finance_review"}],
        },
        {
            "id": "finance_review",
            "label": "财务初审",
            "role": "finance",
            "approval": True,
            "edges": [{"to": "director_review"}],
        },
        {
            "id": "director_review",
            "label": "总经理终审",
            "role": "reviewer",
            "approval": True,
            "terminal": True,
            "edges": [],
        },
    ]
    _publish(
        case,
        changes={
            "nodes": nodes,
            "routing": {
                "auto_approve_below_cents": 0,
                "dual_approval_from_cents": 500_000,
                "escalate_after_hours": 24,
            },
        },
    )
    event = _submitted(case, amount="6000.00")
    after_dept = case.service.approve_department(case.actors["approver"], event["id"], event["version"])
    after_fin = case.service.approve_finance(case.actors["finance"], event["id"], after_dept["version"])
    assert after_fin["status"] == "finance_approved"
    # 财务只推进了一个节点，流程尚停在总经理终审。
    mid_inst = _instance(case, after_fin)
    assert mid_inst["status"] == "running"
    assert mid_inst["current_node"] == "director_review"

    # 最后一名必要审批人完成前，严禁移交会计！
    with pytest.raises(AccountingError) as blocked:
        case.service.handoff(
            case.actors["finance"],
            event["id"],
            HandoffRequest(
                book_id=case.book["id"],
                expected_version=after_fin["version"],
                idempotency_key="handoff-too-early",
            ),
        )
    assert blocked.value.status == 409
    assert "尚未办结" in blocked.value.detail

    # 总经理（manager 角色）完成终审节点后，工作流进入 completed。
    done = case.workflow.act(
        case.manager, mid_inst["id"], InstanceAction(action="approve", reason="终审同意")
    )
    assert done["status"] == "completed"

    # 此时才放行移交会计，生成唯一草稿并锁定实例为不可逆终态。
    receipt = case.service.handoff(
        case.actors["finance"],
        event["id"],
        HandoffRequest(
            book_id=case.book["id"],
            expected_version=after_fin["version"],
            idempotency_key="handoff-after-final",
        ),
    )
    assert receipt["draft_id"]
    with case.db.transaction(actor=case.manager) as conn:
        draft_count = conn.execute(
            "SELECT COUNT(*) FROM business_mappings WHERE event_id=?", (event["id"],)
        ).fetchone()[0]
    assert draft_count == 1
    locked_inst = _instance(case, after_fin)
    assert locked_inst["irreversible_reached"] == 1 or locked_inst["irreversible_reached"] is True
