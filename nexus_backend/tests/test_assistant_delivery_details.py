"""Sixth batch: authoritative account detail, never model-invented balances."""

import copy
import json

import pytest

from test_assistant_delivery_context import project, enqueue
from test_ak04_assistant_execution import execution_case
from test_intelligence_workflow import ai_case
from test_model_cost_workflow import cost_case, cost_rows
from test_business import business
from test_b04_pilot import setup_b03, configure_payable
from test_p0_close_dependencies import close_case
from app.subledger.models import BankAccountCreate, BankAdjustment
from app.subledger.service import SubledgerService


def configured(c):
    opening, _ = setup_b03(c)
    configure_payable(c, opening)
    sub = SubledgerService(c.db)
    bank = sub.create_bank_account(
        c.manager,
        BankAccountCreate(
            book_id=c.book["id"],
            name="虚构待补单银行",
            ledger_account_id=opening["bank"]["id"],
            ledger_account_version=1,
            reason="离线诊断验证",
        ),
    )
    return sub, bank


def test_current_account_details_without_preflight(business):
    c = business
    _, bank = configured(c)
    view = project(c)
    source = next(s for s in view["sources"] if s["kind"] == "reconciliation")
    assert source["company_id"] == c.company["id"] and source["id"] == c.book["id"]
    assert source["period"] == "2026-10"
    control = next(i for i in view["issues"] if i["code"] == "control_account_difference")
    assert "2202" in control["reason"] and "应付账款" in control["reason"]
    assert "-10000.00" in control["reason"] and "借方为正" in control["reason"]
    assert "账套会计" == control["owner_role"]
    assert "正式月结" in control["next_action"]
    bank_issue = next(i for i in view["issues"] if i["code"] == "bank_coverage_missing")
    assert bank["name"] in bank_issue["reason"] and "0.00" not in bank_issue["reason"]
    assert "差额" not in bank_issue["reason"] and "独立核验" in bank_issue["next_action"]


def test_fresh_details_replace_stale_preflight(close_case):
    c = close_case
    c.sub.add_bank_adjustment(
        c.manager,
        BankAdjustment(
            book_id=c.book["id"],
            bank_account_id=c.bank_account["id"],
            period="2026-10",
            kind="error",
            amount="0.30",
            explanation="虚构银行差异",
        ),
    )
    view = project(c)
    assert "preflight_stale" in {i["code"] for i in view["issues"]}
    assert any(i["code"] == "bank_adjustments" and "0.30" in i["reason"] for i in view["issues"])
    assert next(s for s in view["sources"] if s["kind"] == "reconciliation")["inputs_fingerprint"]


def test_details_only_for_authorized_accountants(execution_case):
    c = execution_case
    configured(c)
    view = project(c, c.actors["finance"])
    assert not any(s["kind"] == "reconciliation" for s in view["sources"])
    actor, conv, turn = enqueue(c, c.actors["accountant"], book_ids=[c.book["id"]])
    c.chat_worker.run_once()
    result = c.chat_service.turn(actor, conv["id"], turn["id"])["turn"]
    assert result["status"] == "succeeded"
    assert "2202" in result["reply"]["answer"] and "虚构待补单银行" in result["reply"]["answer"]
    assert c.other_book["id"] not in json.dumps(c.calls)
    assert len(c.calls) == 1 and result["reply"]["proposals"] == []


def test_bank_change_after_send_invalidates_detail_without_resend(execution_case):
    c = execution_case
    sub, bank = configured(c)
    actor, conv, turn = enqueue(c, book_ids=[c.book["id"]])

    def change():
        sub.add_bank_adjustment(
            c.manager,
            BankAdjustment(
                book_id=c.book["id"],
                bank_account_id=bank["id"],
                period="2026-10",
                kind="error",
                amount="0.30",
                explanation="发送后事实改变",
            ),
        )

    c.hook = change
    c.chat_worker.run_once()
    result = c.chat_service.turn(actor, conv["id"], turn["id"])["turn"]
    assert result["status"] == "stale" and result["reply"] is None
    c.chat_worker.run_once()
    assert len(c.calls) == 1 and cost_rows(c)[0]["state"] == "estimated"


def test_no_opening_is_guidance_not_zero_balances(execution_case):
    view = project(execution_case)
    issue = next(i for i in view["issues"] if i["code"] == "reconciliation_unavailable")
    assert "期初" in issue["reason"] and "0.00" not in issue["reason"]


@pytest.mark.parametrize("value", [True, -0.5, "100", None])
def test_invalid_numeric_detail_fails_closed(business, value):
    from app.assistant.delivery_details import validate_details

    c = business
    configured(c)
    source = next(s for s in project(c)["sources"] if s["kind"] == "reconciliation")
    source = copy.deepcopy(source)
    source["control"]["rows"][0]["difference_cents"] = value
    with pytest.raises(ValueError):
        validate_details(source, c.company["id"], c.book["id"], "2026-10")


def test_two_bank_differences_are_not_netted(close_case):
    from app.assistant.delivery_details import detail_issues

    c = close_case
    source = next(s for s in project(c)["sources"] if s["kind"] == "reconciliation")
    first = source["bank"]["rows"][0]
    assert first["coverage"] and first["statement_source_matches"]
    second = copy.deepcopy(first)
    second["account_id"], second["name"] = "second-bank", "合成第二银行"
    for row, difference in ((first, 30), (second, -30)):
        row["passed"] = False
        row["book_cents"] = row["closing_cents"] + difference
        row["book_difference_cents"] = difference
    source["bank"]["rows"] = [first, second]
    source["bank"]["passed"] = False
    issues = [i for i in detail_issues(source) if i["code"] == "bank_account_difference"]
    assert len(issues) == 2
    assert "账面−对账单 0.30 元" in issues[0]["reason"]
    assert "账面−对账单 -0.30 元" in issues[1]["reason"]
    # Same amounts with missing original proof are not published as verified differences.
    first["statement_source_matches"] = False
    issue = next(i for i in detail_issues(source) if i["code"] == "bank_account_difference")
    assert "原件绑定" in issue["reason"] and "0.30" not in issue["reason"]


def test_detail_omission_is_explicit_and_full_source_bound(close_case, monkeypatch):
    from app.assistant import delivery_details

    full = project(close_case)
    monkeypatch.setattr(delivery_details, "LIMIT", 0)
    limited = project(close_case)
    assert any(i["code"] == "control_omitted" for i in limited["issues"])
    assert any(i["code"] == "bank_omitted" for i in limited["issues"])
    sources = [
        next(s for s in v["sources"] if s["kind"] == "reconciliation") for v in (full, limited)
    ]
    assert sources[0]["inputs_fingerprint"] == sources[1]["inputs_fingerprint"]
    assert sources[1]["bank"]["omitted_rows"] == 1
    assert limited["can_deliver"] is None


@pytest.mark.parametrize("field", ["company_id", "id", "period"])
def test_detail_scope_cannot_be_rebound(business, field):
    from app.assistant.delivery_details import validate_details

    c = business
    configured(c)
    source = next(s for s in project(c)["sources"] if s["kind"] == "reconciliation")
    source[field] = "foreign"
    with pytest.raises(ValueError):
        validate_details(source, c.company["id"], c.book["id"], "2026-10")


def test_permission_denial_is_not_relabelled_missing_opening(execution_case, monkeypatch):
    from app.posting.service import PostingService
    from app.accounting.domain import AccountingError

    def denied(*args, **kwargs):
        raise AccountingError("当前授权已撤销", 403)

    monkeypatch.setattr(PostingService, "trial_balance_in_transaction", denied)
    with pytest.raises(AccountingError) as error:
        project(execution_case)
    assert error.value.status == 403
