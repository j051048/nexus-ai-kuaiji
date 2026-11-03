"""Real failure projections and immutable assignment changes, without external sends."""

import json

import pytest

from app.accounting.domain import AccountingError
from test_business import business  # noqa: F401
from test_next04_handoff_adapters import handoff, failed_batch, unknown_notification  # noqa: F401


def projected(c, source_id):
    return next(x for x in c.handoff.queue(c.manager)["items"] if x["source"]["id"] == source_id)


def test_failed_open_without_result_stays_visible_without_guessing_book(handoff):
    failed_batch(handoff)
    with handoff.db.transaction(write=True, actor=handoff.manager) as conn:
        conn.execute("UPDATE batch_operation_items SET result_json='{}' WHERE id='item-n04'")
    item = projected(handoff, "item-n04")
    assert item["scope"]["book_id"] is None
    assert item["scope"]["company_id"] == handoff.company["id"]
    assert item["can_assign"] is False
    assert item["scope"]["batch_id"] == "batch-n04"
    assert item["assignment_blocker"]


def test_failed_export_uses_frozen_plan_not_empty_result(handoff):
    failed_batch(handoff)
    plan = {
        "period": "2026-10",
        "companies": [{"company_id": handoff.company["id"], "book_id": handoff.book["id"]}],
    }
    with handoff.db.transaction(write=True, actor=handoff.manager) as conn:
        conn.execute(
            "UPDATE batch_operations SET kind='review_export',request_json=? WHERE id='batch-n04'",
            (json.dumps(plan),),
        )
        conn.execute("UPDATE batch_operation_items SET result_json='{}' WHERE id='item-n04'")
    item = projected(handoff, "item-n04")
    assert item["scope"]["book_id"] == handoff.book["id"]
    assert item["can_assign"] is True
    assigned = handoff.handoff.assign(
        handoff.manager,
        "item-n04",
        2,
        handoff.manager.user_id,
        None,
        "核对导出失败",
        kind="batch_item",
    )
    assert assigned["scope"]["book_id"] == handoff.book["id"]


def test_unknown_batch_failure_guidance_never_suggests_retry(handoff):
    failed_batch(handoff)
    with handoff.db.transaction(write=True, actor=handoff.manager) as conn:
        conn.execute(
            "UPDATE batch_operation_items SET error_code='response_unknown' WHERE id='item-n04'"
        )
    assert "不自动重试" in projected(handoff, "item-n04")["next_action"]


def test_same_person_due_change_is_audited_and_identical_repeat_is_idempotent(handoff):
    unknown_notification(handoff)
    args = (handoff.manager, "notif-n04", 2, handoff.manager.user_id)
    first = handoff.handoff.assign(
        *args, "2026-11-10", "安排核查", kind="notification_investigation"
    )
    second = handoff.handoff.assign(
        *args, "2026-11-12", "延后核查", kind="notification_investigation"
    )
    assert second["id"] != first["id"]
    assert second["due_on"] == "2026-11-12"
    assert "不自动重发" in second["next_action"]
    repeat = handoff.handoff.assign(
        *args, "2026-11-12", "延后核查", kind="notification_investigation"
    )
    assert repeat["id"] == second["id"]
    with handoff.db.transaction(actor=handoff.manager) as conn:
        old = conn.execute("SELECT * FROM handoff_tasks WHERE id=?", (first["id"],)).fetchone()
        assert old["superseded_at"] and old["due_on"] == "2026-11-10"
        state = conn.execute(
            "SELECT state,attempts FROM delivery_notifications WHERE id='notif-n04'"
        ).fetchone()
        assert state["state"] == "unknown" and state["attempts"] == 1


def test_same_attempt_changed_source_rejects_stale_fingerprint(handoff):
    failed_batch(handoff)
    item = projected(handoff, "item-n04")
    with handoff.db.transaction(write=True, actor=handoff.manager) as conn:
        conn.execute(
            "UPDATE batch_operation_items SET updated_at='2026-11-03T00:00:04+00:00' WHERE id='item-n04'"
        )
    with pytest.raises(AccountingError) as error:
        handoff.handoff.assign(
            handoff.manager,
            "item-n04",
            2,
            handoff.manager.user_id,
            None,
            "旧依据核查",
            kind="batch_item",
            expected_fingerprint=item["source"]["fingerprint"],
        )
    assert error.value.status == 409


def test_exact_source_resolve_never_opens_latest(handoff):
    unknown_notification(handoff)
    item = projected(handoff, "notif-n04")
    assert (
        handoff.handoff.source(
            handoff.manager,
            "notification_investigation",
            "notif-n04",
            2,
            item["source"]["fingerprint"],
        )["source"]
        == item["source"]
    )
    with pytest.raises(AccountingError) as error:
        handoff.handoff.source(
            handoff.manager, "notification_investigation", "notif-n04", 2, "f" * 64
        )
    assert error.value.status == 409


def test_changed_source_keeps_assignment_compare_token_without_old_assignee(handoff):
    failed_batch(handoff)
    first = handoff.handoff.assign(
        handoff.manager,
        "item-n04",
        2,
        handoff.manager.user_id,
        None,
        "原记录核查",
        kind="batch_item",
    )
    with handoff.db.transaction(write=True, actor=handoff.manager) as conn:
        conn.execute(
            "UPDATE batch_operation_items SET updated_at='2026-11-03T00:00:05+00:00' WHERE id='item-n04'"
        )
    item = projected(handoff, "item-n04")
    assert item["assignee_id"] is None
    assert item["assignment_id"] == first["id"]
    second = handoff.handoff.assign(
        handoff.manager,
        "item-n04",
        2,
        handoff.manager.user_id,
        None,
        "依据更新重新确认",
        kind="batch_item",
        expected_fingerprint=item["source"]["fingerprint"],
        expected_assignment_id=item["assignment_id"],
    )
    assert second["id"] != first["id"]
