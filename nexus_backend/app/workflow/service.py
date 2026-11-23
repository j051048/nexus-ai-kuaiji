"""Batch 09-1.1 服务层：版本化 DAG 的发布/回滚、实例启动与人工接管。

设计取舍（都有对应测试）：

* **版本只追加不改写**：编辑规则永远产出新的 ``version`` 行；发布时把旧的
  ``active`` 行改成 ``archived``，回滚则「复制历史版本内容成新版本号」。
  历史行的 ``nodes_json``/``content_fingerprint`` 由双库触发器冻结，服务层
  回滚前还会复算指纹，指纹不一致直接 409（检测被篡改的历史）。
* **实例锁定启动快照**：实例落库时写死 ``definition_id``/``definition_version``
  与 ``path_json``。之后发版、回滚都不影响存量实例：它继续走自己的 path。
* **终态硬红线**：``terminal_external_locked``/``irreversible_reached`` 或路径
  上已过不可逆节点时，回退一律 409 且零状态变更，提示走冲销流程。
* **制单人不能审自己的单**：审批时 ``actor.user_id == created_by`` 直接 403，
  这是机构内部的职责分离，不依赖前端隐藏按钮。
"""

from __future__ import annotations

import json
from uuid import uuid4

from ..accounting.access import AccessControl, FIRM_MANAGERS
from ..accounting.domain import AccountingError, Actor
from ..accounting.service import now
from . import engine

DEFINITION_COLUMNS = (
    "id,firm_id,company_id,process_kind,version,status,nodes_json,routing_json,"
    "content_fingerprint,note,rollback_from_version,created_by,created_at,published_by,published_at"
)
INSTANCE_COLUMNS = (
    "id,firm_id,company_id,book_id,definition_id,definition_version,process_kind,subject_type,"
    "subject_id,amount_cents,current_node,path_json,status,suspended_reason,"
    "terminal_external_locked,irreversible_reached,approvals_json,created_by,created_at,updated_at"
)


def _json(value):
    """PG 的 JSONB 列可能已被驱动解码成 dict/list，SQLite 一律是文本。"""

    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _definition(row) -> dict:
    return {
        "id": row["id"],
        "company_id": row["company_id"],
        "process_kind": row["process_kind"],
        "process_label": engine.PROCESS_LABELS.get(row["process_kind"], row["process_kind"]),
        "version": int(row["version"]),
        "status": row["status"],
        "nodes": _json(row["nodes_json"]),
        "routing": _json(row["routing_json"]),
        "content_fingerprint": row["content_fingerprint"],
        "note": row["note"] or "",
        "rollback_from_version": row["rollback_from_version"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "published_by": row["published_by"],
        "published_at": row["published_at"],
    }


def _instance(row) -> dict:
    return {
        "id": row["id"],
        "company_id": row["company_id"],
        "book_id": row["book_id"],
        "definition_id": row["definition_id"],
        "definition_version": int(row["definition_version"]),
        "process_kind": row["process_kind"],
        "process_label": engine.PROCESS_LABELS.get(row["process_kind"], row["process_kind"]),
        "subject_type": row["subject_type"],
        "subject_id": row["subject_id"],
        "amount_cents": int(row["amount_cents"]),
        "current_node": row["current_node"],
        "path": _json(row["path_json"]),
        "status": row["status"],
        "suspended_reason": row["suspended_reason"] or "",
        "terminal_external_locked": bool(row["terminal_external_locked"]),
        "irreversible_reached": bool(row["irreversible_reached"]),
        "approvals": _json(row["approvals_json"]) or {},
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class WorkflowService:
    def __init__(self, db, accounting=None):
        self.db = db
        self.access = getattr(accounting, "access", None) or AccessControl()

    # ------------------------------------------------------------------ 定义
    def overview(self, actor: Actor, company_id: str) -> dict:
        with self.db.transaction(actor=actor) as conn:
            company = self.access.company(conn, actor, company_id)
            can_manage = self.access.firm_role(
                conn, actor
            ) in FIRM_MANAGERS or self.access.company_role(conn, actor, company_id) in {
                "owner",
                "admin",
            }
            rows = conn.execute(
                f"SELECT {DEFINITION_COLUMNS} FROM workflow_definitions "
                "WHERE firm_id=? AND company_id=? ORDER BY process_kind,version DESC",
                (actor.firm_id, company_id),
            ).fetchall()
            counts = conn.execute(
                "SELECT process_kind,status,COUNT(*) AS total FROM workflow_instances "
                "WHERE firm_id=? AND company_id=? GROUP BY process_kind,status",
                (actor.firm_id, company_id),
            ).fetchall()
        by_kind: dict[str, list] = {kind: [] for kind in engine.PROCESS_KINDS}
        for row in rows:
            by_kind.setdefault(row["process_kind"], []).append(_definition(row))
        tally: dict[str, dict[str, int]] = {kind: {} for kind in engine.PROCESS_KINDS}
        for row in counts:
            tally.setdefault(row["process_kind"], {})[row["status"]] = int(row["total"])
        processes = []
        for kind in engine.PROCESS_KINDS:
            versions = by_kind.get(kind, [])
            active = next((item for item in versions if item["status"] == "active"), None)
            processes.append(
                {
                    "process_kind": kind,
                    "label": engine.PROCESS_LABELS[kind],
                    "summary": engine.PROCESS_SUMMARIES[kind],
                    "template": engine.builtin_template(kind),
                    "active": active,
                    "versions": versions,
                    "instance_counts": tally.get(kind, {}),
                }
            )
        return {
            "company_id": company["id"],
            "company_name": company["name"],
            "can_manage": can_manage,
            "processes": processes,
            "routing_rule_version": "b09-workflow-v1",
        }

    def definition(self, actor: Actor, definition_id: str) -> dict:
        with self.db.transaction(actor=actor) as conn:
            row = self._definition_row(conn, actor, definition_id)
            active = conn.execute(
                f"SELECT {DEFINITION_COLUMNS} FROM workflow_definitions "
                "WHERE firm_id=? AND company_id=? AND process_kind=? AND status='active'",
                (actor.firm_id, row["company_id"], row["process_kind"]),
            ).fetchone()
        payload = _definition(row)
        if active and active["id"] != row["id"]:
            payload["diff_from_active"] = engine.diff_definitions(_definition(active), payload)
        else:
            payload["diff_from_active"] = None
        payload["nodes_validated"], payload["routing_validated"] = engine.validate_definition(
            row["process_kind"], payload["nodes"], payload["routing"]
        )
        return payload

    def create_version(self, actor: Actor, payload) -> dict:
        company_id = payload.company_id
        nodes, routing = engine.validate_definition(
            payload.process_kind, payload.nodes, payload.routing
        )
        fingerprint = engine.content_fingerprint(payload.process_kind, nodes, routing)
        stamp = now()
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            company = self.access.company(conn, actor, company_id, manage=True)
            row = conn.execute(
                "SELECT COALESCE(MAX(version),0) AS version FROM workflow_definitions "
                "WHERE firm_id=? AND company_id=? AND process_kind=?",
                (actor.firm_id, company_id, payload.process_kind),
            ).fetchone()
            version = int(row["version"]) + 1
            identifier = uuid4().hex
            conn.execute(
                "INSERT INTO workflow_definitions("
                "id,firm_id,company_id,process_kind,version,status,nodes_json,routing_json,"
                "content_fingerprint,note,created_by,created_at) VALUES(?,?,?,?,?,'draft',?,?,?,?,?,?)",
                (
                    identifier,
                    actor.firm_id,
                    company_id,
                    payload.process_kind,
                    version,
                    json.dumps(nodes, ensure_ascii=False),
                    json.dumps(routing, ensure_ascii=False),
                    fingerprint,
                    payload.note,
                    actor.user_id,
                    stamp,
                ),
            )
            self._audit(
                conn,
                actor,
                None,
                "workflow.definition.create",
                identifier,
                {
                    "company_id": company_id,
                    "process_kind": payload.process_kind,
                    "version": version,
                    "content_fingerprint": fingerprint,
                },
            )
            if payload.publish:
                self._publish(conn, actor, identifier, stamp)
            created = conn.execute(
                f"SELECT {DEFINITION_COLUMNS} FROM workflow_definitions WHERE id=?",
                (identifier,),
            ).fetchone()
        result = _definition(created)
        result["company_name"] = company["name"]
        return result

    def publish(self, actor: Actor, definition_id: str) -> dict:
        with self.db.transaction(actor=actor) as conn:
            company_id = self._definition_row(conn, actor, definition_id)["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            self.access.company(conn, actor, company_id, manage=True)
            self._publish(conn, actor, definition_id, now())
            updated = conn.execute(
                f"SELECT {DEFINITION_COLUMNS} FROM workflow_definitions WHERE id=?",
                (definition_id,),
            ).fetchone()
        return _definition(updated)

    def _publish(self, conn, actor: Actor, definition_id: str, stamp: str) -> None:
        row = conn.execute(
            f"SELECT {DEFINITION_COLUMNS} FROM workflow_definitions WHERE id=? AND firm_id=?",
            (definition_id, actor.firm_id),
        ).fetchone()
        if not row:
            raise AccountingError("流程版本不存在或无权访问", 404)
        if row["status"] == "active":
            raise AccountingError("该版本已经是生效版本", 409)
        # 先归档旧激活版再激活新版本：部分唯一索引不允许两条 active 并存。
        conn.execute(
            "UPDATE workflow_definitions SET status='archived' "
            "WHERE firm_id=? AND company_id=? AND process_kind=? AND status='active'",
            (actor.firm_id, row["company_id"], row["process_kind"]),
        )
        conn.execute(
            "UPDATE workflow_definitions SET status='active',published_by=?,published_at=? "
            "WHERE id=? AND firm_id=?",
            (actor.user_id, stamp, definition_id, actor.firm_id),
        )
        self._audit(
            conn,
            actor,
            None,
            "workflow.definition.publish",
            definition_id,
            {
                "company_id": row["company_id"],
                "process_kind": row["process_kind"],
                "version": int(row["version"]),
                "content_fingerprint": row["content_fingerprint"],
                "rolled_back_from": None,
            },
        )

    def rollback(self, actor: Actor, definition_id: str, note: str = "") -> dict:
        """回滚＝复制历史版本内容建新版本号；历史行一个字节都不改。"""

        stamp = now()
        with self.db.transaction(actor=actor) as conn:
            company_id = self._definition_row(conn, actor, definition_id)["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            target = self._definition_row(conn, actor, definition_id)
            self.access.company(conn, actor, company_id, manage=True)
            if target["status"] == "active":
                raise AccountingError("该版本已经是生效版本，无需回滚", 409)
            nodes, routing = engine.validate_definition(
                target["process_kind"],
                _json(target["nodes_json"]),
                _json(target["routing_json"]),
            )
            fingerprint = engine.content_fingerprint(target["process_kind"], nodes, routing)
            if fingerprint != target["content_fingerprint"]:
                raise AccountingError("历史版本内容与指纹不一致，拒绝回滚", 409)
            row = conn.execute(
                "SELECT COALESCE(MAX(version),0) AS version FROM workflow_definitions "
                "WHERE firm_id=? AND company_id=? AND process_kind=?",
                (actor.firm_id, target["company_id"], target["process_kind"]),
            ).fetchone()
            version = int(row["version"]) + 1
            identifier = uuid4().hex
            # 先归档旧激活版再写入新激活版：部分唯一索引只允许一条 active。
            conn.execute(
                "UPDATE workflow_definitions SET status='archived' "
                "WHERE firm_id=? AND company_id=? AND process_kind=? AND status='active'",
                (actor.firm_id, target["company_id"], target["process_kind"]),
            )
            conn.execute(
                "INSERT INTO workflow_definitions("
                "id,firm_id,company_id,process_kind,version,status,nodes_json,routing_json,"
                "content_fingerprint,note,rollback_from_version,created_by,created_at,"
                "published_by,published_at) VALUES(?,?,?,?,?,'active',?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    actor.firm_id,
                    target["company_id"],
                    target["process_kind"],
                    version,
                    json.dumps(nodes, ensure_ascii=False),
                    json.dumps(routing, ensure_ascii=False),
                    fingerprint,
                    note or f"回滚自版本 {target['version']}",
                    int(target["version"]),
                    actor.user_id,
                    stamp,
                    actor.user_id,
                    stamp,
                ),
            )
            self._audit(
                conn,
                actor,
                None,
                "workflow.definition.rollback",
                identifier,
                {
                    "company_id": target["company_id"],
                    "process_kind": target["process_kind"],
                    "version": version,
                    "rolled_back_from": int(target["version"]),
                    "content_fingerprint": fingerprint,
                },
            )
            created = conn.execute(
                f"SELECT {DEFINITION_COLUMNS} FROM workflow_definitions WHERE id=?",
                (identifier,),
            ).fetchone()
        return _definition(created)

    def diff(self, actor: Actor, from_id: str, to_id: str) -> dict:
        with self.db.transaction(actor=actor) as conn:
            before = self._definition_row(conn, actor, from_id)
            after = self._definition_row(conn, actor, to_id)
            if before["company_id"] != after["company_id"]:
                raise AccountingError("只能对比同一企业内的流程版本", 422)
        left, right = _definition(before), _definition(after)
        result = engine.diff_definitions(left, right)
        result.update(
            {
                "from": {
                    "id": left["id"],
                    "version": left["version"],
                    "status": left["status"],
                    "published_at": left["published_at"],
                },
                "to": {
                    "id": right["id"],
                    "version": right["version"],
                    "status": right["status"],
                    "published_at": right["published_at"],
                },
            }
        )
        return result

    # ------------------------------------------------------------------ 实例
    def instances(self, actor: Actor, company_id: str, status: str = "") -> dict:
        if status and status not in engine.INSTANCE_STATUSES:
            raise AccountingError("实例状态筛选无效", 422)
        with self.db.transaction(actor=actor) as conn:
            self.access.company(conn, actor, company_id)
            sql = (
                f"SELECT {INSTANCE_COLUMNS} FROM workflow_instances "
                "WHERE firm_id=? AND company_id=?"
            )
            args: list = [actor.firm_id, company_id]
            if status:
                sql += " AND status=?"
                args.append(status)
            rows = conn.execute(
                sql + " ORDER BY updated_at DESC,id DESC LIMIT 200", args
            ).fetchall()
            logs = conn.execute(
                "SELECT instance_id,node_id,action,from_node,to_node,actor_user_id,reason,"
                "metadata_json,created_at FROM workflow_step_logs WHERE firm_id=? AND company_id=? "
                "ORDER BY created_at,id LIMIT 2000",
                (actor.firm_id, company_id),
            ).fetchall()
        trail: dict[str, list] = {}
        for row in logs:
            trail.setdefault(row["instance_id"], []).append(
                {
                    "node_id": row["node_id"],
                    "action": row["action"],
                    "from_node": row["from_node"],
                    "to_node": row["to_node"],
                    "actor_user_id": row["actor_user_id"],
                    "reason": row["reason"],
                    "metadata": _json(row["metadata_json"]) or {},
                    "created_at": row["created_at"],
                }
            )
        items = []
        for row in rows:
            item = _instance(row)
            item["logs"] = trail.get(row["id"], [])
            items.append(item)
        return {
            "company_id": company_id,
            "status": status,
            "items": items,
            "total": len(items),
        }

    def start(self, actor: Actor, payload, *, moment: str | None = None) -> dict:
        stamp = moment or now()
        with self.db.transaction(
            write=True, actor=actor, company_id=payload.company_id, write_scope="company"
        ) as conn:
            book = self.access.book(conn, actor, payload.book_id, "edit")
            if book["company_id"] != payload.company_id:
                raise AccountingError("账套不属于所选企业", 422)
            definition = self._resolve_definition(conn, actor, payload)
            nodes = _json(definition["nodes_json"])
            routing = _json(definition["routing_json"])
            path = engine.route_path(nodes, payload.amount_cents)
            approvals = engine.approval_node_ids(nodes, path)
            if engine.dual_required(routing, payload.amount_cents) and len(approvals) < 2:
                raise AccountingError(
                    "该金额区间要求双人审批，当前流程定义不满足，请先补齐审批节点", 422
                )
            identifier = uuid4().hex
            auto = engine.auto_approved(routing, payload.amount_cents)
            approval_log: dict = {}
            if auto:
                status, current_node = "completed", path[0]
                approval_log = {
                    "auto_approved": True,
                    "outcome": "draft",
                    "threshold_cents": routing["auto_approve_below_cents"],
                    "approvals": [],
                }
                action = "auto_approve"
            else:
                # 发起即代表发起人节点已完成，实例停在第一个待办理节点。
                status, current_node = "running", path[1]
                action = "start"
            conn.execute(
                "INSERT INTO workflow_instances("
                "id,firm_id,company_id,book_id,definition_id,definition_version,process_kind,"
                "subject_type,subject_id,amount_cents,current_node,path_json,status,"
                "suspended_reason,terminal_external_locked,irreversible_reached,approvals_json,"
                "created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'',FALSE,FALSE,?,?,?,?)",
                (
                    identifier,
                    actor.firm_id,
                    payload.company_id,
                    payload.book_id,
                    definition["id"],
                    int(definition["version"]),
                    definition["process_kind"],
                    payload.subject_type,
                    payload.subject_id,
                    payload.amount_cents,
                    current_node,
                    json.dumps(path, ensure_ascii=False),
                    status,
                    json.dumps(approval_log, ensure_ascii=False),
                    actor.user_id,
                    stamp,
                    stamp,
                ),
            )
            self._log(
                conn,
                actor,
                payload.company_id,
                identifier,
                path[0],
                action,
                "",
                current_node if not auto else "",
                {
                    "definition_id": definition["id"],
                    "definition_version": int(definition["version"]),
                    "path": path,
                    "amount_cents": payload.amount_cents,
                    "auto_approved": auto,
                    "dual_required": engine.dual_required(routing, payload.amount_cents),
                },
                stamp,
            )
            created = conn.execute(
                f"SELECT {INSTANCE_COLUMNS} FROM workflow_instances WHERE id=?",
                (identifier,),
            ).fetchone()
        result = _instance(created)
        result["nodes"] = nodes
        result["routing"] = routing
        result["escalation_deadline"] = (
            None if status != "running" else engine.escalation_deadline(stamp, routing)
        )
        return result

    def act(self, actor: Actor, instance_id: str, payload, *, moment: str | None = None) -> dict:
        stamp = moment or now()
        action = payload.action
        if action not in engine.STEP_ACTIONS:
            raise AccountingError("未知的流程动作", 422)
        if action in {"reject", "suspend", "rollback"} and len(payload.reason.strip()) < 2:
            raise AccountingError("该动作必须填写原因", 422)
        with self.db.transaction(actor=actor) as conn:
            company_id = self._instance_row(conn, actor, instance_id)["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            row = self._instance_row(conn, actor, instance_id)
            instance = _instance(row)
            definition = conn.execute(
                f"SELECT {DEFINITION_COLUMNS} FROM workflow_definitions WHERE id=? AND firm_id=?",
                (instance["definition_id"], actor.firm_id),
            ).fetchone()
            if not definition:
                raise AccountingError("流程实例引用的版本已不可见", 409)
            nodes = _json(definition["nodes_json"])
            routing = _json(definition["routing_json"])
            by_id = engine.node_map(nodes)
            path = instance["path"]
            current = instance["current_node"]
            if action == "escalate_timeout":
                self.access.company(conn, actor, instance["company_id"], manage=True)
            elif action == "start" and actor.user_id == instance["created_by"]:
                # 退回起点的实例由发起人补正后重新提交，不因此获得其他权限。
                self.access.book(conn, actor, instance["book_id"], "edit")
            else:
                self.access.book(conn, actor, instance["book_id"], "review")
            if current not in by_id:
                raise AccountingError("实例当前节点不在锁定快照中，请联系管理员", 409)
            if action in {"approve", "external_submit"} and row["status"] not in {
                "running",
                "escalated",
            }:
                raise AccountingError("只有进行中或已升级的实例可以办理", 409)
            if action == "approve":
                if not by_id[current]["approval"]:
                    raise AccountingError("当前节点不是审批节点", 409)
                if actor.user_id == instance["created_by"]:
                    raise AccountingError("不能审批自己发起的流程", 403)
                approvals = instance["approvals"] or {}
                approvals.setdefault("approvals", []).append(
                    {
                        "node_id": current,
                        "user_id": actor.user_id,
                        "at": stamp,
                        "reason": payload.reason,
                    }
                )
                following = engine.next_node(path, current)
                # 节点自身声明的外部/不可逆终态，在离开该节点时就已构成事实。
                locked = bool(
                    instance["terminal_external_locked"]
                    or by_id[current]["external_locked"]
                    or by_id[current]["irreversible"]
                )
                irreversible = bool(
                    instance["irreversible_reached"] or by_id[current]["irreversible"]
                )
                if following is None:
                    new_status, current_node = "completed", current
                else:
                    new_status, current_node = "running", following
                self._update(
                    conn,
                    actor,
                    instance_id,
                    current_node=current_node,
                    status=new_status,
                    approvals=approvals,
                    terminal_external_locked=locked,
                    irreversible_reached=irreversible,
                    suspended_reason="",
                    stamp=stamp,
                )
                self._log(
                    conn,
                    actor,
                    instance["company_id"],
                    instance_id,
                    current,
                    "approve",
                    current,
                    current_node,
                    {"reason": payload.reason},
                    stamp,
                )
            elif action == "reject":
                self._update(
                    conn,
                    actor,
                    instance_id,
                    current_node=current,
                    status="rejected",
                    approvals=instance["approvals"],
                    terminal_external_locked=instance["terminal_external_locked"],
                    irreversible_reached=instance["irreversible_reached"],
                    suspended_reason=payload.reason,
                    stamp=stamp,
                )
                self._log(
                    conn,
                    actor,
                    instance["company_id"],
                    instance_id,
                    current,
                    "reject",
                    current,
                    current,
                    {"reason": payload.reason},
                    stamp,
                )
            elif action == "suspend":
                if row["status"] != "running":
                    raise AccountingError("只有进行中的实例可以挂起", 409)
                self._update(
                    conn,
                    actor,
                    instance_id,
                    current_node=current,
                    status="suspended",
                    approvals=instance["approvals"],
                    terminal_external_locked=instance["terminal_external_locked"],
                    irreversible_reached=instance["irreversible_reached"],
                    suspended_reason=payload.reason,
                    stamp=stamp,
                )
                self._log(
                    conn,
                    actor,
                    instance["company_id"],
                    instance_id,
                    current,
                    "suspend",
                    current,
                    current,
                    {"reason": payload.reason},
                    stamp,
                )
            elif action == "resume":
                if row["status"] != "suspended":
                    raise AccountingError("只有已挂起的实例可以恢复", 409)
                self._update(
                    conn,
                    actor,
                    instance_id,
                    current_node=current,
                    status="running",
                    approvals=instance["approvals"],
                    terminal_external_locked=instance["terminal_external_locked"],
                    irreversible_reached=instance["irreversible_reached"],
                    suspended_reason="",
                    stamp=stamp,
                )
                self._log(
                    conn,
                    actor,
                    instance["company_id"],
                    instance_id,
                    current,
                    "resume",
                    current,
                    current,
                    {"reason": payload.reason},
                    stamp,
                )
            elif action == "rollback":
                # 终态硬红线优先判定：已过账/已提交外部系统一律走冲销，零状态变更。
                passed = path[: path.index(current)]
                locked = bool(
                    instance["terminal_external_locked"]
                    or instance["irreversible_reached"]
                    or any(
                        by_id[node]["irreversible"] or by_id[node]["external_locked"]
                        for node in passed
                    )
                )
                if locked:
                    raise AccountingError(
                        "该流程已过账或已提交银行/税局，无法回退，请走冲销流程", 409
                    )
                if row["status"] in {"completed", "rejected"}:
                    raise AccountingError("已结束的流程实例不能回退", 409)
                index = path.index(current)
                if index == 0:
                    raise AccountingError("流程已在起点，没有可回退的上一步", 409)
                target = path[index - 1]
                approvals = instance["approvals"] or {}
                # 回退目标节点自身的审批也作废：退到该节点即代表需要重新审批。
                approvals["approvals"] = [
                    item
                    for item in approvals.get("approvals", [])
                    if item.get("node_id") not in path[index - 1 :]
                ]
                self._update(
                    conn,
                    actor,
                    instance_id,
                    current_node=target,
                    status="running",
                    approvals=approvals,
                    terminal_external_locked=instance["terminal_external_locked"],
                    irreversible_reached=instance["irreversible_reached"],
                    suspended_reason="",
                    stamp=stamp,
                )
                self._log(
                    conn,
                    actor,
                    instance["company_id"],
                    instance_id,
                    target,
                    "rollback",
                    current,
                    target,
                    {"reason": payload.reason, "path": path},
                    stamp,
                )
            elif action == "escalate_timeout":
                if row["status"] != "running":
                    raise AccountingError("只有进行中的实例可以升级", 409)
                if not engine.escalation_due(row["updated_at"], routing, stamp):
                    raise AccountingError("尚未到升级时限", 409)
                self._update(
                    conn,
                    actor,
                    instance_id,
                    current_node=current,
                    status="escalated",
                    approvals=instance["approvals"],
                    terminal_external_locked=instance["terminal_external_locked"],
                    irreversible_reached=instance["irreversible_reached"],
                    suspended_reason=payload.reason or "超时升级",
                    stamp=stamp,
                )
                self._log(
                    conn,
                    actor,
                    instance["company_id"],
                    instance_id,
                    current,
                    "escalate_timeout",
                    current,
                    current,
                    {"deadline": engine.escalation_deadline(row["updated_at"], routing)},
                    stamp,
                )
            elif action == "external_submit":
                if not (by_id[current]["external_locked"] or by_id[current]["irreversible"]):
                    raise AccountingError("当前节点未声明外部终态，不能提交外部系统", 409)
                self._update(
                    conn,
                    actor,
                    instance_id,
                    current_node=current,
                    status=row["status"],
                    approvals=instance["approvals"],
                    terminal_external_locked=True,
                    irreversible_reached=instance["irreversible_reached"],
                    suspended_reason=instance["suspended_reason"],
                    stamp=stamp,
                )
                self._log(
                    conn,
                    actor,
                    instance["company_id"],
                    instance_id,
                    current,
                    "external_submit",
                    current,
                    current,
                    {"metadata": payload.metadata},
                    stamp,
                )
            elif action == "start":
                # 仅用于「已退回发起人」的实例补正后重新提交，不新建实例。
                if row["status"] != "running":
                    raise AccountingError("只有进行中的实例可以重新提交", 409)
                if current != path[0]:
                    raise AccountingError("只有退回起点的实例需要重新提交", 409)
                following = engine.next_node(path, current)
                if not following:
                    raise AccountingError("流程定义缺少后续办理节点", 409)
                self._update(
                    conn,
                    actor,
                    instance_id,
                    current_node=following,
                    status="running",
                    approvals=instance["approvals"],
                    terminal_external_locked=instance["terminal_external_locked"],
                    irreversible_reached=instance["irreversible_reached"],
                    suspended_reason="",
                    stamp=stamp,
                )
                self._log(
                    conn,
                    actor,
                    instance["company_id"],
                    instance_id,
                    current,
                    "start",
                    current,
                    following,
                    {"resubmitted": True, "reason": payload.reason},
                    stamp,
                )
            else:  # auto_approve 只由实例创建入口产生，不允许对外提交
                raise AccountingError("该动作不能在此提交", 422)
            updated = conn.execute(
                f"SELECT {INSTANCE_COLUMNS} FROM workflow_instances WHERE id=?",
                (instance_id,),
            ).fetchone()
        result = _instance(updated)
        result["nodes"] = nodes
        result["routing"] = routing
        result["escalation_deadline"] = (
            engine.escalation_deadline(result["updated_at"], routing)
            if result["status"] in {"running", "escalated"}
            else None
        )
        return result

    def sweep_escalations(self, actor: Actor, company_id: str, moment: str = "") -> dict:
        stamp = moment or now()
        escalated = []
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            self.access.company(conn, actor, company_id, manage=True)
            rows = conn.execute(
                f"SELECT {INSTANCE_COLUMNS} FROM workflow_instances "
                "WHERE firm_id=? AND company_id=? AND status='running'",
                (actor.firm_id, company_id),
            ).fetchall()
            for row in rows:
                definition = conn.execute(
                    "SELECT routing_json FROM workflow_definitions WHERE id=? AND firm_id=?",
                    (row["definition_id"], actor.firm_id),
                ).fetchone()
                if not definition:
                    continue
                routing = _json(definition["routing_json"])
                if not engine.escalation_due(row["updated_at"], routing, stamp):
                    continue
                self._update(
                    conn,
                    actor,
                    row["id"],
                    current_node=row["current_node"],
                    status="escalated",
                    approvals=_json(row["approvals_json"]) or {},
                    terminal_external_locked=row["terminal_external_locked"],
                    irreversible_reached=row["irreversible_reached"],
                    suspended_reason="超时升级",
                    stamp=stamp,
                )
                self._log(
                    conn,
                    actor,
                    company_id,
                    row["id"],
                    row["current_node"],
                    "escalate_timeout",
                    row["current_node"],
                    row["current_node"],
                    {
                        "deadline": engine.escalation_deadline(row["updated_at"], routing),
                        "sweep": True,
                    },
                    stamp,
                )
                escalated.append(row["id"])
        return {"company_id": company_id, "escalated": escalated, "total": len(escalated)}

    # ------------------------------------------------------------------ 内部
    def _definition_row(self, conn, actor: Actor, definition_id: str):
        row = conn.execute(
            f"SELECT {DEFINITION_COLUMNS} FROM workflow_definitions WHERE id=? AND firm_id=?",
            (definition_id, actor.firm_id),
        ).fetchone()
        if not row:
            raise AccountingError("流程版本不存在或无权访问", 404)
        return row

    def _instance_row(self, conn, actor: Actor, instance_id: str):
        row = conn.execute(
            f"SELECT {INSTANCE_COLUMNS} FROM workflow_instances WHERE id=? AND firm_id=?",
            (instance_id, actor.firm_id),
        ).fetchone()
        if not row:
            raise AccountingError("流程实例不存在或无权访问", 404)
        return row

    def _resolve_definition(self, conn, actor: Actor, payload):
        if payload.definition_id:
            row = self._definition_row(conn, actor, payload.definition_id)
            if row["company_id"] != payload.company_id:
                raise AccountingError("流程版本不属于所选企业", 422)
        else:
            if not payload.process_kind:
                raise AccountingError("请选择流程类型或指定版本", 422)
            if payload.process_kind not in engine.PROCESS_KINDS:
                raise AccountingError("未知流程类型", 422)
            row = conn.execute(
                f"SELECT {DEFINITION_COLUMNS} FROM workflow_definitions "
                "WHERE firm_id=? AND company_id=? AND process_kind=? AND status='active'",
                (actor.firm_id, payload.company_id, payload.process_kind),
            ).fetchone()
            if not row:
                raise AccountingError("该企业还没有已生效的流程版本", 409)
        if row["status"] != "active":
            raise AccountingError("只能按已生效版本发起流程", 409)
        return row

    def _update(
        self,
        conn,
        actor: Actor,
        instance_id: str,
        *,
        current_node: str,
        status: str,
        approvals: dict,
        terminal_external_locked,
        irreversible_reached,
        suspended_reason: str,
        stamp: str,
    ) -> None:
        conn.execute(
            "UPDATE workflow_instances SET current_node=?,status=?,suspended_reason=?,"
            "terminal_external_locked=?,irreversible_reached=?,approvals_json=?,updated_at=? "
            "WHERE id=? AND firm_id=?",
            (
                current_node,
                status,
                suspended_reason,
                bool(terminal_external_locked),
                bool(irreversible_reached),
                json.dumps(approvals or {}, ensure_ascii=False),
                stamp,
                instance_id,
                actor.firm_id,
            ),
        )

    def _log(
        self,
        conn,
        actor: Actor,
        company_id: str,
        instance_id: str,
        node_id: str,
        action: str,
        from_node: str,
        to_node: str,
        metadata: dict,
        stamp: str,
    ) -> None:
        conn.execute(
            "INSERT INTO workflow_step_logs(id,firm_id,company_id,instance_id,node_id,action,"
            "from_node,to_node,actor_user_id,reason,metadata_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                uuid4().hex,
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

    @staticmethod
    def _audit(conn, actor: Actor, book_id, action: str, object_id: str, details: dict) -> None:
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                actor.firm_id,
                book_id,
                actor.user_id,
                action,
                object_id,
                json.dumps(details, ensure_ascii=False),
                now(),
            ),
        )
