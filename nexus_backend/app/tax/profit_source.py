"""可溯源的累计利润参考值（企业所得税预缴预填）。

企业所得税按累计利润计征，而底稿里的 ``ledger_cents`` 是账载税额，不是利润。
这里只读取“有来源、可追溯”的数值：

* 已复核 / 已发布（reviewed、published）的利润表快照 → ``is_final_report=True``；
* 试算 / 已校验（draft、validated）的利润表快照 → ``is_final_report=False``，
  并明确提示正式报表尚未复核；

没有任何可用快照时返回 ``suggested_profit_cents=None`` 并说明原因：既不猜 0，
也不拿账载税额冒充利润。
"""

from __future__ import annotations

import json
from typing import Any

SOURCE_REVIEWED = "reviewed_report"
SOURCE_TRIAL = "trial_report"

REVIEWED_STATUSES = ("reviewed", "published")
TRIAL_STATUSES = ("draft", "validated")

LABELS = {
    SOURCE_REVIEWED: "已复核利润表快照",
    SOURCE_TRIAL: "试算利润表快照",
}

UNAVAILABLE_NOTICE = (
    "本期没有可用的利润表快照：系统不猜测累计利润，也不拿底稿账载税额代替。"
    "请先生成并复核利润表，或人工填写累计利润并说明原因。"
)


def _json(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _cents(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def unavailable() -> dict:
    return {
        "suggested_profit_cents": None,
        "source_kind": None,
        "source_label": None,
        "source_period": None,
        "source_version": None,
        "is_final_report": False,
        "report_run_id": None,
        "notice": UNAVAILABLE_NOTICE,
    }


def suggest(conn, book, period: str) -> dict:
    """Pick the best available profit snapshot for ``book``/``period``.

    Reviewed and published reports win over trial ones; within the same tier the
    highest version wins. Superseded and expired reports are ignored so an old
    number never masquerades as current.
    """
    statuses = (*REVIEWED_STATUSES, *TRIAL_STATUSES)
    placeholders = ",".join("?" for _ in statuses)
    rows = conn.execute(
        "SELECT id,version,status,period,content_json FROM report_runs "
        f"WHERE book_id=? AND period=? AND status IN ({placeholders}) "
        "ORDER BY version DESC",
        (book["id"], period, *statuses),
    ).fetchall()
    best: dict | None = None
    best_rank = -1
    for row in rows:
        statement = _json(_json(row["content_json"]).get("profit_statement"))
        cents = _cents(statement.get("profit_cents"))
        if cents is None:
            continue
        reviewed = row["status"] in REVIEWED_STATUSES
        rank = (2 if reviewed else 1) * 1_000_000 + int(row["version"])
        if rank <= best_rank:
            continue
        best_rank = rank
        best = {
            "suggested_profit_cents": cents,
            "source_kind": SOURCE_REVIEWED if reviewed else SOURCE_TRIAL,
            "source_label": LABELS[SOURCE_REVIEWED if reviewed else SOURCE_TRIAL],
            "source_period": row["period"],
            "source_version": int(row["version"]),
            "is_final_report": reviewed,
            "report_run_id": row["id"],
            "notice": None
            if reviewed
            else "来源是试算利润表：正式报表尚未复核，金额可能随调整变化。",
        }
    return best or unavailable()
