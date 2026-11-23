"""Batch 09-1.1：版本化 DAG 引擎、金额分流与实例快照隔离的真实事务验证。

覆盖四类财务流程的合法性、跨企业拒绝、发布/回滚的只追加版本语义、
存量实例保旧版、终态回退零状态变更、以及步骤日志只追加不可改删。
"""

import json
from types import SimpleNamespace

import pytest

from app.accounting.database import Database
from app.accounting.directory import DirectoryService
from app.accounting.domain import Actor, AccountingError
from app.accounting.models import (
    BookCreate,
    BookMemberGrant,
    CompanyCreate,
    FirmCreate,
    FirmMemberGrant,
)
from app.accounting.service import AccountingService
from app.workflow import engine
from app.workflow.models import (
    DefinitionRollback,
    DefinitionWrite,
    InstanceAction,
    InstanceStart,
)
from app.workflow.service import WorkflowService


@pytest.fixture
def workflow(tmp_path):
    db = Database(tmp_path / "workflow.db")
    directory, ledger, accounting = (
        DirectoryService(db),
        AccountingService(db),
        AccountingService(db),
    )
    service = WorkflowService(db, accounting)

    def user(name):
        identity = SimpleNamespace(user_id=name, display_name=name, email=f"{name}@example.test")
        directory.register_identity(identity)
        return identity

    owner = user("owner")
    firm = directory.create_firm(owner.user_id, FirmCreate(name="流程试点事务所"))
    manager = Actor(owner.user_id, firm["id"])
    company = directory.create_company(manager, CompanyCreate(name="甲客户企业"))
    other = directory.create_company(manager, CompanyCreate(name="乙客户企业"))
    book = ledger.create_book(
        manager, BookCreate(name="甲账套", period="2026-10", company_id=company["id"])
    )
    other_book = ledger.create_book(
        manager, BookCreate(name="乙账套", period="2026-10", company_id=other["id"])
    )
    employee = user("employee")
    directory.grant_firm_member(manager, FirmMemberGrant(email=employee.email, role="operator"))
    directory.grant_book_member(
        manager, book["id"], employee.user_id, BookMemberGrant(role="editor")
    )
    directory.grant_book_member(
        manager, other_book["id"], employee.user_id, BookMemberGrant(role="editor")
    )
    reviewers = []
    for name in ("reviewer", "reviewer2"):
        identity = user(name)
        directory.grant_firm_member(manager, FirmMemberGrant(email=identity.email, role="reviewer"))
        directory.grant_book_member(
            manager, book["id"], identity.user_id, BookMemberGrant(role="reviewer")
        )
        reviewers.append(Actor(identity.user_id, firm["id"]))
    outsider = user("outsider")
    other_firm = directory.create_firm(outsider.user_id, FirmCreate(name="外部事务所"))
    return SimpleNamespace(
        db=db,
        directory=directory,
        ledger=ledger,
        service=service,
        manager=manager,
        company=company,
        other=other,
        book=book,
        other_book=other_book,
        employee=Actor(employee.user_id, firm["id"]),
        reviewer=reviewers[0],
        reviewer2=reviewers[1],
        outsider=Actor(outsider.user_id, other_firm["id"]),
    )


def write(workflow, process_kind="expense_reimbursement", *, company=None, publish=True, **changes):
    template = engine.builtin_template(process_kind)
    payload = {
        "company_id": (company or workflow.company)["id"],
        "process_kind": process_kind,
        "nodes": template["nodes"],
        "routing": template["routing"],
        "note": "",
        "publish": publish,
    }
    payload.update(changes)
    return workflow.service.create_version(workflow.manager, DefinitionWrite(**payload))


def start(workflow, definition, amount, *, book=None, actor=None, moment=None):
    return workflow.service.start(
        actor or workflow.employee,
        InstanceStart(
            company_id=workflow.company["id"],
            book_id=(book or workflow.book)["id"],
            subject_type="voucher",
            subject_id="v-1",
            amount_cents=amount,
            process_kind=definition["process_kind"],
        ),
        moment=moment,
    )


def act(workflow, instance, action, *, actor, reason="", moment=None):
    return workflow.service.act(
        actor,
        instance["id"],
        InstanceAction(action=action, reason=reason),
        moment=moment,
    )


def logs(workflow, instance_id):
    with workflow.db.transaction(actor=workflow.manager) as conn:
        rows = conn.execute(
            "SELECT action,from_node,to_node,reason FROM workflow_step_logs "
            "WHERE instance_id=? ORDER BY created_at,id",
            (instance_id,),
        ).fetchall()
    return [dict(row) for row in rows]


# --------------------------------------------------------------------- 规则层
def test_all_four_templates_are_valid_and_fingerprinted():
    fingerprints = set()
    for process_kind in engine.PROCESS_KINDS:
        template = engine.builtin_template(process_kind)
        nodes, routing = engine.validate_definition(
            process_kind, template["nodes"], template["routing"]
        )
        assert sum(1 for node in nodes if node["start"]) == 1
        assert any(node["terminal"] for node in nodes)
        digest = engine.content_fingerprint(process_kind, nodes, routing)
        assert len(digest) == 64
        # 同内容同指纹：规范化 JSON 保证可复算
        assert digest == engine.content_fingerprint(process_kind, nodes, routing)
        fingerprints.add(digest)
    assert len(fingerprints) == 4


def test_dag_rejects_cycles_floats_and_dangling_edges():
    template = engine.builtin_template("expense_reimbursement")
    nodes = template["nodes"]

    cyclic = [dict(node) for node in nodes]
    cyclic[0]["edges"] = [{"to": "manager_review"}]
    cyclic[1]["edges"] = [
        {"to": "finance_review", "condition": {"amount_cents_lt": 100_000}},
        {"to": "draft"},
    ]
    with pytest.raises(AccountingError) as failure:
        engine.validate_definition("expense_reimbursement", cyclic, template["routing"])
    assert "环" in str(failure.value)

    with pytest.raises(AccountingError):
        engine.validate_definition(
            "expense_reimbursement",
            [
                dict(node, edges=[{"to": "missing"}]) if node["id"] == "draft" else node
                for node in nodes
            ],
            template["routing"],
        )

    float_condition = [dict(node) for node in nodes]
    float_condition[0]["edges"] = [
        {"to": "finance_review", "condition": {"amount_cents_lt": 100.5}},
        {"to": "manager_review"},
    ]
    with pytest.raises(AccountingError) as failure:
        engine.validate_definition("expense_reimbursement", float_condition, template["routing"])
    assert "整数分" in str(failure.value)

    boolean_threshold = dict(template["routing"], auto_approve_below_cents=True)
    with pytest.raises(AccountingError):
        engine.validate_definition("expense_reimbursement", nodes, boolean_threshold)

    two_starts = [dict(node, start=True) for node in nodes]
    with pytest.raises(AccountingError) as failure:
        engine.validate_definition("expense_reimbursement", two_starts, template["routing"])
    assert "起点" in str(failure.value)


def test_dag_rejects_unreachable_node_and_dangling_terminal():
    template = engine.builtin_template("fee_variance")
    nodes = [dict(node) for node in template["nodes"]]
    orphan = {
        "id": "orphan",
        "label": "孤立节点",
        "role": "reviewer",
        "terminal": True,
        "edges": [],
    }
    with pytest.raises(AccountingError) as failure:
        engine.validate_definition("fee_variance", nodes + [orphan], template["routing"])
    assert "不可达" in str(failure.value)

    dangling = [dict(node) for node in nodes]
    dangling[-1]["terminal"] = False
    with pytest.raises(AccountingError):
        engine.validate_definition("fee_variance", dangling, template["routing"])


def test_routing_thresholds_cannot_invert_or_exceed_bounds():
    template = engine.builtin_template("expense_reimbursement")
    with pytest.raises(AccountingError) as failure:
        engine.validate_definition(
            "expense_reimbursement",
            template["nodes"],
            {
                "auto_approve_below_cents": 900_000,
                "dual_approval_from_cents": 500_000,
                "escalate_after_hours": 24,
            },
        )
    assert "直通门槛" in str(failure.value)
    with pytest.raises(AccountingError):
        engine.validate_definition(
            "expense_reimbursement",
            template["nodes"],
            {
                "auto_approve_below_cents": 0,
                "dual_approval_from_cents": 500_000,
                "escalate_after_hours": 0,
            },
        )
    with pytest.raises(AccountingError) as failure:
        engine.validate_definition(
            "expense_reimbursement",
            template["nodes"],
            {"auto_approve_below_cents": 0, "unknown_threshold": 1},
        )
    assert "未知" in str(failure.value)


def test_route_branches_by_amount_and_dual_approval_counts_nodes():
    template = engine.builtin_template("expense_reimbursement")
    nodes, routing = engine.validate_definition(
        "expense_reimbursement", template["nodes"], template["routing"]
    )
    small = engine.route_path(nodes, 10_000)
    large = engine.route_path(nodes, 900_000)
    assert small == ["draft", "finance_review"]
    assert large == ["draft", "manager_review", "finance_review"]
    assert len(engine.approval_node_ids(nodes, small)) == 1
    assert len(engine.approval_node_ids(nodes, large)) == 2
    assert engine.auto_approved(routing, 40_000) is True
    assert engine.auto_approved(routing, 60_000) is False
    assert engine.dual_required(routing, 500_000) is True
    assert engine.dual_required(routing, 499_999) is False
    # 兜底边必须排最后，因此任何金额都能落到唯一确定路径
    assert engine.route_path(nodes, 0) == ["draft", "finance_review"]


# ------------------------------------------------------------------ 版本管理
def test_publish_archives_previous_and_keeps_one_active(workflow):
    first = write(workflow, note="初始版本")
    assert first["status"] == "active" and first["version"] == 1
    second = write(
        workflow,
        note="调低直通门槛",
        routing={
            "auto_approve_below_cents": 10_000,
            "dual_approval_from_cents": 500_000,
            "escalate_after_hours": 24,
        },
    )
    assert second["version"] == 2 and second["status"] == "active"
    detail = workflow.service.definition(workflow.manager, first["id"])
    assert detail["status"] == "archived"
    # diff_from_active 的语义是「若本版本生效，相对当前生效版本会改什么」。
    assert detail["diff_from_active"]["routing"]["auto_approve_below_cents"] == {
        "before": 10_000,
        "after": 50_000,
    }
    with workflow.db.transaction(actor=workflow.manager) as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM workflow_definitions WHERE status='active'"
        ).fetchone()
    assert rows[0] == 1
    with pytest.raises(AccountingError) as failure:
        workflow.service.publish(workflow.manager, second["id"])
    assert "已经是生效版本" in str(failure.value)


def test_published_version_is_frozen_in_both_directions(workflow):
    first = write(workflow, publish=True)
    with pytest.raises(Exception) as failure:
        with workflow.db.transaction(
            write=True,
            actor=workflow.manager,
            company_id=workflow.company["id"],
            write_scope="company",
        ) as conn:
            conn.execute(
                "UPDATE workflow_definitions SET nodes_json='[]' WHERE id=?", (first["id"],)
            )
    assert "immutable" in str(failure.value)
    with pytest.raises(Exception) as failure:
        with workflow.db.transaction(
            write=True,
            actor=workflow.manager,
            company_id=workflow.company["id"],
            write_scope="company",
        ) as conn:
            conn.execute("DELETE FROM workflow_definitions WHERE id=?", (first["id"],))
    assert "cannot be deleted" in str(failure.value)


def test_rollback_creates_new_version_without_mutating_history(workflow):
    v1 = write(workflow, note="v1")
    v2 = write(
        workflow,
        note="v2",
        routing={
            "auto_approve_below_cents": 10_000,
            "dual_approval_from_cents": 300_000,
            "escalate_after_hours": 8,
        },
    )
    rolled = workflow.service.rollback(workflow.manager, v1["id"])
    assert rolled["version"] == 3
    assert rolled["status"] == "active"
    assert rolled["rollback_from_version"] == 1
    assert rolled["content_fingerprint"] == v1["content_fingerprint"]
    assert rolled["routing"]["auto_approve_below_cents"] == 50_000
    for original in (v1, v2):
        with workflow.db.transaction(actor=workflow.manager) as conn:
            row = conn.execute(
                "SELECT status,content_fingerprint,nodes_json FROM workflow_definitions WHERE id=?",
                (original["id"],),
            ).fetchone()
        assert row["status"] == "archived"
        assert row["content_fingerprint"] == original["content_fingerprint"]
    with pytest.raises(AccountingError) as failure:
        workflow.service.rollback(workflow.manager, rolled["id"])
    assert "无需回滚" in str(failure.value)


def test_rollback_refuses_history_whose_fingerprint_does_not_match(workflow):
    template = engine.builtin_template("expense_reimbursement")
    with workflow.db.transaction(
        write=True,
        actor=workflow.manager,
        company_id=workflow.company["id"],
        write_scope="company",
    ) as conn:
        conn.execute(
            "INSERT INTO workflow_definitions(id,firm_id,company_id,process_kind,version,status,"
            "nodes_json,routing_json,content_fingerprint,note,created_by,created_at) "
            "VALUES('tampered',?,?,?,9,'archived',?,?,?,?,?,?)",
            (
                workflow.manager.firm_id,
                workflow.company["id"],
                "expense_reimbursement",
                json.dumps(template["nodes"], ensure_ascii=False),
                json.dumps(template["routing"], ensure_ascii=False),
                "0" * 64,
                "被篡改的历史版本",
                workflow.manager.user_id,
                "2026-10-01T00:00:00+00:00",
            ),
        )
    with pytest.raises(AccountingError) as failure:
        workflow.service.rollback(workflow.manager, "tampered")
    assert "指纹不一致" in str(failure.value)


def test_definition_writes_require_company_manager(workflow):
    with pytest.raises(AccountingError) as failure:
        workflow.service.create_version(
            workflow.employee,
            DefinitionWrite(
                company_id=workflow.company["id"],
                process_kind="expense_reimbursement",
                nodes=engine.builtin_template("expense_reimbursement")["nodes"],
                routing=engine.builtin_template("expense_reimbursement")["routing"],
            ),
        )
    assert "权限" in str(failure.value)


# ------------------------------------------------------------------- 实例路径
def test_instance_locks_snapshot_and_keeps_running_on_old_version(workflow):
    v1 = write(workflow)
    instance = start(workflow, v1, 600_000)
    assert instance["status"] == "running"
    assert instance["definition_version"] == 1
    assert instance["path"] == ["draft", "manager_review", "finance_review"]
    v2 = write(
        workflow,
        routing={
            "auto_approve_below_cents": 0,
            "dual_approval_from_cents": 100_000,
            "escalate_after_hours": 24,
        },
    )
    assert v2["version"] == 2
    moved = act(workflow, instance, "approve", actor=workflow.reviewer)
    assert moved["definition_version"] == 1
    assert moved["current_node"] == "finance_review"
    assert moved["status"] == "running"
    listed = workflow.service.instances(workflow.manager, workflow.company["id"])
    assert listed["items"][0]["definition_version"] == 1
    fresh = start(workflow, v2, 600_000)
    assert fresh["definition_version"] == 2
    assert fresh["path"] == ["draft", "manager_review", "finance_review"]
    # 存量实例按旧版定义的可回退性继续运行，不受发版影响。
    rolled = act(workflow, moved, "rollback", actor=workflow.manager, reason="旧版要求重审")
    assert rolled["current_node"] == "manager_review"
    assert rolled["definition_version"] == 1


def test_approver_cannot_be_the_creator(workflow):
    definition = write(workflow)
    instance = start(workflow, definition, 600_000, actor=workflow.manager)
    with pytest.raises(AccountingError) as failure:
        act(workflow, instance, "approve", actor=workflow.manager)
    assert failure.value.status == 403
    assert "自己发起" in str(failure.value)
    with workflow.db.transaction(actor=workflow.manager) as conn:
        row = conn.execute(
            "SELECT status,current_node FROM workflow_instances WHERE id=?", (instance["id"],)
        ).fetchone()
    assert row["status"] == "running" and row["current_node"] == "manager_review"


def test_dual_approval_completes_and_then_terminal_blocks_rollback(workflow):
    definition = write(workflow)
    instance = start(workflow, definition, 600_000)
    first = act(workflow, instance, "approve", actor=workflow.reviewer)
    assert first["status"] == "running" and first["current_node"] == "finance_review"
    done = act(workflow, first, "approve", actor=workflow.reviewer2)
    assert done["status"] == "completed"
    assert done["irreversible_reached"] is True
    assert len(done["approvals"]["approvals"]) == 2
    trail = logs(workflow, instance["id"])
    assert [item["action"] for item in trail] == ["start", "approve", "approve"]
    with pytest.raises(AccountingError) as failure:
        act(workflow, done, "rollback", actor=workflow.manager, reason="过账后想回退")
    assert failure.value.status == 409
    assert "冲销" in str(failure.value)
    with workflow.db.transaction(actor=workflow.manager) as conn:
        row = conn.execute(
            "SELECT status,current_node FROM workflow_instances WHERE id=?", (instance["id"],)
        ).fetchone()
    assert row["status"] == "completed"
    assert len(logs(workflow, instance["id"])) == 3  # 回退被拒时零状态变更


def test_external_lock_blocks_rollback_after_bank_submission(workflow):
    definition = write(workflow, process_kind="payment_approval")
    instance = start(workflow, definition, 20_000_000)
    assert instance["path"] == ["draft", "treasury_review", "cashier_review", "treasury_release"]
    step = act(workflow, instance, "approve", actor=workflow.reviewer)
    step = act(workflow, step, "approve", actor=workflow.reviewer2)
    assert step["current_node"] == "treasury_release"
    done = act(workflow, step, "approve", actor=workflow.reviewer)
    assert done["status"] == "completed"
    assert done["terminal_external_locked"] is True
    with pytest.raises(AccountingError) as failure:
        act(workflow, done, "rollback", actor=workflow.manager, reason="已提交银行还想退回")
    assert failure.value.status == 409
    assert "冲销" in str(failure.value)


def test_safe_rollback_walks_back_one_approval_at_a_time(workflow):
    definition = write(workflow)
    instance = start(workflow, definition, 600_000)
    instance = act(workflow, instance, "approve", actor=workflow.reviewer)
    assert instance["current_node"] == "finance_review"
    back = act(workflow, instance, "rollback", actor=workflow.manager, reason="主管要求重审")
    assert back["current_node"] == "manager_review"
    assert back["status"] == "running"
    assert back["approvals"]["approvals"] == []
    back2 = act(workflow, back, "rollback", actor=workflow.manager, reason="退回发起人补件")
    assert back2["current_node"] == "draft"
    with pytest.raises(AccountingError) as failure:
        act(workflow, back2, "rollback", actor=workflow.manager, reason="再退")
    assert "起点" in str(failure.value)
    # 退回发起人后由发起人补正重新提交，实例号不变、不新建流程。
    resubmitted = act(workflow, back2, "start", actor=workflow.employee, reason="已补齐附件")
    assert resubmitted["id"] == instance["id"]
    assert resubmitted["current_node"] == "manager_review"
    assert [item["action"] for item in logs(workflow, instance["id"])] == [
        "start",
        "approve",
        "rollback",
        "rollback",
        "start",
    ]


def test_suspend_resume_and_deterministic_timeout_escalation(workflow):
    definition = write(workflow)
    moment = "2026-10-01T00:00:00+00:00"
    instance = start(workflow, definition, 600_000, moment=moment)
    suspended = act(
        workflow,
        instance,
        "suspend",
        actor=workflow.reviewer,
        reason="等客户补发票",
        moment="2026-10-01T01:00:00+00:00",
    )
    assert suspended["status"] == "suspended"
    assert suspended["suspended_reason"] == "等客户补发票"
    resumed = act(
        workflow,
        suspended,
        "resume",
        actor=workflow.reviewer,
        reason="发票已到",
        moment="2026-10-01T02:00:00+00:00",
    )
    assert resumed["status"] == "running"
    with pytest.raises(AccountingError) as failure:
        act(
            workflow,
            resumed,
            "escalate_timeout",
            actor=workflow.manager,
            moment="2026-10-01T12:00:00+00:00",
        )
    assert "升级时限" in str(failure.value)
    escalated = act(
        workflow,
        resumed,
        "escalate_timeout",
        actor=workflow.manager,
        moment="2026-10-02T03:00:00+00:00",
    )
    assert escalated["status"] == "escalated"
    assert logs(workflow, instance["id"])[-1]["action"] == "escalate_timeout"


def test_sweep_only_escalates_overdue_instances(workflow):
    definition = write(workflow)
    stale = start(workflow, definition, 600_000, moment="2026-10-01T00:00:00+00:00")
    fresh = start(workflow, definition, 600_000, moment="2026-10-21T00:00:00+00:00")
    sweep = workflow.service.sweep_escalations(
        workflow.manager, workflow.company["id"], "2026-10-21T12:00:00+00:00"
    )
    assert sweep["escalated"] == [stale["id"]]
    with workflow.db.transaction(actor=workflow.manager) as conn:
        rows = {
            row["id"]: row["status"]
            for row in conn.execute(
                "SELECT id,status FROM workflow_instances WHERE id IN (?,?)",
                (stale["id"], fresh["id"]),
            ).fetchall()
        }
    assert rows[stale["id"]] == "escalated"
    assert rows[fresh["id"]] == "running"


def test_auto_approved_low_risk_lands_as_draft_without_approvals(workflow):
    definition = write(workflow)
    instance = start(workflow, definition, 1_000)
    assert instance["status"] == "completed"
    assert instance["current_node"] == "draft"
    assert instance["approvals"]["auto_approved"] is True
    assert instance["approvals"]["outcome"] == "draft"
    assert instance["approvals"]["approvals"] == []
    assert [item["action"] for item in logs(workflow, instance["id"])] == ["auto_approve"]


def test_start_requires_an_active_version(workflow):
    draft = write(workflow, publish=False)
    with pytest.raises(AccountingError) as failure:
        start(workflow, draft, 10_000)
    assert failure.value.status == 409
    assert "已生效" in str(failure.value)
    with pytest.raises(AccountingError) as failure:
        workflow.service.start(
            workflow.employee,
            InstanceStart(
                company_id=workflow.company["id"],
                book_id=workflow.book["id"],
                subject_type="voucher",
                subject_id="v-2",
                amount_cents=10_000,
                process_kind="monthly_close",
            ),
        )
    assert "已生效" in str(failure.value)


def test_dual_threshold_without_two_approval_nodes_is_rejected_at_start(workflow):
    definition = write(
        workflow,
        routing={
            "auto_approve_below_cents": 0,
            "dual_approval_from_cents": 0,
            "escalate_after_hours": 24,
        },
    )
    with pytest.raises(AccountingError) as failure:
        start(workflow, definition, 10_000)
    assert "双人审批" in str(failure.value)


def test_instance_snapshot_columns_are_locked(workflow):
    definition = write(workflow)
    instance = start(workflow, definition, 600_000)
    with pytest.raises(Exception) as failure:
        with workflow.db.transaction(
            write=True,
            actor=workflow.manager,
            company_id=workflow.company["id"],
            write_scope="company",
        ) as conn:
            conn.execute(
                "UPDATE workflow_instances SET amount_cents=1 WHERE id=?", (instance["id"],)
            )
    assert "snapshot is locked" in str(failure.value)


def test_step_logs_are_append_only(workflow):
    definition = write(workflow)
    instance = start(workflow, definition, 600_000)
    with workflow.db.transaction(actor=workflow.manager) as conn:
        row = conn.execute(
            "SELECT id FROM workflow_step_logs WHERE instance_id=?", (instance["id"],)
        ).fetchone()
    with pytest.raises(Exception) as failure:
        with workflow.db.transaction(
            write=True,
            actor=workflow.manager,
            company_id=workflow.company["id"],
            write_scope="company",
        ) as conn:
            conn.execute("UPDATE workflow_step_logs SET reason='x' WHERE id=?", (row["id"],))
    assert "append-only" in str(failure.value)
    with pytest.raises(Exception) as failure:
        with workflow.db.transaction(
            write=True,
            actor=workflow.manager,
            company_id=workflow.company["id"],
            write_scope="company",
        ) as conn:
            conn.execute("DELETE FROM workflow_step_logs WHERE id=?", (row["id"],))
    assert "append-only" in str(failure.value)


def test_book_and_company_must_match_on_start(workflow):
    definition = write(workflow)
    with pytest.raises(AccountingError) as failure:
        workflow.service.start(
            workflow.employee,
            InstanceStart(
                company_id=workflow.company["id"],
                book_id=workflow.other_book["id"],
                subject_type="voucher",
                subject_id="v-3",
                amount_cents=600_000,
                process_kind=definition["process_kind"],
            ),
        )
    assert "不属于所选企业" in str(failure.value)


def test_another_firm_cannot_see_definitions_or_instances(workflow):
    definition = write(workflow)
    start(workflow, definition, 600_000)
    with pytest.raises(AccountingError) as failure:
        workflow.service.overview(workflow.outsider, workflow.company["id"])
    assert failure.value.status == 404
    with pytest.raises(AccountingError) as failure:
        workflow.service.instances(workflow.outsider, workflow.company["id"])
    assert failure.value.status == 404
    with pytest.raises(AccountingError) as failure:
        workflow.service.definition(workflow.outsider, definition["id"])
    assert failure.value.status == 404


def test_overview_separates_companies(workflow):
    write(workflow)
    overview = workflow.service.overview(workflow.manager, workflow.other["id"])
    assert overview["company_name"] == "乙客户企业"
    assert overview["can_manage"] is True
    assert all(process["versions"] == [] for process in overview["processes"])
    assert len(overview["processes"]) == 4
    assert {process["process_kind"] for process in overview["processes"]} == set(
        engine.PROCESS_KINDS
    )
    first = workflow.service.overview(workflow.manager, workflow.company["id"])
    active = [process["active"] for process in first["processes"] if process["active"]]
    assert len(active) == 1
    assert first["routing_rule_version"] == "b09-workflow-v1"


def test_diff_reports_added_nodes_and_routing(workflow):
    v1 = write(workflow)
    nodes = engine.builtin_template("expense_reimbursement")["nodes"]
    extra = {
        "id": "cfo_review",
        "label": "财务负责人复核",
        "role": "manager",
        "approval": True,
        "terminal": True,
        "edges": [],
    }
    expanded = [dict(node) for node in nodes]
    expanded[1]["edges"] = [{"to": "cfo_review"}]
    expanded[2]["terminal"] = False
    expanded[2]["edges"] = [{"to": "cfo_review"}]
    v2 = write(
        workflow,
        nodes=expanded + [extra],
        routing={
            "auto_approve_below_cents": 0,
            "dual_approval_from_cents": 900_000,
            "escalate_after_hours": 48,
        },
    )
    diff = workflow.service.diff(workflow.manager, v1["id"], v2["id"])
    assert diff["added_nodes"] == ["cfo_review"]
    assert diff["identical"] is False
    assert diff["routing"]["escalate_after_hours"] == {"before": 24, "after": 48}
    assert diff["from"]["version"] == 1 and diff["to"]["version"] == 2
    same = workflow.service.diff(workflow.manager, v2["id"], v2["id"])
    assert same["identical"] is True


def test_reject_requires_reason_and_closes_instance(workflow):
    definition = write(workflow)
    instance = start(workflow, definition, 600_000)
    with pytest.raises(AccountingError) as failure:
        act(workflow, instance, "reject", actor=workflow.reviewer)
    assert "必须填写原因" in str(failure.value)
    rejected = act(workflow, instance, "reject", actor=workflow.reviewer, reason="票据不合规")
    assert rejected["status"] == "rejected"
    with pytest.raises(AccountingError) as failure:
        act(workflow, rejected, "rollback", actor=workflow.manager, reason="已终态")
    assert "不能回退" in str(failure.value)


def test_rollback_payload_accepts_note(workflow):
    v1 = write(workflow)
    write(
        workflow,
        routing={
            "auto_approve_below_cents": 0,
            "dual_approval_from_cents": 500_000,
            "escalate_after_hours": 24,
        },
    )
    rolled = workflow.service.rollback(workflow.manager, v1["id"], "验收回滚")
    assert rolled["note"] == "验收回滚"
    assert DefinitionRollback(note="验收回滚").note == "验收回滚"
