from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.scheduler.service import PlanWrite, SchedulerService
from test_business import business  # noqa: F401


def _plan(kind="periodic_accrual", **rule):
    base = {"day": 1, "hour": 8, "minute": 0}
    base.update(rule)
    return PlanWrite(kind=kind, rule=base, enabled=True)


def test_tick_is_idempotent_and_creates_one_reminder(business):
    scheduler = SchedulerService(business.db)
    plan = scheduler.create_plan(business.manager, _plan(minute=0))
    now = datetime.fromisoformat(plan["next_run_at"])
    assert scheduler.tick(now)["triggers"] == 1
    assert scheduler.tick(now)["triggers"] == 0
    triggers = scheduler.triggers(business.manager, plan["id"])
    assert len(triggers) == 1
    assert triggers[0]["status"] == "pending"
    assert triggers[0]["reminder"]["kind"] == "periodic_accrual"


def test_disabled_plan_does_not_trigger(business):
    scheduler = SchedulerService(business.db)
    plan = scheduler.create_plan(business.manager, _plan())
    scheduler.set_enabled(business.manager, plan["id"], False)
    assert scheduler.tick(datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc))["triggers"] == 0
    assert scheduler.triggers(business.manager, plan["id"]) == []


def test_monthly_open_waits_for_confirmation_before_worker_can_claim(business):
    scheduler = SchedulerService(business.db)
    plan = scheduler.create_plan(
        business.manager,
        PlanWrite(
            kind="monthly_open",
            company_id=business.book["company_id"],
            rule={"day": 1, "hour": 8, "minute": 0},
            enabled=True,
        ),
    )
    now = datetime.fromisoformat(plan["next_run_at"])
    assert scheduler.tick(now)["triggers"] == 1
    trigger = scheduler.triggers(business.manager, plan["id"])[0]
    assert trigger["batch_id"]
    with business.db.transaction(actor=business.manager) as conn:
        item = conn.execute(
            "SELECT available_at FROM batch_operation_items WHERE batch_id=?",
            (trigger["batch_id"],),
        ).fetchone()
    assert item["available_at"] > "2027-01-01"
    scheduler.confirm(business.manager, trigger["id"])
    with business.db.transaction(actor=business.manager) as conn:
        item = conn.execute(
            "SELECT available_at FROM batch_operation_items WHERE batch_id=?",
            (trigger["batch_id"],),
        ).fetchone()
    assert item["available_at"] <= now.isoformat()


def test_employee_cannot_create_plan(business):
    scheduler = SchedulerService(business.db)
    with pytest.raises(Exception) as error:
        scheduler.create_plan(business.actors["employee"], _plan())
    assert getattr(error.value, "status", None) in (403, 404)


def test_plan_update_is_audited_and_rule_rejects_sql(business):
    scheduler = SchedulerService(business.db)
    with pytest.raises(Exception) as error:
        scheduler.create_plan(business.manager, _plan(sql="SELECT 1"))
    assert getattr(error.value, "status", None) == 422
    plan = scheduler.create_plan(business.manager, _plan())
    scheduler.set_enabled(business.manager, plan["id"], False)
    with business.db.transaction(actor=business.manager) as conn:
        rows = conn.execute(
            "SELECT action FROM audit_events WHERE action LIKE 'scheduler.plan.%' AND object_id=? ORDER BY created_at",
            (plan["id"],),
        ).fetchall()
    assert [row["action"] for row in rows] == ["scheduler.plan.create", "scheduler.plan.disable"]


def test_scheduler_loop_runs_one_tick_and_stops():
    from app.scheduler.runner import SchedulerLoop

    class Fake:
        calls = 0

        def tick(self):
            self.calls += 1
            return {"triggers": 0}

    service = Fake()
    loop = SchedulerLoop(service, interval_seconds=1)
    assert loop.run_once() == {"triggers": 0}
    loop.start()
    loop.stop()
    assert service.calls == 1


def test_missed_schedule_only_catches_up_latest_occurrence(business):
    scheduler = SchedulerService(business.db)
    plan = scheduler.create_plan(business.manager, _plan(day=1))
    old = "2026-09-01T00:00:00+00:00"
    with business.db.transaction(write=True, actor=business.manager) as conn:
        conn.execute("UPDATE fsc_schedule_plans SET next_run_at=? WHERE id=?", (old, plan["id"]))
    now = datetime(2026, 11, 17, tzinfo=timezone.utc)
    assert scheduler.tick(now)["triggers"] == 1
    triggers = scheduler.triggers(business.manager, plan["id"])
    assert len(triggers) == 1
    assert triggers[0]["scheduled_for"] == "2026-11-01T00:00:00+00:00"
    with business.db.transaction(actor=business.manager) as conn:
        assert (
            conn.execute(
                "SELECT next_run_at FROM fsc_schedule_plans WHERE id=?", (plan["id"],)
            ).fetchone()["next_run_at"]
            > now.isoformat()
        )
    assert scheduler.tick(now)["triggers"] == 0
