"""Batch 09-1.1 流程编排纯规则层：DAG 合法性、金额分流、内容指纹与超时判定。

这里刻意不碰数据库：四类财务流程的合法性、金额条件的整数分校验、超时临界点
都能在没有连接的情况下被确定性验证，服务层只负责把结论落库。

几条硬约定（全部有对应测试）：

* 金额一律整数分；``bool``/浮点/字符串都会被拒绝，避免 0.1+0.2 这类浮点误差
  进入审批阈值。
* DAG 必须「有且仅有一个起点、至少一个终点、全部节点从起点可达、无环」；
  带条件的边必须排在唯一一条兜底边之前，保证同一金额只有一条确定路径。
* 门槛（``auto_approve_below_cents`` / ``dual_approval_from_cents``）非负，
  且直通门槛不得高于双人审批门槛——否则「低风险直通」会盖住「必须双人复核」。
* 已发布版本的规则与指纹由数据库触发器冻结；这里只保证写入前算出的指纹与
  内容一一对应（键排序后的规范 JSON），使「同内容同指纹」可复算。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from ..accounting.domain import AccountingError

PROCESS_KINDS = (
    "expense_reimbursement",
    "payment_approval",
    "fee_variance",
    "monthly_close",
)
PROCESS_LABELS = {
    "expense_reimbursement": "费用报销",
    "payment_approval": "付款审批",
    "fee_variance": "费用差异处理",
    "monthly_close": "月度结账",
}
PROCESS_SUMMARIES = {
    "expense_reimbursement": "员工垫付与报销：金额越大复核层级越高，会计入账后不可回退。",
    "payment_approval": "对外付款：出纳复核与资金审批分离，提交银行后只能冲销。",
    "fee_variance": "结算单与银行回单的差异处理：先解释差异，再决定是否调整。",
    "monthly_close": "月末结账：清单核对与复核分离，正式锁期后不可回退。",
}
STATUSES = ("draft", "active", "archived")
INSTANCE_STATUSES = ("running", "suspended", "completed", "rejected", "escalated")
STEP_ACTIONS = (
    "start",
    "approve",
    "reject",
    "suspend",
    "resume",
    "rollback",
    "escalate_timeout",
    "auto_approve",
    "external_submit",
)

NODE_ROLES = ("creator", "reviewer", "manager", "finance", "cashier")
NODE_KEYS = {
    "id",
    "label",
    "role",
    "approval",
    "terminal",
    "irreversible",
    "external_locked",
    "start",
    "edges",
}
EDGE_KEYS = {"to", "condition"}
CONDITION_KEYS = {"amount_cents_gte", "amount_cents_lt"}
ROUTING_KEYS = {
    "auto_approve_below_cents",
    "dual_approval_from_cents",
    "escalate_after_hours",
}
MAX_NODES = 40
MAX_EDGES = 8


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_time(value: str) -> datetime:
    """Parse the stored ISO timestamp. Naive input is treated as UTC."""

    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def builtin_template(process_kind: str) -> dict:
    """开箱可用的四类流程模板。

    模板只作为「还没有发布过任何版本」时的可视化起点；真正生效的规则必须以
    发布后的版本行为准，模板本身不参与任何实例的路由。
    """

    if process_kind == "expense_reimbursement":
        return {
            "nodes": [
                {
                    "id": "draft",
                    "label": "提交报销单",
                    "role": "creator",
                    "start": True,
                    "edges": [
                        {"to": "finance_review", "condition": {"amount_cents_lt": 500_000}},
                        {"to": "manager_review"},
                    ],
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
                    "label": "财务复核并入账",
                    "role": "reviewer",
                    "approval": True,
                    "irreversible": True,
                    "terminal": True,
                    "edges": [],
                },
            ],
            "routing": {
                "auto_approve_below_cents": 50_000,
                "dual_approval_from_cents": 500_000,
                "escalate_after_hours": 24,
            },
        }
    if process_kind == "payment_approval":
        return {
            "nodes": [
                {
                    "id": "draft",
                    "label": "发起付款申请",
                    "role": "creator",
                    "start": True,
                    "edges": [
                        {"to": "cashier_review", "condition": {"amount_cents_lt": 10_000_000}},
                        {"to": "treasury_review"},
                    ],
                },
                {
                    "id": "treasury_review",
                    "label": "资金主管审批",
                    "role": "manager",
                    "approval": True,
                    "edges": [{"to": "cashier_review"}],
                },
                {
                    "id": "cashier_review",
                    "label": "出纳复核",
                    "role": "finance",
                    "approval": True,
                    "edges": [{"to": "treasury_release"}],
                },
                {
                    "id": "treasury_release",
                    "label": "资金终审并提交银行",
                    "role": "manager",
                    "approval": True,
                    "irreversible": True,
                    "external_locked": True,
                    "terminal": True,
                    "edges": [],
                },
            ],
            "routing": {
                "auto_approve_below_cents": 0,
                "dual_approval_from_cents": 10_000_000,
                "escalate_after_hours": 8,
            },
        }
    if process_kind == "fee_variance":
        return {
            "nodes": [
                {
                    "id": "draft",
                    "label": "登记差异",
                    "role": "creator",
                    "start": True,
                    "edges": [
                        {"to": "explain", "condition": {"amount_cents_lt": 200_000}},
                        {"to": "manager_review"},
                    ],
                },
                {
                    "id": "manager_review",
                    "label": "主管确认差异性质",
                    "role": "manager",
                    "approval": True,
                    "edges": [{"to": "explain"}],
                },
                {
                    "id": "explain",
                    "label": "财务解释差异",
                    "role": "finance",
                    "approval": True,
                    "edges": [{"to": "close_variance"}],
                },
                {
                    "id": "close_variance",
                    "label": "复核并关闭差异",
                    "role": "reviewer",
                    "approval": True,
                    "terminal": True,
                    "edges": [],
                },
            ],
            "routing": {
                "auto_approve_below_cents": 10_000,
                "dual_approval_from_cents": 200_000,
                "escalate_after_hours": 24,
            },
        }
    if process_kind == "monthly_close":
        return {
            "nodes": [
                {
                    "id": "draft",
                    "label": "发起结账清单",
                    "role": "creator",
                    "start": True,
                    "edges": [
                        {"to": "review", "condition": {"amount_cents_lt": 500_000}},
                        {"to": "manager_review"},
                    ],
                },
                {
                    "id": "manager_review",
                    "label": "主管复核清单",
                    "role": "manager",
                    "approval": True,
                    "edges": [{"to": "review"}],
                },
                {
                    "id": "review",
                    "label": "会计复核并正式锁期",
                    "role": "reviewer",
                    "approval": True,
                    "irreversible": True,
                    "terminal": True,
                    "edges": [],
                },
            ],
            "routing": {
                "auto_approve_below_cents": 0,
                "dual_approval_from_cents": 500_000,
                "escalate_after_hours": 48,
            },
        }
    raise AccountingError("未知流程类型", 422)


def _strict_amount(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AccountingError(f"{field} 必须是整数分，不接受浮点或布尔值", 422)
    if value < 0:
        raise AccountingError(f"{field} 不能为负数", 422)
    return value


def normalize_routing(routing, process_kind: str) -> dict:
    """校验并规范化门槛配置；缺省字段取该流程的内置默认值。"""

    if routing is None:
        routing = {}
    if not isinstance(routing, dict):
        raise AccountingError("routing 必须是对象", 422)
    unknown = set(routing) - ROUTING_KEYS
    if unknown:
        raise AccountingError(f"未知的门槛字段：{', '.join(sorted(unknown))}", 422)
    default = builtin_template(process_kind)["routing"]
    normalized = {key: _strict_amount(routing.get(key, default[key]), key) for key in ROUTING_KEYS}
    hours = normalized["escalate_after_hours"]
    if hours < 1 or hours > 24 * 30:
        raise AccountingError("超时升级小时数必须在 1 到 720 之间", 422)
    if normalized["auto_approve_below_cents"] > normalized["dual_approval_from_cents"]:
        raise AccountingError("低风险直通门槛不能高于双人审批门槛", 422)
    return normalized


def normalize_nodes(nodes) -> list[dict]:
    """校验 DAG 结构并返回规范化节点（保持声明顺序，便于前端确定性渲染）。"""

    if not isinstance(nodes, list) or not nodes:
        raise AccountingError("流程至少需要一个节点", 422)
    if len(nodes) > MAX_NODES:
        raise AccountingError(f"流程节点不能超过 {MAX_NODES} 个", 422)
    identifiers: list[str] = []
    normalized: list[dict] = []
    for raw in nodes:
        if not isinstance(raw, dict):
            raise AccountingError("节点必须是对象", 422)
        unknown = set(raw) - NODE_KEYS
        if unknown:
            raise AccountingError(f"节点存在未知字段：{', '.join(sorted(unknown))}", 422)
        identifier = raw.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            raise AccountingError("节点必须带非空标识", 422)
        identifier = identifier.strip()
        if not identifier.replace("_", "").isalnum() or not identifier[0].isalpha():
            raise AccountingError(f"节点标识只允许字母数字下划线且以字母开头：{identifier}", 422)
        if identifier in identifiers:
            raise AccountingError(f"节点标识重复：{identifier}", 422)
        identifiers.append(identifier)
        label = raw.get("label")
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 60:
            raise AccountingError(f"节点 {identifier} 的展示名必须在 1 到 60 字之间", 422)
        role = raw.get("role", "creator")
        if role not in NODE_ROLES:
            raise AccountingError(f"节点 {identifier} 的角色不在允许范围内", 422)
        flags = {}
        for flag in ("approval", "terminal", "irreversible", "external_locked", "start"):
            value = raw.get(flag, False)
            if not isinstance(value, bool):
                raise AccountingError(f"节点 {identifier} 的 {flag} 必须是布尔值", 422)
            flags[flag] = value
        if flags["terminal"] and flags["start"] and len(nodes) > 1:
            raise AccountingError(f"节点 {identifier} 不能同时是起点和终点", 422)
        edges = raw.get("edges", [])
        if not isinstance(edges, list) or len(edges) > MAX_EDGES:
            raise AccountingError(f"节点 {identifier} 的连线必须是数组且不超过 {MAX_EDGES} 条", 422)
        edge_rows = []
        for edge in edges:
            if not isinstance(edge, dict):
                raise AccountingError("连线必须是对象", 422)
            unknown_edge = set(edge) - EDGE_KEYS
            if unknown_edge:
                raise AccountingError(f"连线存在未知字段：{', '.join(sorted(unknown_edge))}", 422)
            target = edge.get("to")
            if not isinstance(target, str) or not target.strip():
                raise AccountingError("连线缺少目标节点", 422)
            condition = edge.get("condition")
            if condition is None:
                condition = {}
            if not isinstance(condition, dict):
                raise AccountingError("连线条件必须是对象", 422)
            unknown_condition = set(condition) - CONDITION_KEYS
            if unknown_condition:
                raise AccountingError(
                    f"连线条件存在未知字段：{', '.join(sorted(unknown_condition))}", 422
                )
            condition = {
                key: _strict_amount(condition[key], f"连线条件 {key}")
                for key in CONDITION_KEYS
                if key in condition
            }
            if (
                "amount_cents_gte" in condition
                and "amount_cents_lt" in condition
                and condition["amount_cents_gte"] >= condition["amount_cents_lt"]
            ):
                raise AccountingError("连线金额区间必须非空（下限小于上限）", 422)
            edge_rows.append({"to": target.strip(), "condition": condition})
        normalized.append(
            {
                "id": identifier,
                "label": label.strip(),
                "role": role,
                **flags,
                "edges": edge_rows,
            }
        )
    by_id = {item["id"]: item for item in normalized}
    for item in normalized:
        targets = [edge["to"] for edge in item["edges"]]
        if len(set(targets)) != len(targets):
            raise AccountingError(f"节点 {item['id']} 存在重复连线目标", 422)
        for edge in item["edges"]:
            if edge["to"] not in by_id:
                raise AccountingError(f"连线指向不存在的节点：{edge['to']}", 422)
        conditional = [edge for edge in item["edges"] if edge["condition"]]
        if len(conditional) != len(item["edges"]) and item["edges"][-1]["condition"]:
            raise AccountingError(f"节点 {item['id']} 的兜底连线必须排在条件连线之后", 422)
        if len(item["edges"]) - len(conditional) > 1:
            raise AccountingError(f"节点 {item['id']} 最多只能有一条兜底连线", 422)
        if item["terminal"] and item["edges"]:
            raise AccountingError(f"终点节点 {item['id']} 不能有后续连线", 422)
        if not item["terminal"] and not item["edges"]:
            raise AccountingError(f"非终点节点 {item['id']} 必须至少有一条后续连线", 422)
    starts = [item["id"] for item in normalized if item["start"]]
    if len(starts) != 1:
        raise AccountingError("流程必须且只能有一个起点节点", 422)
    terminals = [item["id"] for item in normalized if item["terminal"]]
    if not terminals:
        raise AccountingError("流程必须至少有一个终点节点", 422)
    # 无环 + 全部可达：从唯一起点做三色 DFS。
    state: dict[str, int] = {}

    def walk(identifier: str) -> None:
        state[identifier] = 1
        for edge in by_id[identifier]["edges"]:
            mark = state.get(edge["to"], 0)
            if mark == 1:
                raise AccountingError(f"流程存在环：{identifier} → {edge['to']}", 422)
            if mark == 0:
                walk(edge["to"])
        state[identifier] = 2

    walk(starts[0])
    unreachable = [item["id"] for item in normalized if state.get(item["id"]) != 2]
    if unreachable:
        raise AccountingError(f"存在从起点不可达的节点：{', '.join(unreachable)}", 422)
    return normalized


def validate_definition(process_kind, nodes, routing) -> tuple[list[dict], dict]:
    if process_kind not in PROCESS_KINDS:
        raise AccountingError("未知流程类型", 422)
    return normalize_nodes(nodes), normalize_routing(routing, process_kind)


def canonical_payload(process_kind: str, nodes, routing) -> str:
    """规范 JSON：键排序、紧凑分隔符——同内容必然同指纹。"""

    return json.dumps(
        {"process_kind": process_kind, "nodes": nodes, "routing": routing},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def content_fingerprint(process_kind: str, nodes, routing) -> str:
    return hashlib.sha256(
        canonical_payload(process_kind, nodes, routing).encode("utf-8")
    ).hexdigest()


def node_map(nodes) -> dict[str, dict]:
    return {item["id"]: item for item in nodes}


def start_node(nodes) -> dict:
    return next(item for item in nodes if item["start"])


def route_path(nodes, amount_cents: int) -> list[str]:
    """按金额走一条确定路径；同一节点只会出现一次（校验阶段已保证无环）。"""

    by_id = node_map(nodes)
    current = start_node(by_id.values() if False else nodes)["id"]
    path = [current]
    while True:
        node = by_id[current]
        if node["terminal"]:
            return path
        chosen = next(
            (
                edge
                for edge in node["edges"]
                if (not edge["condition"])
                or (
                    edge["condition"].get("amount_cents_gte", 0) <= amount_cents
                    and amount_cents < edge["condition"].get("amount_cents_lt", amount_cents + 1)
                )
            ),
            None,
        )
        if chosen is None:
            raise AccountingError(f"金额 {amount_cents} 分在节点 {current} 没有可走的分支", 422)
        current = chosen["to"]
        if current in path:
            raise AccountingError("流程定义存在环，无法确定路径", 422)
        path.append(current)


def approval_node_ids(nodes, path) -> list[str]:
    by_id = node_map(nodes)
    return [identifier for identifier in path if by_id[identifier]["approval"]]


def auto_approved(routing: dict, amount_cents: int) -> bool:
    return amount_cents < routing["auto_approve_below_cents"]


def dual_required(routing: dict, amount_cents: int) -> bool:
    return amount_cents >= routing["dual_approval_from_cents"]


def escalation_deadline(updated_at: str, routing: dict) -> str:
    return (parse_time(updated_at) + timedelta(hours=routing["escalate_after_hours"])).isoformat()


def escalation_due(updated_at: str, routing: dict, now: str) -> bool:
    return parse_time(now) >= parse_time(escalation_deadline(updated_at, routing))


def next_node(path, current_node: str) -> str | None:
    index = path.index(current_node)
    return path[index + 1] if index + 1 < len(path) else None


def diff_definitions(old: dict, new: dict) -> dict:
    """逐节点比对两版规则，供前端展示「改了什么」，不做静默归一。"""

    old_nodes = node_map(old["nodes"])
    new_nodes = node_map(new["nodes"])
    added = [identifier for identifier in new_nodes if identifier not in old_nodes]
    removed = [identifier for identifier in old_nodes if identifier not in new_nodes]
    changed = []
    for identifier in new_nodes:
        if identifier not in old_nodes:
            continue
        before, after = old_nodes[identifier], new_nodes[identifier]
        fields = {}
        for key in ("label", "role", "approval", "terminal", "irreversible", "external_locked"):
            if before.get(key) != after.get(key):
                fields[key] = {"before": before.get(key), "after": after.get(key)}
        if before.get("edges") != after.get("edges"):
            fields["edges"] = {"before": before.get("edges"), "after": after.get("edges")}
        if fields:
            changed.append({"id": identifier, "fields": fields})
    routing = {
        key: {"before": old["routing"].get(key), "after": new["routing"].get(key)}
        for key in ROUTING_KEYS
        if old["routing"].get(key) != new["routing"].get(key)
    }
    return {
        "added_nodes": added,
        "removed_nodes": removed,
        "changed_nodes": changed,
        "routing": routing,
        "start_changed": old.get("start") != new.get("start"),
        "identical": not (added or removed or changed or routing),
    }
