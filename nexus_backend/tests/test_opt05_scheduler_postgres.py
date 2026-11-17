"""OPT05-D: the scheduler is idempotent under concurrent PG scanners."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from app.scheduler.service import PlanWrite, SchedulerService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 管理员及受限运行测试连接")


def test_pg_concurrent_tick_creates_one_trigger_without_side_effects(business_case):
    case = business_case
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("DELETE FROM accounting.fsc_schedule_reminders")
        conn.execute("DELETE FROM accounting.fsc_schedule_triggers")
        conn.execute("DELETE FROM accounting.fsc_schedule_plans")
    scheduler = SchedulerService(case.db)
    plan = scheduler.create_plan(
        case.owner,
        PlanWrite(kind="periodic_accrual", rule={"day": 1, "hour": 8, "minute": 0}, enabled=True),
    )
    assert scheduler.tick(datetime.fromisoformat(plan["next_run_at"]))["triggers"] in (0, 1)
    # reset to a fresh plan for a clean concurrent race
    plan = scheduler.create_plan(
        case.owner,
        PlanWrite(kind="periodic_accrual", rule={"day": 2, "hour": 8, "minute": 0}, enabled=True),
    )
    now = datetime.fromisoformat(plan["next_run_at"])
    with case.db.transaction(actor=case.owner) as conn:
        before = tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("drafts", "vouchers", "sources")
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: scheduler.tick(now), range(2)))
    assert sum(result["triggers"] for result in results) == 1
    assert sum(result["skipped"] for result in results) == 1
    with case.db.transaction(actor=case.owner) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fsc_schedule_triggers WHERE plan_id=?", (plan["id"],)).fetchone()[0] == 1
        after = tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("drafts", "vouchers", "sources")
        )
    assert after == before
