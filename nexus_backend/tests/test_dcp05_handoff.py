"""DCP05：岗位与指派分开，指派前校验资格，版本冲突说清楚，重复指派幂等。"""

import json
from types import SimpleNamespace

import pytest

from app.accounting.domain import AccountingError, Actor
from app.accounting.directory import BookMemberGrant, FirmMemberGrant
from app.handoff.service import HandoffService
from test_business import business  # noqa: F401
from test_tax_workpaper import invoices, profile, row  # noqa: F401
from test_tax_workpaper import tax as tax_fixture  # noqa: F401


@pytest.fixture
def handoff(tax_fixture):
    tax_fixture.handoff = HandoffService(tax_fixture.db)
    return tax_fixture


def reviewer(case, email="reviewer@example.com", *, firm_role="reviewer", book=True):
    name = email.split("@")[0]
    case.directory.register_identity(SimpleNamespace(user_id=name, display_name=name, email=email))
    member = case.directory.grant_firm_member(
        case.manager, FirmMemberGrant(email=email, role=firm_role)
    )
    if book:
        case.directory.grant_book_member(
            case.manager, case.book["id"], member["user_id"], BookMemberGrant(role="reviewer")
        )
    return Actor(member["user_id"], case.manager.firm_id)


def draft(case):
    case.tax.set_profile(case.manager, case.book["id"], profile())
    invoices(case, [row("output", 100_000, 3_000)])
    return case.tax.generate(case.actor_accountant, case.book["id"], "2026-10")


def test_queue_lists_draft_workpapers_with_role_and_unassigned_label(handoff):
    paper = draft(handoff)
    view = handoff.handoff.queue(handoff.manager)
    item = next(entry for entry in view["items"] if entry["source"]["id"] == paper["id"])
    assert item["source"]["kind"] == "tax_workpaper"
    assert item["source"]["version"] == 1
    assert len(item["source"]["fingerprint"]) == 64
    assert item["role"] == "reviewer"
    assert item["assignee_id"] is None
    assert item["label"] == "待指派"
    assert item["next_action"]
    assert view["pending"] >= 1


def test_assign_requires_a_firm_reviewer_with_book_access(handoff):
    paper = draft(handoff)
    outsider = reviewer(handoff, email="finance@example.com", firm_role="operator", book=False)
    handoff.directory.grant_book_member(
        handoff.manager,
        handoff.book["id"],
        outsider.user_id,
        BookMemberGrant(role="editor"),
    )
    with pytest.raises(AccountingError) as error:
        handoff.handoff.assign(handoff.manager, paper["id"], 1, outsider.user_id, None, "指派复核")
    assert error.value.status == 403
    assert "不会授予权限" in error.value.detail

    unbound = reviewer(handoff, email="reviewer2@example.com", book=False)
    with pytest.raises(AccountingError) as error:
        handoff.handoff.assign(handoff.manager, paper["id"], 1, unbound.user_id, None, "指派复核")
    assert error.value.status == 403


def test_maker_cannot_be_handed_their_own_workpaper(handoff):
    paper = draft(handoff)
    with pytest.raises(AccountingError) as error:
        handoff.handoff.assign(
            handoff.manager, paper["id"], 1, handoff.actor_accountant.user_id, None, "自己复核"
        )
    assert error.value.status == 409
    assert "制单人" in error.value.detail


def test_stale_version_reports_current_version_and_fingerprint(handoff):
    paper = draft(handoff)
    target = reviewer(handoff)
    # 调用者手里的版本比服务端新：同样拒绝，并回报真实版本与指纹。
    with pytest.raises(AccountingError) as error:
        handoff.handoff.assign(handoff.manager, paper["id"], 2, target.user_id, None, "过期版本")
    assert error.value.status == 409
    detail = json.loads(error.value.detail)
    assert detail["current_version"] == 1
    assert len(detail["current_fingerprint"]) == 64
    assert detail["refresh"]


def test_reassignment_supersedes_and_repeat_is_idempotent(handoff):
    paper = draft(handoff)
    first = reviewer(handoff, email="r1@example.com")
    second = reviewer(handoff, email="r2@example.com")
    one = handoff.handoff.assign(
        handoff.manager, paper["id"], 1, first.user_id, "2026-11-10", "先给甲"
    )
    again = handoff.handoff.assign(
        handoff.manager, paper["id"], 1, first.user_id, "2026-11-10", "先给甲"
    )
    assert again["id"] == one["id"]
    assert one["status"] == "assigned"
    other = handoff.handoff.assign(handoff.manager, paper["id"], 1, second.user_id, None, "改给乙")
    assert other["id"] != one["id"]
    view = handoff.handoff.queue(handoff.manager)
    item = next(entry for entry in view["items"] if entry["source"]["id"] == paper["id"])
    assert item["assignee_id"] == second.user_id
    assert item["status"] == "assigned"


def test_assign_without_book_access_is_not_found(handoff):
    paper = draft(handoff)
    stranger = reviewer(handoff, email="stranger@example.com", book=False)
    with pytest.raises(AccountingError) as error:
        handoff.handoff.assign(stranger, paper["id"], 1, stranger.user_id, None, "越权指派")
    assert error.value.status == 404


def test_candidates_only_include_eligible_reviewers(handoff):
    eligible = reviewer(handoff, email="cand1@example.com")
    reviewer(handoff, email="cand2@example.com", book=False)
    view = handoff.handoff.candidates(handoff.manager, handoff.book["id"], "reviewer")
    ids = {item["user_id"] for item in view["items"]}
    assert eligible.user_id in ids
    assert handoff.actor_accountant.user_id in ids
    assert all(item["firm_role"] in {"owner", "admin", "reviewer"} for item in view["items"])
