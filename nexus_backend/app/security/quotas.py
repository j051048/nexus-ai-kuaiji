"""Per-firm quotas: the guard that keeps one firm from starving the cluster.

Quotas live in one row per firm (`firm_quotas`) with documented defaults in
`DEFAULTS`, so a firm that never configured anything still has limits and the
numbers cannot drift between code and schema. Enforcement is deliberately a
single function per resource: callers pass the *requested* amount and get an
`AccountingError` with the HTTP status the client should see (429 for rate-ish
limits, 413 for size limits) plus a message that names the limit and the
current usage.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from dataclasses import dataclass

from ..accounting.domain import AccountingError, Actor


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


logger = logging.getLogger("accounting.quotas")

DEFAULTS = {
    "import_rows_per_batch": 5000,
    "async_jobs_per_day": 500,
    "storage_bytes": 2147483648,  # 2 GiB
    "concurrent_requests": 32,
}

LIMITS = {
    "import_rows_per_batch": (1, 200000),
    "async_jobs_per_day": (1, 100000),
    "storage_bytes": (1048576, 1099511627776),
    "concurrent_requests": (1, 4096),
}

_STATUS = {
    "import_rows_per_batch": 413,
    "storage_bytes": 413,
    "async_jobs_per_day": 429,
    "concurrent_requests": 429,
}

_LABEL = {
    "import_rows_per_batch": "单次导入行数",
    "async_jobs_per_day": "单日异步任务数",
    "storage_bytes": "原件总容量（字节）",
    "concurrent_requests": "并发请求数",
}


@dataclass(frozen=True)
class Quota:
    import_rows_per_batch: int = DEFAULTS["import_rows_per_batch"]
    async_jobs_per_day: int = DEFAULTS["async_jobs_per_day"]
    storage_bytes: int = DEFAULTS["storage_bytes"]
    concurrent_requests: int = DEFAULTS["concurrent_requests"]

    def as_dict(self) -> dict[str, int]:
        return {
            "import_rows_per_batch": self.import_rows_per_batch,
            "async_jobs_per_day": self.async_jobs_per_day,
            "storage_bytes": self.storage_bytes,
            "concurrent_requests": self.concurrent_requests,
        }


def _row_to_quota(row) -> Quota:
    if row is None:
        return Quota()
    return Quota(
        import_rows_per_batch=int(row["import_rows_per_batch"]),
        async_jobs_per_day=int(row["async_jobs_per_day"]),
        storage_bytes=int(row["storage_bytes"]),
        concurrent_requests=int(row["concurrent_requests"]),
    )


class QuotaService:
    """Reads and enforces firm quotas. Writes require a firm manager."""

    def __init__(self, db, access=None):
        self.db = db
        if access is None:
            from ..accounting.access import AccessControl

            access = AccessControl()
        self.access = access

    # -- read ---------------------------------------------------------
    def quota(self, actor: Actor) -> Quota:
        with self.db.transaction(actor=actor) as conn:
            row = conn.execute(
                "SELECT * FROM firm_quotas WHERE firm_id = ?", (actor.firm_id,)
            ).fetchone()
        return _row_to_quota(row)

    # -- write (manager only) -----------------------------------------
    def configure(self, actor: Actor, **values: int) -> Quota:
        unknown = set(values) - set(DEFAULTS)
        if unknown:
            raise AccountingError(f"未知的配额项：{', '.join(sorted(unknown))}", status=422)
        for name, value in values.items():
            low, high = LIMITS[name]
            if not isinstance(value, int) or isinstance(value, bool):
                raise AccountingError(f"{_LABEL[name]}必须是整数", status=422)
            if not low <= value <= high:
                raise AccountingError(f"{_LABEL[name]}必须在 {low} 到 {high} 之间", status=422)
        with self.db.transaction(actor=actor, write=True, write_scope="global") as conn:
            self.access.require_manager(conn, actor)
            current = _row_to_quota(
                conn.execute(
                    "SELECT * FROM firm_quotas WHERE firm_id = ?", (actor.firm_id,)
                ).fetchone()
            )
            merged = {**current.as_dict(), **values}
            stamp = _now()
            conn.execute(
                """INSERT INTO firm_quotas(firm_id, import_rows_per_batch,
                       async_jobs_per_day, storage_bytes, concurrent_requests,
                       updated_by, updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(firm_id) DO UPDATE SET
                       import_rows_per_batch=excluded.import_rows_per_batch,
                       async_jobs_per_day=excluded.async_jobs_per_day,
                       storage_bytes=excluded.storage_bytes,
                       concurrent_requests=excluded.concurrent_requests,
                       updated_by=excluded.updated_by,
                       updated_at=excluded.updated_at""",
                (
                    actor.firm_id,
                    merged["import_rows_per_batch"],
                    merged["async_jobs_per_day"],
                    merged["storage_bytes"],
                    merged["concurrent_requests"],
                    actor.user_id,
                    stamp,
                ),
            )
            conn.execute(
                "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,"
                "created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    actor.firm_id,
                    None,
                    actor.user_id,
                    "quotas.configured",
                    ",".join(sorted(values)),
                    json.dumps(values, ensure_ascii=False, sort_keys=True),
                    stamp,
                ),
            )
        return Quota(**merged)

    # -- enforcement ---------------------------------------------------
    def ensure_import_rows(self, conn, actor: Actor, rows: int) -> Quota:
        quota = self._quota_on(conn, actor)
        if rows > quota.import_rows_per_batch:
            self._refuse(actor, "import_rows_per_batch", rows, quota.import_rows_per_batch)
            raise AccountingError(
                f"单次导入 {rows} 行，超过本单位上限 {quota.import_rows_per_batch} 行；"
                "请拆分文件后重试",
                status=_STATUS["import_rows_per_batch"],
            )
        return quota

    def ensure_daily_jobs(self, conn, actor: Actor, queued_today: int, adding: int = 1) -> Quota:
        quota = self._quota_on(conn, actor)
        if queued_today + adding > quota.async_jobs_per_day:
            self._refuse(
                actor, "async_jobs_per_day", queued_today + adding, quota.async_jobs_per_day
            )
            raise AccountingError(
                f"今日已入队 {queued_today} 个异步任务，上限 {quota.async_jobs_per_day} 个；"
                "请明日再试或申请提高配额",
                status=_STATUS["async_jobs_per_day"],
            )
        return quota

    def ensure_storage(self, conn, actor: Actor, used_bytes: int, adding: int) -> Quota:
        quota = self._quota_on(conn, actor)
        if used_bytes + adding > quota.storage_bytes:
            self._refuse(actor, "storage_bytes", used_bytes + adding, quota.storage_bytes)
            raise AccountingError(
                f"原件容量已用 {used_bytes} 字节，本次新增 {adding} 字节将超过上限 "
                f"{quota.storage_bytes} 字节；请清理历史原件或申请扩容",
                status=_STATUS["storage_bytes"],
            )
        return quota

    def ensure_concurrent(self, conn, actor: Actor, in_flight: int, adding: int = 1) -> Quota:
        """在途并发任务守卫。

        接在异步入队入口：`in_flight` 是该企业当前排队/运行中的任务数，
        超限即 429，绝不先落库再回滚（避免半批数据）。
        """
        quota = self._quota_on(conn, actor)
        if in_flight + adding > quota.concurrent_requests:
            self._refuse(
                actor, "concurrent_requests", in_flight + adding, quota.concurrent_requests
            )
            raise AccountingError(
                f"本单位当前在途任务 {in_flight} 个，上限 {quota.concurrent_requests} 个；"
                "请等待已有任务完成或申请提高并发配额",
                status=_STATUS["concurrent_requests"],
            )
        return quota

    # -- internals -----------------------------------------------------
    def _quota_on(self, conn, actor: Actor) -> Quota:
        row = conn.execute(
            "SELECT * FROM firm_quotas WHERE firm_id = ?", (actor.firm_id,)
        ).fetchone()
        return _row_to_quota(row)

    @staticmethod
    def _refuse(actor: Actor, limit: str, requested: int, ceiling: int) -> None:
        """Refusals are logged, not written: enforcement runs inside the caller's
        transaction, and raising rolls that transaction back — an audit INSERT
        made here would vanish with it. The log line is the metric; persistent
        evidence of a refusal belongs in the caller's own audit trail."""
        logger.warning(
            "quota exceeded: %s requested=%s limit=%s firm=%s actor=%s",
            limit,
            requested,
            ceiling,
            actor.firm_id,
            actor.user_id,
        )
