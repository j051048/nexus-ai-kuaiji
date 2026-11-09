"""Tasks 01–03: an authorized task must identify a real, current work surface."""

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate
from app.delivery.followups import DeliveryFollowupService
from app.delivery.followup_models import DeliveryGrantRequest
from test_delivery_followups import case, freeze, grant  # noqa: F401
from test_monthly_delivery_package import ready_case  # noqa: F401
from test_p0_close_dependencies import close_case  # noqa: F401
from test_business import business  # noqa: F401


def test_delivery_feed_pages_reach_later_companies_without_duplicates(business):
    c = business
    expected = {c.book["id"], c.other_book["id"]}
    for index in range(10):
        book = c.ledger.create_book(
            c.manager,
            BookCreate(
                name=f"CI同名账套-{index // 2}", period="2026-10", company_id=c.company["id"]
            ),
        )
        expected.add(book["id"])
    service = DeliveryFollowupService(c.db)
    offset, seen = 0, []
    for _ in range(20):
        result = service.followups(c.manager, limit=3, offset=offset)
        assert result["pagination"]["offset"] == offset
        assert result["count"] <= 3
        seen.extend(row["book_id"] for row in result["items"])
        offset = result["pagination"]["next_offset"]
        if offset is None:
            break
    assert set(seen) == expected
    assert len(seen) == len(expected)


def test_delivery_feed_paging_keeps_authorization_and_validates_bounds(business):
    c = business
    service = DeliveryFollowupService(c.db)
    for value in (-1, True, 1.5, 1_000_001):
        with pytest.raises(AccountingError):
            service.followups(c.manager, limit=1, offset=value)
    seen, offset = [], 0
    for _ in range(5):
        result = service.followups(c.actors["accountant"], limit=1, offset=offset)
        seen.extend(row["book_id"] for row in result["items"])
        offset = result["pagination"]["next_offset"]
        if offset is None:
            break
    assert seen == [c.book["id"]]


def test_followup_feed_binds_actor_company_period_and_current_package(case):
    package = freeze(case)
    feed = case.followups.followups(case.reviewer)
    assert feed["scope"] == {"firm_id": case.reviewer.firm_id, "user_id": case.reviewer.user_id}
    row = next(x for x in feed["items"] if x["book_id"] == case.book["id"])
    state = case.followups.status(case.reviewer, case.book["id"], "2026-10")
    assert row["company_id"] == case.book["company_id"]
    assert row["source_fingerprint"] == state["source_fingerprint"]
    assert state["event"]["manifest_sha256"] == package["manifest_sha256"]
    assert state["action"]["kind"] == "await_customer_signature"
    assert state["action"]["label"] == "查看签收进度"
    assert state["action"]["can_act"] is False


def test_recipient_change_invalidates_opening_target_and_does_not_complete_task(case):
    freeze(case)
    before = case.followups.status(case.reviewer, case.book["id"], "2026-10")
    grant(case, recipient="company_admin")
    after = case.followups.status(case.reviewer, case.book["id"], "2026-10")
    assert after["source_fingerprint"] != before["source_fingerprint"]
    assert after["action"]["kind"] == "fix_recipient"
    assert after["signature"]["state"] == "awaiting"
    assert after["followup"]["needed"] is True


def test_followup_cannot_resolve_a_foreign_or_missing_book(case):
    with pytest.raises(AccountingError) as error:
        case.followups.status(case.reviewer, "nonexistent-book", "2026-10")
    assert error.value.status in {403, 404}


def test_stale_recipient_form_does_not_overwrite_another_reviewers_choice(case):
    freeze(case)
    before = case.followups.status(case.reviewer, case.book["id"], "2026-10")
    grant(case, recipient="company_admin")
    with pytest.raises(AccountingError) as conflict:
        case.followups.configure(
            case.reviewer,
            case.book["id"],
            DeliveryGrantRequest(
                period="2026-10",
                recipient_user_id="finance",
                reason="旧表单不能覆盖新配置",
                expected_source_fingerprint=before["source_fingerprint"],
            ),
        )
    assert conflict.value.status == 409
    assert (
        case.followups.status(case.reviewer, case.book["id"], "2026-10")["recipient"]["user_id"]
        == "company_admin"
    )
