"""费用报销业务单据与声明式工作流引擎的同事务绑定。

绑定范围只覆盖 ``template_id == "reimbursement"`` 的费用报销单，且必须由调用方
（``BusinessService``）在同一个写事务里调用，保证「单据状态」与「流程实例」要么一起前进、
要么一起回滚，不会出现一边已审批、另一边还停在旧节点的分裂状态。

几条硬约束：

* 企业没有已生效的 ``expense_reimbursement`` 定义时不绑定，报销单继续走既有默认审批流；
  本次迁移不写入任何 ``workflow_definitions`` 默认数据，避免员工提交动作变成隐式的管理动作。
* 实例在提交那一刻锁定 ``definition_id``/``definition_version``/``path_json``；之后流程中台
  发布新版本也不影响存量单据，存量单据按锁定的旧版本走完。
* 部门审批只推进 ``role == "manager"`` 的审批节点；若锁定的路径里没有部门节点（低金额直通
  财务复核的模板就是这种），部门审批只记录业务动作、不动实例，等财务审批再推进。
* 财务审批推进其后所有剩余节点直到终态；审批人不得是实例发起人，重复审批由节点状态拦截。
* 退回补件把实例挂起并记录原因，重新提交后恢复；实例已过账/已报外部不可再推进。
"""

from . import engine

BUSINESS_SUBJECT = "business_event"
PROCESS_KIND = "expense_reimbursement"
INSTANCE_COLUMNS = (
    "id,firm_id,company_id,book_id,definition_id,definition_version,process_kind,subject_type,"
    "subject_id,subject_version,amount_cents,current_node,path_json,status,suspended_reason,"
    "terminal_external_locked,irreversible_reached,approvals_json,created_by,created_at,updated_at"
)
ACTIVE_STATUSES = ("running", "suspended")


def _json(value):
    """PG 的 JSONB 列可能已被驱动解码成 dict/list，SQLite 一律是文本。"""

    import json

    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def active_definition(conn, firm_id, company_id):
    """取当前生效的报销流程定义；没有就返回 None（调用方保持既有默认审批流）。"""

    return conn.execute(
        "SELECT id,firm_id,company_id,process_kind,version,status,nodes_json,routing_json "
        "FROM workflow_definitions WHERE firm_id=? AND company_id=? AND process_kind=? "
        "AND status='active'",
        (firm_id, company_id, PROCESS_KIND),
    ).fetchone()


def instance_for(conn, firm_id, subject_id):
    row = conn.execute(
        f"SELECT {INSTANCE_COLUMNS} FROM workflow_instances "
        "WHERE firm_id=? AND subject_type=? AND subject_id=? "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (firm_id, BUSINESS_SUBJECT, subject_id),
    ).fetchone()
    return row


def summary(conn, firm_id, subject_id):
    """给报销单详情附带的只读摘要（不含金额以外任何可写字段）。"""

    row = instance_for(conn, firm_id, subject_id)
    if not row:
        return None
    return {
        "id": row["id"],
        "definition_id": row["definition_id"],
        "definition_version": int(row["definition_version"]),
        "status": row["status"],
        "current_node": row["current_node"],
        "path": _json(row["path_json"]) or [],
        "subject_version": row["subject_version"],
        "suspended_reason": row["suspended_reason"] or "",
        "terminal_external_locked": bool(row["terminal_external_locked"]),
        "irreversible_reached": bool(row["irreversible_reached"]),
    }


def _log(
    conn, actor, company_id, instance_id, node_id, action, from_node, to_node, metadata, stamp
):
    import json

    conn.execute(
        "INSERT INTO workflow_step_logs(id,firm_id,company_id,instance_id,node_id,action,"
        "from_node,to_node,actor_user_id,reason,metadata_json,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            _identifier(),
            actor.firm_id,
            company_id,
            instance_id,
            node_id,
            action,
            from_node,
            to_node,
            actor.user_id,
            str(metadata.get("reason") or ""),
            json.dumps(metadata, ensure_ascii=False),
            stamp,
        ),
    )


def _identifier():
    from uuid import uuid4

    return uuid4().hex


def bind(conn, actor, event, definition, amount_cents, stamp, subject_version=None):
    """按锁定的生效版本创建实例（与提交动作同一事务）。

    返回新建实例的行；重复提交同一 ``submitted_version`` 时返回已存在的那一条（幂等），
    由数据库唯一索引兜底并发。``subject_version`` 取本次提交产生的新版本号，而不是
    读取提交前的行值（``_bump`` 之后内存里的 ``event`` 仍是旧快照）。
    """

    import json
    from uuid import uuid4

    version = int(subject_version if subject_version is not None else event["submitted_version"])
    existing = conn.execute(
        f"SELECT {INSTANCE_COLUMNS} FROM workflow_instances WHERE firm_id=? AND company_id=? "
        "AND subject_type=? AND subject_id=? AND subject_version=?",
        (actor.firm_id, event["company_id"], BUSINESS_SUBJECT, event["id"], version),
    ).fetchone()
    if existing:
        return existing
    nodes = _json(definition["nodes_json"])
    routing = _json(definition["routing_json"])
    path = engine.route_path(nodes, amount_cents)
    if (
        engine.dual_required(routing, amount_cents)
        and len(engine.approval_node_ids(nodes, path)) < 2
    ):
        # 金额已经进入双人审批区间但锁定的旧版本只有一个审批节点：这一笔宁可走既有默认审批流，
        # 也不允许以「单级审批」冒充双人审批。
        return None
    identifier = uuid4().hex
    auto = engine.auto_approved(routing, amount_cents)
    if auto:
        status, current_node = "completed", path[0]
        approvals = {
            "auto_approved": True,
            "outcome": "draft",
            "threshold_cents": routing["auto_approve_below_cents"],
            "approvals": [],
        }
        action = "auto_approve"
    else:
        status, current_node = "running", path[1] if len(path) > 1 else path[0]
        action = "start"
        approvals = {"auto_approved": False, "approvals": []}
    conn.execute(
        "INSERT INTO workflow_instances("
        "id,firm_id,company_id,book_id,definition_id,definition_version,process_kind,"
        "subject_type,subject_id,subject_version,amount_cents,current_node,path_json,status,"
        "suspended_reason,terminal_external_locked,irreversible_reached,approvals_json,"
        "created_by,created_at,updated_at) "
        "VALUES(?,?,?,NULL,?,?,?,?,?,?,?,?,?,?,'',FALSE,FALSE,?,?,?,?)",
        (
            identifier,
            actor.firm_id,
            event["company_id"],
            definition["id"],
            int(definition["version"]),
            definition["process_kind"],
            BUSINESS_SUBJECT,
            event["id"],
            version,
            int(amount_cents),
            current_node,
            json.dumps(path, ensure_ascii=False),
            status,
            json.dumps(approvals, ensure_ascii=False),
            actor.user_id,
            stamp,
            stamp,
        ),
    )
    _log(
        conn,
        actor,
        event["company_id"],
        identifier,
        path[0],
        action,
        "",
        current_node if not auto else "",
        {
            "definition_id": definition["id"],
            "definition_version": int(definition["version"]),
            "path": path,
            "amount_cents": int(amount_cents),
            "auto_approved": auto,
            "subject_version": version,
        },
        stamp,
    )
    return conn.execute(
        f"SELECT {INSTANCE_COLUMNS} FROM workflow_instances WHERE id=?", (identifier,)
    ).fetchone()


def _update(conn, actor, row, *, current_node, status, approvals, lock_flags, stamp, reason=""):
    import json

    terminal_locked, irreversible = lock_flags
    conn.execute(
        "UPDATE workflow_instances SET current_node=?,status=?,suspended_reason=?,"
        "terminal_external_locked=?,irreversible_reached=?,approvals_json=?,updated_at=? "
        "WHERE id=? AND firm_id=?",
        (
            current_node,
            status,
            reason,
            bool(terminal_locked),
            bool(irreversible),
            json.dumps(approvals, ensure_ascii=False),
            stamp,
            row["id"],
            actor.firm_id,
        ),
    )
    return conn.execute(
        f"SELECT {INSTANCE_COLUMNS} FROM workflow_instances WHERE id=?", (row["id"],)
    ).fetchone()


def _approve_current(conn, actor, row, node, approvals, stamp, *, routing=None):
    from ..accounting.domain import AccountingError

    if actor.user_id == row["created_by"]:
        raise AccountingError("不能审批自己发起的流程", 403)
    path = _json(row["path_json"]) or []
    if not node.get("approval"):
        raise AccountingError("当前节点不是审批节点，不能审批", 409)
    recorded = list(approvals.get("approvals") or [])
    recorded.append({"node": node["id"], "user_id": actor.user_id, "at": stamp})
    following = engine.next_node(path, node["id"])
    if following is None and routing and engine.dual_required(routing, int(row["amount_cents"])):
        distinct_approvers = {item.get("user_id") for item in recorded if item.get("user_id")}
        if len(distinct_approvers) < 2:
            raise AccountingError("该金额区间要求两位不同审批人审批，不能由同一人审完所有节点", 403)
    approvals["approvals"] = recorded
    terminal_locked = bool(row["terminal_external_locked"]) or bool(
        node.get("external_locked") or node.get("irreversible")
    )
    irreversible = bool(row["irreversible_reached"]) or bool(node.get("irreversible"))
    if following is None:
        return (
            _update(
                conn,
                actor,
                row,
                current_node=node["id"],
                status="completed",
                approvals=approvals,
                lock_flags=(terminal_locked, irreversible),
                stamp=stamp,
            ),
            "",
        )
    return (
        _update(
            conn,
            actor,
            row,
            current_node=following,
            status="running",
            approvals=approvals,
            lock_flags=(terminal_locked, irreversible),
            stamp=stamp,
        ),
        following,
    )


def advance(conn, actor, row, stage, stamp):
    """业务审批同事务推进流程实例。

    ``stage`` 为 ``"department"`` 或 ``"finance"``。返回推进后的实例行；
    单次业务审批最多只推进一个审批节点，严禁同一审批人连续穿过多个审批节点。
    """

    from ..accounting.domain import AccountingError

    if not row:
        return row
    if row["status"] in {"completed"} and not _json(row["approvals_json"] or {}).get(
        "auto_approved"
    ):
        raise AccountingError("流程实例已完成，不能再按业务审批推进", 409)
    if row["status"] == "rejected":
        raise AccountingError("流程实例已终止，不能再按业务审批推进", 409)
    if row["status"] == "suspended":
        raise AccountingError("流程实例已悬挂，请先在流程中台恢复", 409)
    if row["terminal_external_locked"] or row["irreversible_reached"]:
        raise AccountingError("流程实例已过账或已到不可逆节点，不能再按业务审批推进", 409)
    definition = conn.execute(
        "SELECT nodes_json,routing_json FROM workflow_definitions WHERE id=? AND firm_id=?",
        (row["definition_id"], actor.firm_id),
    ).fetchone()
    if not definition:
        raise AccountingError("流程实例锁定的版本不存在，拒绝推进", 409)
    nodes = engine.node_map(_json(definition["nodes_json"]))
    routing = _json(definition["routing_json"]) or {}
    path = _json(row["path_json"]) or []
    approvals = _json(row["approvals_json"]) or {}
    if row["status"] == "completed":
        # 低风险直通实例：业务审批阶段照常记录，但不改变实例状态。
        return row
    current = nodes.get(row["current_node"])
    if current is None:
        raise AccountingError("流程实例当前节点不在锁定版本里，拒绝推进", 409)
    if stage == "department":
        if current.get("role") != "manager":
            _log(
                conn,
                actor,
                row["company_id"],
                row["id"],
                current["id"],
                "approve",
                current["id"],
                current["id"],
                {
                    "stage": "department",
                    "skipped": True,
                    "reason": "锁定的流程版本没有部门审批节点",
                },
                stamp,
            )
            return row
        updated, next_id = _approve_current(
            conn, actor, row, current, approvals, stamp, routing=routing
        )
        _log(
            conn,
            actor,
            row["company_id"],
            row["id"],
            current["id"],
            "approve",
            current["id"],
            next_id,
            {"stage": "department", "path": path},
            stamp,
        )
        return updated
    if stage != "finance":
        raise AccountingError("未知的业务审批阶段", 422)
    if not current.get("approval"):
        if current.get("terminal"):
            return _update(
                conn,
                actor,
                row,
                current_node=current["id"],
                status="completed",
                approvals=approvals,
                lock_flags=(
                    bool(row["terminal_external_locked"]),
                    bool(row["irreversible_reached"]),
                ),
                stamp=stamp,
            )
        raise AccountingError("流程实例停在非审批节点，拒绝推进", 409)
    # 财务审批只推进当前单个审批节点；若后续仅剩非审批终态节点则自动收口，
    # 严禁同一个审批人循环审批后续其他审批节点。
    updated, next_id = _approve_current(
        conn, actor, row, current, approvals, stamp, routing=routing
    )
    _log(
        conn,
        actor,
        updated["company_id"],
        updated["id"],
        current["id"],
        "approve",
        current["id"],
        next_id,
        {"stage": "finance", "path": path, "node_role": current.get("role")},
        stamp,
    )
    if updated["status"] == "running" and next_id:
        next_node = nodes.get(next_id)
        if next_node and not next_node.get("approval") and next_node.get("terminal"):
            if engine.dual_required(routing, int(updated["amount_cents"])):
                distinct = {
                    item.get("user_id")
                    for item in (_json(updated["approvals_json"]) or {}).get("approvals", [])
                    if item.get("user_id")
                }
                if len(distinct) < 2:
                    raise AccountingError(
                        "该金额区间要求两位不同审批人审批，不能由同一人审完所有节点", 403
                    )
            updated = _update(
                conn,
                actor,
                updated,
                current_node=next_node["id"],
                status="completed",
                approvals=_json(updated["approvals_json"]) or approvals,
                lock_flags=(
                    bool(updated["terminal_external_locked"]),
                    bool(updated["irreversible_reached"]),
                ),
                stamp=stamp,
            )
    return updated


def suspend_for_return(conn, actor, row, reason, stamp):
    """部门/财务退回补件：实例挂起并写明原因，等待重新提交。"""

    from ..accounting.domain import AccountingError

    if not row or row["status"] not in ACTIVE_STATUSES:
        return row
    if row["terminal_external_locked"] or row["irreversible_reached"]:
        raise AccountingError("流程实例已过账或已到不可逆节点，不能再退回补件", 409)
    _log(
        conn,
        actor,
        row["company_id"],
        row["id"],
        row["current_node"],
        "suspend",
        "",
        row["current_node"],
        {"reason": str(reason or "退回补件")},
        stamp,
    )
    return _update(
        conn,
        actor,
        row,
        current_node=row["current_node"],
        status="suspended",
        approvals=_json(row["approvals_json"]) or {},
        lock_flags=(bool(row["terminal_external_locked"]), bool(row["irreversible_reached"])),
        stamp=stamp,
        reason=str(reason or "退回补件"),
    )


def resume_after_resubmit(conn, actor, row, stamp):
    """重新提交后恢复挂起实例（节点与审批记录保持不变，已完成的审批不重来）。"""

    if not row or row["status"] != "suspended":
        return row
    _log(
        conn,
        actor,
        row["company_id"],
        row["id"],
        row["current_node"],
        "resume",
        row["current_node"],
        row["current_node"],
        {"reason": "重新提交补件后恢复"},
        stamp,
    )
    conn.execute(
        "UPDATE workflow_instances SET status='running',suspended_reason='',updated_at=? "
        "WHERE id=? AND firm_id=?",
        (stamp, row["id"], actor.firm_id),
    )
    return conn.execute(
        f"SELECT {INSTANCE_COLUMNS} FROM workflow_instances WHERE id=?", (row["id"],)
    ).fetchone()


def supersede_for_amount_change(conn, actor, row, new_amount_cents, stamp):
    """退回补件修改了报销金额时，原锁定金额的挂起实例作废留痕，由重提按新版本重新路由。"""

    if not row or row["status"] != "suspended":
        return row
    reason = f"重提金额由 {int(row['amount_cents'])} 分调整为 {int(new_amount_cents)} 分，原挂起实例作废重路由"
    _log(
        conn,
        actor,
        row["company_id"],
        row["id"],
        row["current_node"],
        "reject",
        row["current_node"],
        "",
        {"reason": reason, "new_amount_cents": int(new_amount_cents)},
        stamp,
    )
    conn.execute(
        "UPDATE workflow_instances SET status='rejected',suspended_reason=?,updated_at=? "
        "WHERE id=? AND firm_id=?",
        (reason, stamp, row["id"], actor.firm_id),
    )
    return conn.execute(
        f"SELECT {INSTANCE_COLUMNS} FROM workflow_instances WHERE id=?", (row["id"],)
    ).fetchone()


__all__ = [
    "ACTIVE_STATUSES",
    "BUSINESS_SUBJECT",
    "PROCESS_KIND",
    "active_definition",
    "advance",
    "bind",
    "instance_for",
    "resume_after_resubmit",
    "summary",
    "supersede_for_amount_change",
    "suspend_for_return",
]
