"""NEXT04: batch, unknown-notification and delivery projections share one handed-off contract."""

import json
from types import SimpleNamespace

import pytest

from app.accounting.domain import AccountingError, Actor
from app.accounting.directory import BookMemberGrant, FirmMemberGrant
from app.handoff.service import HandoffService
from test_business import business  # noqa: F401


@pytest.fixture
def handoff(business):  # noqa: F811
    business.handoff = HandoffService(business.db)
    return business


def failed_batch(case, *, company_id=None, book_id=None, attempt=2):
    company_id = company_id or case.company["id"]
    book_id = book_id or case.book["id"]
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "INSERT INTO batch_operations(id,firm_id,kind,status,idempotency_key,request_json,"
            "total,succeeded,skipped,failed,created_by,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "batch-n04",
                case.manager.firm_id,
                "monthly_open",
                "attention",
                "n04",
                json.dumps({"period": "2026-10", "companies": [company_id]}),
                1,
                0,
                0,
                1,
                case.manager.user_id,
                "2026-11-03T00:00:00+00:00",
                "2026-11-03T00:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO batch_operation_items(id,batch_id,firm_id,company_id,company_name,"
            "sequence_no,status,attempt,max_attempts,available_at,result_json,error_code,"
            "error_detail,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "item-n04",
                "batch-n04",
                case.manager.firm_id,
                company_id,
                "第一客户企业",
                1,
                "failed",
                attempt,
                3,
                "2026-11-03T00:00:00+00:00",
                json.dumps({"book_id": book_id}),
                "material_missing",
                "缺少银行流水",
                "2026-11-03T00:00:00+00:00",
                "2026-11-03T00:00:00+00:00",
            ),
        )
    return "item-n04"


def unknown_notification(case):
    payload = json.dumps(
        {
            "notification_id": "notif-n04",
            "firm_id": case.manager.firm_id,
            "company_id": case.company["id"],
            "book_id": case.book["id"],
            "period": "2026-10",
            "package_id": None,
            "package_version": None,
            "manifest_sha256": None,
            "recipient_user_id": None,
            "grant_id": None,
            "grant_version": 0,
            "restore_epoch": "",
            "kind": "missing_contact_followup",
            "reason": "test",
        },
        ensure_ascii=False,
    )
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "INSERT INTO delivery_notifications(id,firm_id,company_id,book_id,period,kind,"
            "dedupe_key,state,attempts,last_error,payload_json,created_by,created_at,updated_at,"
            "available_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "notif-n04",
                case.manager.firm_id,
                case.company["id"],
                case.book["id"],
                "2026-10",
                "missing_contact_followup",
                "n04-unknown",
                "pending",
                0,
                "",
                payload,
                case.manager.user_id,
                "2026-11-03T00:00:00+00:00",
                "2026-11-03T00:00:00+00:00",
                "",
            ),
        )
        conn.execute(
            "UPDATE delivery_notifications SET state='claimed',worker_id='w1',lease_token='l1',"
            "lease_until='2026-11-03T00:01:00+00:00',updated_at='2026-11-03T00:00:01+00:00' "
            "WHERE id='notif-n04'"
        )
        conn.execute(
            "UPDATE delivery_notifications SET state='sending',attempts=1,"
            "send_started_at='2026-11-03T00:00:02+00:00',"
            "updated_at='2026-11-03T00:00:02+00:00' WHERE id='notif-n04'"
        )
        conn.execute(
            "UPDATE delivery_notifications SET state='unknown',last_error='timeout',"
            "updated_at='2026-11-03T00:00:03+00:00' WHERE id='notif-n04'"
        )
    return "notif-n04"


def admin(case, email="admin-n04@example.test"):
    identity = SimpleNamespace(user_id="admin-n04", display_name="admin", email=email)
    case.directory.register_identity(identity)
    member = case.directory.grant_firm_member(
        case.manager, FirmMemberGrant(email=email, role="admin")
    )
    case.directory.grant_book_member(
        case.manager, case.book["id"], member["user_id"], BookMemberGrant(role="reviewer")
    )
    return Actor(member["user_id"], case.manager.firm_id)


def test_batch_failure_is_projected_without_copying_business_state(handoff):
    failed_batch(handoff)
    view = handoff.handoff.queue(handoff.manager)
    item = next(entry for entry in view["items"] if entry["source"]["id"] == "item-n04")
    assert item["source"]["kind"] == "batch_item"
    assert item["source"]["version"] == 2
    assert item["role"] == "ops"
    assert item["scope"]["book_id"] == handoff.book["id"]
    assert item["scope"]["batch_id"] == "batch-n04"
    assert item["reason"] == "缺少银行流水"
    assert item["assignee_id"] is None


def test_ops_assignment_requires_current_firm_manager_and_book_read(handoff):
    failed_batch(handoff)
    operator = Actor(handoff.actors["finance"].user_id, handoff.manager.firm_id)
    with pytest.raises(AccountingError) as error:
        handoff.handoff.assign(
            operator, "item-n04", 2, handoff.manager.user_id, None, "交办失败项", kind="batch_item"
        )
    assert error.value.status == 403 or error.value.status == 404
    target = admin(handoff)
    result = handoff.handoff.assign(
        handoff.manager, "item-n04", 2, target.user_id, "2026-11-10", "交办批量失败", kind="batch_item"
    )
    assert result["source"]["kind"] == "batch_item"
    assert result["role"] == "ops"
    assert result["assignee_id"] == target.user_id


def test_unknown_notification_handoff_does_not_authorise_resend(handoff):
    unknown_notification(handoff)
    view = handoff.handoff.queue(handoff.manager)
    item = next(entry for entry in view["items"] if entry["source"]["id"] == "notif-n04")
    assert item["source"]["kind"] == "notification_investigation"
    assert item["source"]["version"] == 2
    assert item["role"] == "ops"
    assert "不自动重发" in item["next_action"]
    result = handoff.handoff.assign(
        handoff.manager, "notif-n04", 2, handoff.manager.user_id, None, "人工核查发送结果", kind="notification_investigation"
    )
    assert result["assignee_id"] == handoff.manager.user_id
    assert result["source"]["kind"] == "notification_investigation"


def test_stale_and_foreign_sources_fail_closed(handoff):
    failed_batch(handoff)
    target = admin(handoff)
    with pytest.raises(AccountingError) as stale:
        handoff.handoff.assign(handoff.manager, "item-n04", 1, target.user_id, None, "旧版本", kind="batch_item")
    assert stale.value.status == 409
    detail = json.loads(stale.value.detail)
    assert detail["current_version"] == 2
    assert detail["refresh"]
    with pytest.raises(AccountingError) as missing:
        handoff.handoff.assign(
            handoff.manager, "item-n04", 2, target.user_id, None, "xxx", kind="unknown"
        )
    assert missing.value.status == 422


from test_p0_close_dependencies import close_case  # noqa: E402,F401
from test_monthly_delivery_package import freeze, ready_case  # noqa: E402,F401


def test_delivery_package_projection_can_be_handed_to_delivery_staff(ready_case):
    c = ready_case
    package = freeze(c)
    handoff = HandoffService(c.db)
    queued = handoff.queue(c.manager)["items"]
    source = next(
        row
        for row in queued
        if row["source"]["kind"] == "delivery_package" and row["source"]["id"] == package["id"]
    )
    assert source["role"] == "delivery"
    assert source["source"]["version"] == package["version"]
    assigned = handoff.assign(
        c.manager,
        package["id"],
        package["version"],
        c.reviewer.user_id,
        None,
        "NEXT04 交付包指派",
        kind="delivery_package",
    )
    assert assigned["source"]["kind"] == "delivery_package"
    assert assigned["assignee_id"] == c.reviewer.user_id

    # The assignee is a reviewer, not a firm manager. Assignment must make the
    # delivery handoff visible to the person who is expected to act on it.
    reviewer_view = handoff.queue(c.reviewer)["items"]
    visible = next(
        row
        for row in reviewer_view
        if row["source"]["kind"] == "delivery_package" and row["source"]["id"] == package["id"]
    )
    assert visible["assignee_id"] == c.reviewer.user_id
    assert visible["role"] == "delivery"


def test_delivery_assignee_reviewer_sees_the_assigned_handoff(ready_case):
    c = ready_case
    package = freeze(c)
    handoff = HandoffService(c.db)
    identity = SimpleNamespace(
        user_id="reviewer-n04", display_name="交付复核", email="reviewer-n04@example.test"
    )
    c.directory.register_identity(identity)
    member = c.directory.grant_firm_member(
        c.manager, FirmMemberGrant(email=identity.email, role="reviewer")
    )
    c.directory.grant_book_member(
        c.manager, c.book["id"], member["user_id"], BookMemberGrant(role="reviewer")
    )
    assignee = Actor(member["user_id"], c.manager.firm_id)
    handoff.assign(
        c.manager,
        package["id"],
        package["version"],
        assignee.user_id,
        None,
        "NEXT04 交付包指派给复核人",
        kind="delivery_package",
    )
    visible = [
        row
        for row in handoff.queue(assignee)["items"]
        if row["source"]["kind"] == "delivery_package" and row["source"]["id"] == package["id"]
    ]
    assert len(visible) == 1
    assert visible[0]["assignee_id"] == assignee.user_id
    assert visible[0]["role"] == "delivery"
