"""PLT07-A / PLT07-B: deterministic shared-service queue lanes and backlog attribution.

All rules are pure, versioned functions over authoritative workspace task fields
using Asia/Shanghai calendar dates. No model scoring or fabricated ETAs.
"""

from __future__ import annotations

from datetime import date, timedelta
from statistics import median
from typing import Any

QUEUE_LANE_RULE_VERSION = "plt07-lanes-v1"
DEFAULT_DUE_SOON_DAYS = 3
MIN_ETA_SAMPLES = 5

LANE_LABELS = {
    "must_today": "今天必须办",
    "due_soon": "即将逾期",
    "waiting_others": "等待别人",
    "no_due_date": "无截止日",
    "scheduled": "计划内待办",
}

BACKLOG_CATEGORIES = (
    ("business_dept", "业务部门（等资料/审批/补件）"),
    ("shared_center", "共享中心（等经办/复核）"),
    ("external_system", "外部系统（等回执/结果未知）"),
    ("unassigned", "未分配"),
)


def due_soon_cutoff(today: str, days: int = DEFAULT_DUE_SOON_DAYS) -> str:
    return (date.fromisoformat(today) + timedelta(days=max(0, int(days)))).isoformat()


def classify_task_lane(
    task: dict[str, Any],
    *,
    today: str,
    due_soon_days: int = DEFAULT_DUE_SOON_DAYS,
) -> str:
    """Assign each task into exactly one mutually exclusive lane."""
    if task.get("bucket") != "action":
        return "waiting_others"
    due_on = str(task.get("due_on") or "").strip()
    if not due_on:
        return "no_due_date"
    if due_on <= today:
        return "must_today"
    cutoff = due_soon_cutoff(today, due_soon_days)
    if due_on <= cutoff:
        return "due_soon"
    return "scheduled"


def partition_queue_lanes(
    tasks: list[dict[str, Any]],
    *,
    today: str,
    due_soon_days: int = DEFAULT_DUE_SOON_DAYS,
) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {key: [] for key in LANE_LABELS}
    for task in tasks:
        lane = classify_task_lane(task, today=today, due_soon_days=due_soon_days)
        buckets[lane].append(task)
    return {
        "rule_version": QUEUE_LANE_RULE_VERSION,
        "today": today,
        "due_soon_days": due_soon_days,
        "counts": {key: len(items) for key, items in buckets.items()},
        "lanes": buckets,
    }


def classify_backlog_category(task: dict[str, Any]) -> str:
    """Attribute a task to one of four factual waiting/blocking categories."""
    responsible = str(task.get("responsible") or "").strip()
    kind = str(task.get("kind") or "").strip()
    state = str(task.get("state") or "").strip()
    if responsible in {"", "未分配"}:
        return "unassigned"
    if kind in {"bridge_return", "delivery", "alert"}:
        return "external_system"
    if kind in {"intake", "approval", "material"} or state == "awaiting_info":
        return "business_dept"
    return "shared_center"


def attribute_backlog(
    tasks: list[dict[str, Any]],
    *,
    sample_durations_minutes: dict[str, list[float]] | None = None,
    min_samples: int = MIN_ETA_SAMPLES,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {code: [] for code, _ in BACKLOG_CATEGORIES}
    for task in tasks:
        grouped[classify_backlog_category(task)].append(task)

    samples = sample_durations_minutes or {}
    summary = []
    for code, label in BACKLOG_CATEGORIES:
        rows = grouped[code]
        companies = sorted(
            {
                str(row.get("company_name") or row.get("company_id") or "")
                for row in rows
                if row.get("company_name") or row.get("company_id")
            }
        )
        oldest_updated = min(
            (str(row.get("updated_at") or "") for row in rows if row.get("updated_at")),
            default=None,
        )
        cat_samples = [float(x) for x in samples.get(code, []) if x is not None and float(x) >= 0]
        if len(cat_samples) >= min_samples and rows:
            med = float(median(cat_samples))
            eta_minutes: float | None = round(med * len(rows), 1)
            eta_label = f"约 {eta_minutes} 分钟（基于近 {len(cat_samples)} 条中位耗时 {round(med, 1)} 分钟）"
        else:
            eta_minutes = None
            eta_label = "样本不足"
        summary.append(
            {
                "code": code,
                "label": label,
                "count": len(rows),
                "companies": companies,
                "oldest_updated_at": oldest_updated,
                "sample_count": len(cat_samples),
                "eta_minutes": eta_minutes,
                "eta_label": eta_label,
            }
        )
    return summary
