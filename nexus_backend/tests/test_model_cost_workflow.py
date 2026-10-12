"""Exact local model-cost controls with synthetic usage, never supplier invoices."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import json

import pytest
from fastapi.testclient import TestClient

from app.accounting.domain import Actor, AccountingError
from app.accounting.models import CompanyMemberGrant
from app.accounting.service import fingerprint
from app.intelligence.models import ProviderResult, ProviderUsage
from app.intelligence.provider import ProviderError
from app.jobs.requests import BatchRequest
from app.jobs.service import QueueService
from app.jobs.worker import QueueWorker
from app.operations.meter import BudgetError, MeterService
from app.operations.pricing import Pricing, Rate
from app.operations.requests import AcknowledgeRequest, PolicyRequest, ReconcileRequest
from app.operations.service import OperationsService, record_exception_snapshot
from app.main import create_app
from test_intelligence_workflow import FakeProvider, ai_case, create_event, extract, handoff, http_auth, http_create, invoice_document, propose


class CostClock:
    def __init__(self):
        self.value = datetime(2026, 10, 12, 1, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class UsageProvider(FakeProvider):
    """Explicit local fixture supplies reported token counts with fake OCR text."""
    def __init__(self, *args, usage=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usage = usage

    def extract_invoice(self, pages):
        result = super().extract_invoice(pages)
        return ProviderResult(result.data, result.provider, result.model, result.prompt_version, self.usage)

    def suggest_journal(self, context):
        result = super().suggest_journal(context)
        return ProviderResult(result.data, result.provider, result.model, result.prompt_version, self.usage)


@pytest.fixture
def cost_case(ai_case):
    case = ai_case
    case.clock = CostClock()
    case.provider = UsageProvider(usage=ProviderUsage(prompt_tokens=3, completion_tokens=4, total_tokens=7))
    case.intelligence.provider = case.provider
    case.pricing = Pricing(ocr=Rate("1.000000", "2.000000"), proposal=Rate("1.000000", "2.000000"))
    case.meter = MeterService(case.db, pricing=case.pricing, clock=case.clock)
    case.intelligence.meter = case.meter
    case.operations = OperationsService(case.db, case.meter, intelligence=case.intelligence)
    return case


def policy(case, quota_enabled=True, **values):
    current = case.operations.policy(case.manager, case.company["id"])
    payload = {"expected_version": current["version"], "quota_enabled": quota_enabled,
               "monthly_limit": "1.00", "ocr_reserve": "0.10", "proposal_reserve": "0.20", **values}
    return case.operations.policy(case.manager, case.company["id"], PolicyRequest(**payload))


def claimed_context(case, event=None, kind="ocr"):
    """Claim a real request but do not infer or send any evidence in this helper."""
    event = event or create_event(case)
    actor = case.actors["employee"]
    attachment = event["attachments"][0]
    with case.db.transaction(write=True, actor=actor) as conn:
        current = case.intelligence.business._event(conn, actor, event["id"])
        run, execute = case.intelligence._claim(conn, actor, current, kind, "meter-claim-" + uuid4().hex,
                                               fingerprint({"event": event["id"], "kind": kind}),
                                               case.provider.settings.vision_model, "synthetic-cost-test", attachment_id=attachment["id"])
        assert execute
    return case.meter.context(actor, run), event


def before_send(case, context):
    with case.db.transaction(write=True, actor=context.actor) as conn:
        context.before_send(conn)


def cost_rows(case):
    with case.db.transaction() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM model_costs ORDER BY created_at,id")]


def test_company_operations_visibility_and_budget_permission_do_not_grant_original_access(cost_case):
    case = cost_case
    event = create_event(case)
    assert any(company["id"] == case.company["id"] for company in case.operations.companies(case.manager)["companies"])
    finance = case.operations.companies(case.actors["finance"])["companies"]
    assert len(finance) == 1 and finance[0]["id"] == case.company["id"] and not finance[0]["can_manage_budget"]
    for role in ("employee", "approver", "accountant"):
        assert case.operations.companies(case.actors[role]) == {"companies": []}
        with pytest.raises(AccountingError):
            case.operations.overview(case.actors[role], case.company["id"])
    with pytest.raises(AccountingError):
        case.operations.policy(case.actors["finance"], case.company["id"], PolicyRequest(expected_version=0))
    with pytest.raises(AccountingError):
        case.intelligence.detail(case.manager, event["id"])
    view = case.operations.overview(case.manager, case.company["id"])
    assert event["id"] not in str(view) and event["attachments"][0]["id"] not in str(view)
    assert "test-only-not-a-secret" not in str(view)


def test_default_budget_is_off_and_policy_requires_current_version(cost_case):
    case = cost_case
    original = case.operations.policy(case.manager, case.company["id"])
    assert original["version"] == 0 and not original["quota_enabled"]
    enabled = policy(case)
    assert enabled["quota_enabled"] and enabled["monthly_limit"] == "1.00" and enabled["version"] == 1
    with pytest.raises(AccountingError) as stale:
        case.operations.policy(case.manager, case.company["id"], PolicyRequest(expected_version=0, failure_threshold=10))
    assert stale.value.status == 409
    assert case.operations.policy(case.manager, case.company["id"])["failure_threshold"] == 3


def test_reservation_and_usage_estimate_use_integer_micros_with_price_snapshot(cost_case):
    case = cost_case
    policy(case)
    context, _ = claimed_context(case)
    before_send(case, context)
    held = cost_rows(case)[0]
    assert held["state"] == "sent" and held["reserved_micro"] == 100_000
    assert held["estimated_micro"] is None
    context.finish(result=SimpleNamespace(usage=case.provider.usage), attempted=True)
    settled = cost_rows(case)[0]
    assert settled["state"] == "estimated" and settled["estimated_micro"] == 11
    assert settled["input_tokens"] == 3 and settled["output_tokens"] == 4
    assert json.loads(settled["pricing_json"])["input_per_million_micro"] == 1_000_000
    case.meter.pricing = Pricing(ocr=Rate("100.000000", "200.000000"), proposal=Rate())
    public = case.operations.overview(case.manager, case.company["id"])
    assert public["usage"]["estimated_cost"] == "0.000011"
    assert public["usage"]["exposure"] == "0.000011"
    assert public["costs"][0]["estimated_cost"] == "0.000011"
    assert public["costs"][0]["confirmed_cost"] is None
    assert not public["usage"]["confirmed_cost"] or public["usage"]["confirmed_cost"] == "0.000000"


def test_fractional_micro_cost_rounds_up_instead_of_becoming_free(cost_case):
    case = cost_case
    case.meter.pricing = Pricing(ocr=Rate("0.100000", "0.100000"), proposal=Rate())
    policy(case)
    context, _ = claimed_context(case)
    before_send(case, context)
    context.finish(result=SimpleNamespace(usage=ProviderUsage(prompt_tokens=1, completion_tokens=0, total_tokens=1)), attempted=True)
    assert cost_rows(case)[0]["estimated_micro"] == 1
    assert case.operations.overview(case.manager, case.company["id"])["usage"]["exposure"] == "0.000001"


def test_missing_usage_holds_reserve_until_manager_records_manual_confirmation(cost_case):
    case = cost_case
    policy(case)
    context, event = claimed_context(case)
    before_send(case, context)
    context.finish(result=SimpleNamespace(usage=None), attempted=True)
    uncertain = cost_rows(case)[0]
    assert uncertain["state"] == "uncertain" and uncertain["estimated_micro"] is None
    assert uncertain["reserved_micro"] == 100_000
    finance = case.operations.overview(case.actors["finance"], case.company["id"])
    assert finance["usage"]["uncertain_requests"] == 1 and finance["usage"]["exposure"] == "0.100000"
    assert not finance["costs"][0]["can_reconcile"] and event["id"] not in str(finance)
    request = ReconcileRequest(expected_version=uncertain["version"], confirmed_cost="0.03", reason="合成供应商结算证据已核对，人工确认费用")
    with pytest.raises(AccountingError):
        case.operations.reconcile(case.actors["finance"], uncertain["id"], request)
    confirmed = case.operations.reconcile(case.manager, uncertain["id"], request)
    assert confirmed["state"] == "reconciled" and confirmed["confirmed_cost"] == "0.030000"
    updated = cost_rows(case)[0]
    assert updated["estimated_micro"] is None and updated["confirmed_micro"] == 30_000
    assert updated["reserved_micro"] == 100_000 and updated["manual_by"] == case.manager.user_id
    assert "结算证据" in updated["manual_reason"]
    assert case.operations.overview(case.manager, case.company["id"])["usage"]["exposure"] == "0.030000"
    with pytest.raises(AccountingError):
        case.operations.reconcile(case.manager, uncertain["id"], request)


def test_inflight_reservation_cannot_be_released_by_manual_confirmation(cost_case):
    case = cost_case
    policy(case)
    context, _ = claimed_context(case)
    before_send(case, context)
    sent = cost_rows(case)[0]
    with pytest.raises(AccountingError):
        case.operations.reconcile(case.manager, sent["id"], ReconcileRequest(expected_version=sent["version"], confirmed_cost="0.00", reason="试图把正在执行的调用按免费释放"))
    assert cost_rows(case)[0]["state"] == "sent" and cost_rows(case)[0]["reserved_micro"] == 100_000


def test_safe_local_rejection_releases_reserve_without_claiming_usage(cost_case):
    case = cost_case
    policy(case)
    context, _ = claimed_context(case)
    before_send(case, context)
    from app.intelligence.provider import ProviderError
    context.finish(error=ProviderError("busy", "合成本地服务忙，未发送请求"), attempted=True)
    released = cost_rows(case)[0]
    assert released["state"] == "released" and released["estimated_micro"] == 0
    assert released["input_tokens"] is None and released["output_tokens"] is None
    assert case.operations.overview(case.manager, case.company["id"])["usage"]["exposure"] == "0.000000"


def test_enabled_budget_with_missing_pricing_rejects_before_reservation_or_model_call(cost_case):
    case = cost_case
    policy(case)
    case.meter.pricing = Pricing()
    context, _ = claimed_context(case)
    with pytest.raises(BudgetError) as denied:
        before_send(case, context)
    assert denied.value.code == "pricing_missing"
    assert cost_rows(case) == [] and case.provider.extract_calls == 0


def test_reserve_larger_than_monthly_budget_is_not_sent(cost_case):
    case = cost_case
    case.operations.policy(case.manager, case.company["id"], PolicyRequest(expected_version=0, quota_enabled=True, monthly_limit="0.01", ocr_reserve="0.02", proposal_reserve="0.02"))
    context, _ = claimed_context(case)
    with pytest.raises(BudgetError) as denied:
        before_send(case, context)
    assert denied.value.code == "quota_exhausted" and cost_rows(case) == []
    assert case.provider.extract_calls == 0


def test_concurrent_requests_cannot_both_reserve_last_company_budget(cost_case):
    case = cost_case
    case.operations.policy(case.manager, case.company["id"], PolicyRequest(expected_version=0, quota_enabled=True, monthly_limit="0.01", ocr_reserve="0.01", proposal_reserve="0.01"))
    contexts = [claimed_context(case)[0] for _ in range(2)]
    def reserve(context):
        try:
            before_send(case, context)
            return "accepted"
        except BudgetError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, contexts))
    assert sorted(results) == ["accepted", "quota_exhausted"]
    assert len(cost_rows(case)) == 1 and case.provider.extract_calls == 0
    assert case.operations.overview(case.manager, case.company["id"])["usage"]["exposure"] == "0.010000"


def test_cross_month_unknown_usage_is_not_discarded_from_budget_exposure(cost_case):
    case = cost_case
    case.clock.value = datetime(2026, 10, 31, 15, 59, 0, tzinfo=timezone.utc)
    case.operations.policy(case.manager, case.company["id"], PolicyRequest(expected_version=0, quota_enabled=True, monthly_limit="0.01", ocr_reserve="0.01", proposal_reserve="0.01"))
    first, _ = claimed_context(case)
    before_send(case, first)
    first.finish(result=SimpleNamespace(usage=None), attempted=True)
    assert cost_rows(case)[0]["period"] == "2026-10"
    case.clock.advance(120)
    assert case.meter.period() == "2026-11"
    second, _ = claimed_context(case)
    with pytest.raises(BudgetError) as held:
        before_send(case, second)
    assert held.value.code == "quota_exhausted" and len(cost_rows(case)) == 1
    current = case.operations.overview(case.manager, case.company["id"])
    assert current["period"] == "2026-11" and current["usage"]["available"] == "0.000000"


def test_quota_overrun_stops_future_calls_until_independent_cost_confirmation(cost_case):
    case = cost_case
    policy(case)
    context, _ = claimed_context(case)
    before_send(case, context)
    context.finish(result=SimpleNamespace(usage=ProviderUsage(prompt_tokens=100_000, completion_tokens=100_000, total_tokens=200_000)), attempted=True)
    overrun = cost_rows(case)[0]
    assert overrun["estimated_micro"] == 300_000 and overrun["overrun"] == 1
    another, _ = claimed_context(case)
    with pytest.raises(BudgetError) as blocked:
        before_send(case, another)
    assert blocked.value.code == "quota_overrun" and len(cost_rows(case)) == 1
    case.operations.reconcile(case.manager, overrun["id"], ReconcileRequest(expected_version=overrun["version"], confirmed_cost="0.30", reason="人工核对单请求超预留费用，保留原估算并确认"))
    before_send(case, another)
    rows = cost_rows(case)
    assert len(rows) == 2 and next(row for row in rows if row["id"] == overrun["id"])["estimated_micro"] == 300_000


def test_unknown_price_with_quota_off_tracks_uncertainty_without_blocking_ocr(cost_case):
    case = cost_case
    case.meter.pricing = Pricing()
    event = create_event(case)
    run = extract(case, event)
    assert run["status"] == "succeeded" and case.provider.extract_calls == 1
    costs = cost_rows(case)
    assert len(costs) == 1 and costs[0]["state"] == "uncertain"
    assert costs[0]["input_tokens"] == 3 and costs[0]["output_tokens"] == 4
    assert costs[0]["estimated_micro"] is None


def test_ocr_and_proposal_both_track_usage_and_repeated_run_has_no_duplicate_cost(cost_case):
    case = cost_case
    policy(case)
    event = create_event(case)
    first = extract(case, event)
    assert extract(case, event)["id"] == first["id"] and case.provider.extract_calls == 1
    receipt = handoff(case, event)
    proposal = propose(case, receipt["draft_id"])
    assert proposal["status"] == "succeeded"
    assert propose(case, receipt["draft_id"])["id"] == proposal["id"] and case.provider.proposal_calls == 1
    costs = cost_rows(case)
    assert len(costs) == 2 and {row["kind"] for row in costs} == {"ocr", "proposal"}
    assert all(row["state"] == "estimated" and row["estimated_micro"] == 11 for row in costs)
    assert {row["reserved_micro"] for row in costs} == {100_000, 200_000}
    overview = case.operations.overview(case.manager, case.company["id"])
    assert overview["usage"]["requests"] == 2 and overview["usage"]["input_tokens"] == 6
    assert overview["usage"]["output_tokens"] == 8 and overview["usage"]["estimated_cost"] == "0.000022"


def test_highest_legal_token_counts_and_rates_do_not_overflow_intermediate_math(cost_case):
    case = cost_case
    case.meter.pricing = Pricing(ocr=Rate("1000000.000000", "1000000.000000"), proposal=Rate())
    context, _ = claimed_context(case)
    before_send(case, context)
    context.finish(result=SimpleNamespace(usage=ProviderUsage(prompt_tokens=10_000_000, completion_tokens=10_000_000, total_tokens=20_000_000)), attempted=True)
    cost = cost_rows(case)[0]
    assert cost["state"] == "estimated" and cost["estimated_micro"] == 20_000_000_000_000
    assert case.operations.overview(case.manager, case.company["id"])["usage"]["estimated_cost"] == "20000000.000000"


@pytest.mark.parametrize("tokens", [True, "3", -1, 10_000_001])
def test_untrusted_or_out_of_range_usage_never_turns_unknown_cost_into_free(cost_case, tokens):
    case = cost_case
    policy(case)
    context, _ = claimed_context(case)
    before_send(case, context)
    invalid_usage = SimpleNamespace(prompt_tokens=tokens, completion_tokens=4)
    context.finish(result=SimpleNamespace(usage=invalid_usage), attempted=True)
    unknown = cost_rows(case)[0]
    assert unknown["state"] == "uncertain" and unknown["estimated_micro"] is None
    assert unknown["input_tokens"] is None and unknown["output_tokens"] is None
    assert case.operations.overview(case.manager, case.company["id"])["usage"]["exposure"] == "0.100000"


def test_recording_cost_after_actor_revocation_does_not_reopen_evidence(cost_case):
    case = cost_case
    policy(case)
    context, event = claimed_context(case)
    before_send(case, context)
    case.directory.revoke_company_member(case.manager, case.company["id"], case.actors["employee"].user_id)
    context.finish(result=SimpleNamespace(usage=case.provider.usage), attempted=True)
    assert cost_rows(case)[0]["state"] == "estimated" and cost_rows(case)[0]["estimated_micro"] == 11
    with pytest.raises(AccountingError):
        case.intelligence.detail(case.actors["employee"], event["id"])
    assert case.operations.companies(case.actors["employee"]) == {"companies": []}
    assert case.operations.overview(case.manager, case.company["id"])["usage"]["estimated_cost"] == "0.000011"


def test_manual_cost_revisions_preserve_estimate_and_late_settlement_cannot_overwrite(cost_case):
    case = cost_case
    policy(case)
    context, _ = claimed_context(case)
    before_send(case, context)
    context.finish(result=SimpleNamespace(usage=case.provider.usage), attempted=True)
    original = cost_rows(case)[0]
    first = case.operations.reconcile(case.manager, original["id"], ReconcileRequest(expected_version=original["version"], confirmed_cost="0.03", reason="第一次依据合成结算单核定费用"))
    second = case.operations.reconcile(case.manager, original["id"], ReconcileRequest(expected_version=first["version"], confirmed_cost="0.04", reason="发现合成结算单调整，第二次核定保留历史"))
    context.finish(result=SimpleNamespace(usage=ProviderUsage(prompt_tokens=99, completion_tokens=99, total_tokens=198)), attempted=True)
    final = cost_rows(case)[0]
    assert final["state"] == "reconciled" and final["confirmed_micro"] == 40_000
    assert final["estimated_micro"] == 11 and final["input_tokens"] == 3 and final["output_tokens"] == 4
    assert final["version"] == second["version"]
    with case.db.transaction() as conn:
        audits = [dict(row) for row in conn.execute("SELECT * FROM audit_events WHERE object_id=?", (original["id"],))]
    assert "第一次依据合成结算单" in str(audits) and "第二次核定保留历史" in str(audits)


def test_inexact_or_partial_deployment_rates_are_unknown_without_zero_fallback(monkeypatch):
    monkeypatch.setenv("ACCOUNTING_AI_OCR_INPUT_CNY_PER_MILLION", "0.1234567")
    monkeypatch.setenv("ACCOUNTING_AI_OCR_OUTPUT_CNY_PER_MILLION", "2.000000")
    monkeypatch.setenv("ACCOUNTING_AI_PROPOSAL_INPUT_CNY_PER_MILLION", "1.000000")
    monkeypatch.setenv("ACCOUNTING_AI_PROPOSAL_OUTPUT_CNY_PER_MILLION", "")
    rates = Pricing.from_env()
    assert not rates.ocr.configured and not rates.proposal.configured
    assert rates.ocr.snapshot("provider", "model")["input_per_million_micro"] is None
    assert rates.proposal.snapshot("provider", "model")["output_per_million_micro"] is None


def test_unknown_charge_alert_can_be_acknowledged_without_releasing_budget(cost_case):
    case = cost_case
    policy(case, monthly_limit="0.10")
    context, _ = claimed_context(case)
    before_send(case, context)
    context.finish(result=SimpleNamespace(usage=None), attempted=True)
    viewed = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert {alert["code"] for alert in viewed["alerts"] if alert["state"] == "open"} == {"usage_uncertain", "budget_exhausted"}
    alert = next(alert for alert in viewed["alerts"] if alert["code"] == "usage_uncertain")
    version = alert["version"]
    case.clock.advance(10)
    refreshed = case.operations.refresh(case.actors["finance"], case.company["id"])
    same = next(row for row in refreshed["alerts"] if row["id"] == alert["id"])
    assert same["version"] == version and same["last_seen_at"] != alert["last_seen_at"]
    ack = case.operations.acknowledge(case.actors["finance"], alert["id"], AcknowledgeRequest(expected_version=version, reason="企业财务已知晓，等待管理员核对供应商结算"))
    assert ack["state"] == "acknowledged"
    still = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert next(row for row in still["alerts"] if row["id"] == alert["id"])["version"] == ack["version"]
    assert still["usage"]["exposure"] == "0.100000" and cost_rows(case)[0]["state"] == "uncertain"
    with case.db.transaction() as conn:
        reason = conn.execute("SELECT reason FROM ops_alert_actions WHERE alert_id=?", (alert["id"],)).fetchone()[0]
    assert "等待管理员核对" in reason
    cost = cost_rows(case)[0]
    case.operations.reconcile(case.manager, cost["id"], ReconcileRequest(expected_version=cost["version"], confirmed_cost="0.01", reason="管理员根据合成结算凭据核定"))
    resolved = case.operations.overview(case.actors["finance"], case.company["id"])
    assert all(row["state"] == "resolved" for row in resolved["alerts"])
    assert resolved["usage"]["exposure"] == "0.010000"


def test_new_cost_risk_reopens_acknowledged_alert_and_rejects_old_ack_version(cost_case):
    case = cost_case
    policy(case)
    first, _ = claimed_context(case)
    before_send(case, first)
    first.finish(result=SimpleNamespace(usage=None), attempted=True)
    alert = next(row for row in case.operations.refresh(case.manager, case.company["id"])["alerts"] if row["code"] == "usage_uncertain")
    ack = case.operations.acknowledge(case.actors["finance"], alert["id"], AcknowledgeRequest(expected_version=alert["version"], reason="已知晓第一条待核定成本"))
    second, _ = claimed_context(case)
    before_send(case, second)
    second.finish(result=SimpleNamespace(usage=None), attempted=True)
    reopened = next(row for row in case.operations.refresh(case.manager, case.company["id"])["alerts"] if row["id"] == alert["id"])
    assert reopened["state"] == "open" and reopened["version"] > ack["version"]
    with pytest.raises(AccountingError) as stale:
        case.operations.acknowledge(case.actors["finance"], alert["id"], AcknowledgeRequest(expected_version=ack["version"], reason="尝试用旧版本消除新增风险"))
    assert stale.value.status == 409


def test_get_overview_is_read_only_and_expired_sent_cost_is_refreshed_conservatively(cost_case):
    case = cost_case
    policy(case)
    context, _ = claimed_context(case)
    before_send(case, context)
    case.clock.advance(181)
    before = cost_rows(case)[0]
    view = case.operations.overview(case.manager, case.company["id"])
    assert view["costs"][0]["state"] == "sent" and cost_rows(case)[0]["version"] == before["version"]
    refreshed = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert refreshed["costs"][0]["state"] == "uncertain"
    assert refreshed["usage"]["exposure"] == "0.100000"
    assert cost_rows(case)[0]["version"] == before["version"] + 1


def test_queue_wait_and_worker_heartbeat_alerts_follow_actual_conditions(cost_case):
    case = cost_case
    policy(case, quota_enabled=False, queue_wait_minutes=1)
    case.queue = QueueService(case.db, case.intelligence, clock=case.clock)
    event = create_event(case)
    batch = case.queue.enqueue(case.actors["employee"], BatchRequest(company_id=case.company["id"], items=[{
        "event_id": event["id"], "attachment_id": event["attachments"][0]["id"], "expected_version": event["version"],
    }], idempotency_key="operations-pending-heartbeat"))
    missing = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert any(row["code"] == "worker_unavailable" and row["state"] == "open" for row in missing["alerts"])
    with case.queue.worker_transaction() as conn:
        case.operations.maintenance(conn, "synthetic-heartbeat-worker", 0, "idle")
    healthy = case.operations.overview(case.manager, case.company["id"])
    assert healthy["workers"]["available"]
    assert next(row for row in healthy["alerts"] if row["code"] == "worker_unavailable")["state"] == "resolved"
    case.clock.advance(61)
    slow = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert any(row["code"] == "queue_wait" and row["state"] == "open" for row in slow["alerts"])
    assert slow["workers"]["available"]
    case.clock.advance(30)
    stale = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert not stale["workers"]["available"]
    assert next(row for row in stale["alerts"] if row["code"] == "worker_unavailable")["state"] == "open"
    case.queue.cancel_job(case.actors["employee"], batch["jobs"][0]["id"], "合成任务不再需要处理，明确取消")
    cleared = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert all(row["state"] == "resolved" for row in cleared["alerts"])
    assert case.provider.extract_calls == 0


def test_repeated_snapshot_reads_do_not_reset_oldest_manual_exception_age(cost_case):
    case = cost_case
    event = create_event(case)
    detail = case.intelligence.reconcile(case.actors["finance"], event["id"])
    assert detail["findings"]
    with case.db.transaction() as conn:
        before = dict(conn.execute("SELECT * FROM ops_exception_snapshots WHERE event_id=?", (event["id"],)).fetchone())
    assert before["blocking_count"] > 0
    case.intelligence.detail(case.actors["finance"], event["id"])
    case.intelligence.reconcile(case.actors["finance"], event["id"])
    with case.db.transaction() as conn:
        stable = dict(conn.execute("SELECT * FROM ops_exception_snapshots WHERE event_id=?", (event["id"],)).fetchone())
    assert stable["updated_at"] == before["updated_at"] and stable["oldest_opened_at"] == before["oldest_opened_at"]
    case.intelligence.company_settings(case.manager, case.company["id"], enabled=False)
    assert case.operations.overview(case.manager, case.company["id"])["exceptions"]["blocking"] == 0


def test_non_overridable_queue_and_pending_ocr_are_not_manual_exception_backlog(cost_case):
    case = cost_case
    event = create_event(case)
    queue = QueueService(case.db, case.intelligence, clock=case.clock)
    queue.enqueue(case.actors["employee"], BatchRequest(company_id=case.company["id"], items=[{
        "event_id": event["id"], "attachment_id": event["attachments"][0]["id"], "expected_version": event["version"],
    }], idempotency_key="pending-not-manual-backlog"))
    detail = case.intelligence.reconcile(case.actors["finance"], event["id"])
    assert any(row["payload"].get("non_overridable") for row in detail["findings"])
    view = case.operations.overview(case.manager, case.company["id"])
    assert view["queue"]["queued"] == 1 and view["exceptions"]["blocking"] == 0
    assert not any(row["code"] == "exception_backlog" and row["state"] == "open" for row in view["alerts"])


def test_late_valid_usage_settles_expired_unknown_reservation_but_not_manual_revision(cost_case):
    case = cost_case
    policy(case)
    context, _ = claimed_context(case)
    before_send(case, context)
    case.clock.advance(181)
    case.operations.refresh(case.manager, case.company["id"])
    assert cost_rows(case)[0]["state"] == "uncertain"
    context.finish(result=SimpleNamespace(usage=case.provider.usage), attempted=True)
    late = cost_rows(case)[0]
    assert late["state"] == "estimated" and late["estimated_micro"] == 11
    case.operations.reconcile(case.manager, late["id"], ReconcileRequest(expected_version=late["version"], confirmed_cost="0.03", reason="已由管理者核定，迟到响应不能改写"))
    context.finish(result=SimpleNamespace(usage=ProviderUsage(prompt_tokens=99, completion_tokens=99, total_tokens=198)), attempted=True)
    assert cost_rows(case)[0]["confirmed_micro"] == 30_000 and cost_rows(case)[0]["estimated_micro"] == 11


def test_failed_model_output_with_reported_usage_is_still_a_paid_estimate(cost_case):
    case = cost_case
    policy(case)
    failure = ProviderError("schema_invalid", "合成供应商字段不合规，但报告了真实计量")
    failure.usage = case.provider.usage
    case.provider.failure = failure
    event = create_event(case)
    result = extract(case, event)
    assert result["status"] == "failed" and result["result"] is None
    cost = cost_rows(case)[0]
    assert cost["state"] == "estimated" and cost["estimated_micro"] == 11
    assert extract(case, event)["id"] == result["id"] and len(cost_rows(case)) == 1


def test_queue_retry_cannot_bypass_unknown_cost_reservation_or_company_budget(cost_case):
    case = cost_case
    policy(case, monthly_limit="0.10")
    case.queue = QueueService(case.db, case.intelligence, clock=case.clock)
    worker = QueueWorker(case.queue, worker_id="meter-queue-worker")
    event = create_event(case)
    batch = case.queue.enqueue(case.actors["employee"], BatchRequest(company_id=case.company["id"], items=[{
        "event_id": event["id"], "attachment_id": event["attachments"][0]["id"], "expected_version": event["version"],
    }], idempotency_key="budgeted-queue-batch"))
    case.provider.failure = ProviderError("timeout", "合成发送后超时，费用未知")
    assert worker.run_once()
    unknown = cost_rows(case)[0]
    assert unknown["state"] == "uncertain"
    case.provider.failure = None
    case.queue.retry_job(case.actors["employee"], batch["jobs"][0]["id"], "retry-while-cost-held")
    assert worker.run_once()
    denied = case.queue.get_batch(case.actors["employee"], batch["id"])["jobs"][0]
    assert denied["status"] == "failed" and denied["error_code"] == "quota_exhausted"
    assert case.provider.extract_calls == 1 and len(cost_rows(case)) == 1
    case.operations.reconcile(case.manager, unknown["id"], ReconcileRequest(expected_version=unknown["version"], confirmed_cost="0.00", reason="管理者核对合成结算证据，明确确认该次未收费"))
    case.queue.retry_job(case.actors["employee"], denied["id"], "retry-after-cost-confirmed")
    assert worker.run_once()
    assert case.queue.get_batch(case.actors["employee"], batch["id"])["progress"]["succeeded"] == 1
    assert case.provider.extract_calls == 2 and len(cost_rows(case)) == 2


@pytest.fixture
def operations_http(tmp_path):
    provider = UsageProvider(usage=ProviderUsage(prompt_tokens=10_000, completion_tokens=0, total_tokens=10_000))
    pricing = Pricing(ocr=Rate("1.000000", "2.000000"), proposal=Rate("1.000000", "2.000000"))
    app = create_app(tmp_path / "http-operations.db", seed=True, attachment_root=tmp_path / "private-operations", intelligence_provider=provider,
                     queue_worker_enabled=False, model_pricing=pricing)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 6800)) as client:
        client.provider = provider
        yield client


def test_http_company_scopes_and_cost_permissions_keep_raw_employee_evidence_private(operations_http):
    client = operations_http
    employee, department, finance, accountant = [http_auth(client, role) for role in ("employee", "department", "finance", "accountant")]
    event, company = http_create(client, employee)
    for restricted in (employee, department):
        response = client.get("/api/operations/companies", headers=restricted)
        assert response.status_code == 200 and response.json() == {"companies": []}
        assert client.get(f"/api/operations/companies/{company['id']}/overview", headers=restricted).status_code == 404
    all_companies = client.get("/api/operations/companies", headers=accountant).json()["companies"]
    # The local persona is deliberately assigned to all three fictional
    # companies. Remove two grants through the real management endpoint.
    for other_company in all_companies:
        if other_company["id"] != company["id"]:
            response = client.delete(f"/api/companies/{other_company['id']}/members/local-business-finance", headers=accountant)
            assert response.status_code == 200, response.text
    finance_companies = client.get("/api/operations/companies", headers=finance).json()["companies"]
    assert len(finance_companies) == 1 and not finance_companies[0]["can_manage_budget"]
    other = next(row for row in all_companies if row["id"] != company["id"])
    assert client.get(f"/api/operations/companies/{other['id']}/overview", headers=finance).status_code == 404
    overview = client.get(f"/api/operations/companies/{company['id']}/overview", headers=accountant)
    assert overview.status_code == 200 and event["id"] not in overview.text
    assert event["attachments"][0]["id"] not in overview.text and "test-only-not-a-secret" not in overview.text
    assert client.get(f"/api/business/events/{event['id']}/intelligence", headers=accountant).status_code == 404
    url = f"/api/operations/companies/{company['id']}/policy"
    body = {"expected_version": 0, "quota_enabled": True, "monthly_limit": "0.01", "ocr_reserve": "0.01", "proposal_reserve": "0.01"}
    assert client.put(url, headers=finance, json=body).status_code == 403
    assert client.put(url, headers=employee, json=body).status_code == 404
    saved = client.put(url, headers=accountant, json=body)
    assert saved.status_code == 200 and saved.json()["monthly_limit"] == "0.01", saved.text
    assert client.put(url, headers=accountant, json=body).status_code == 409


def test_http_quota_refuses_second_send_finance_ack_and_manager_manual_cost_confirmation(operations_http):
    client = operations_http
    employee, finance, accountant = [http_auth(client, role) for role in ("employee", "finance", "accountant")]
    event, company = http_create(client, employee)
    client.provider.document = invoice_document(buyer_name=company["name"], buyer_tax_id=None)
    assert client.put(f"/api/companies/{company['id']}/intelligence/settings", headers=accountant, json={"enabled": True}).status_code == 200
    assert client.put(f"/api/operations/companies/{company['id']}/policy", headers=accountant, json={
        "expected_version": 0, "quota_enabled": True, "monthly_limit": "0.01", "ocr_reserve": "0.01", "proposal_reserve": "0.01",
    }).status_code == 200
    request = {"attachment_id": event["attachments"][0]["id"], "expected_version": event["version"], "idempotency_key": "http-paid-ocr-once"}
    url = f"/api/business/events/{event['id']}/ocr"
    first = client.post(url, headers=employee, json=request)
    assert first.status_code == 200 and first.json()["status"] == "succeeded", first.text
    assert client.post(url, headers=employee, json=request).json()["id"] == first.json()["id"]
    refused = client.post(url, headers=employee, json={**request, "idempotency_key": "http-paid-ocr-refused"})
    assert refused.status_code == 429 and client.provider.extract_calls == 1, refused.text
    refresh = client.post(f"/api/operations/companies/{company['id']}/refresh", headers=finance)
    assert refresh.status_code == 200, refresh.text
    view = refresh.json()
    assert view["usage"]["estimated_cost"] == "0.010000" and view["usage"]["available"] == "0.000000"
    assert len(view["costs"]) == 1 and view["costs"][0]["estimated_cost"] == "0.010000"
    alert = next(row for row in view["alerts"] if row["code"] == "budget_exhausted")
    acknowledged = client.post(f"/api/operations/alerts/{alert['id']}/acknowledge", headers=finance,
                               json={"expected_version": alert["version"], "reason": "财务已知晓预算耗尽，交管理者核对供应商结算"})
    assert acknowledged.status_code == 200 and acknowledged.json()["state"] == "acknowledged"
    cost = view["costs"][0]
    reconcile_url = f"/api/operations/costs/{cost['id']}/reconcile"
    confirmed = {"expected_version": cost["version"], "confirmed_cost": "0.00", "reason": "管理者核对合成供应商结算单明确确认未收费"}
    assert client.post(reconcile_url, headers=finance, json=confirmed).status_code == 403
    assert client.post(reconcile_url, headers=employee, json=confirmed).status_code == 404
    received = client.post(reconcile_url, headers=accountant, json=confirmed)
    assert received.status_code == 200 and received.json()["confirmed_cost"] == "0.000000", received.text
    assert received.json()["estimated_cost"] == "0.010000"
    updated = client.get(f"/api/operations/companies/{company['id']}/overview", headers=finance).json()
    assert updated["usage"]["available"] == "0.010000" and updated["usage"]["confirmed_cost"] == "0.000000"
    assert next(row for row in updated["alerts"] if row["id"] == alert["id"])["state"] == "resolved"
    assert client.provider.extract_calls == 1


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_company_managers_can_manage_budget_without_firm_membership_or_original_access(cost_case, role):
    case = cost_case
    identity = SimpleNamespace(user_id="ops-company-" + uuid4().hex, display_name="企业预算管理者", email="ops-" + uuid4().hex + "@example.test")
    case.directory.register_identity(identity)
    case.directory.grant_company_member(case.manager, case.company["id"], CompanyMemberGrant(email=identity.email, role=role))
    actor = Actor(identity.user_id, case.manager.firm_id)
    event = create_event(case)
    listed = case.operations.companies(actor)["companies"]
    assert len(listed) == 1 and listed[0]["can_manage_budget"]
    changed = case.operations.policy(actor, case.company["id"], PolicyRequest(expected_version=0, quota_enabled=True, monthly_limit="1.00", ocr_reserve="0.10", proposal_reserve="0.10"))
    assert changed["can_manage"] and changed["quota_enabled"]
    with pytest.raises(AccountingError):
        case.operations.overview(actor, case.other_company["id"])
    with pytest.raises(AccountingError):
        case.intelligence.detail(actor, event["id"])


def test_task_failure_frequency_opens_site_alert_and_known_model_cost_is_preserved(cost_case):
    case = cost_case
    policy(case, quota_enabled=False, failure_threshold=3)
    queue = QueueService(case.db, case.intelligence, clock=case.clock)
    worker = QueueWorker(queue, worker_id="failure-monitor-worker")
    events = [create_event(case) for _ in range(3)]
    queue.enqueue(case.actors["employee"], BatchRequest(company_id=case.company["id"], items=[{
        "event_id": event["id"], "attachment_id": event["attachments"][0]["id"], "expected_version": event["version"],
    } for event in events], idempotency_key="failure-frequency-batch"))
    failure = ProviderError("schema_invalid", "合成供应商输出不合规，但其用量仍计费")
    failure.usage = case.provider.usage
    case.provider.failure = failure
    for index in range(3):
        assert worker.run_once()
        view = case.operations.overview(case.actors["finance"], case.company["id"])
        assert view["queue"]["recent_failures"] == index + 1
        if index < 2:
            assert not any(alert["code"] == "task_failures" and alert["state"] == "open" for alert in view["alerts"])
    alert = next(row for row in view["alerts"] if row["code"] == "task_failures")
    assert alert["state"] == "open" and alert["severity"] == "warning"
    assert view["usage"]["estimated_cost"] == "0.000033" and case.provider.extract_calls == 3
    assert all(row["state"] == "estimated" for row in cost_rows(case))


def test_longstanding_manual_exception_alert_uses_snapshot_counts_without_original_reads(cost_case):
    case = cost_case
    policy(case, quota_enabled=False, backlog_threshold=1, backlog_age_minutes=1)
    event = create_event(case)
    case.intelligence.reconcile(case.actors["finance"], event["id"])
    old = (case.clock() - timedelta(minutes=2)).isoformat()
    with case.db.transaction(write=True) as conn:
        conn.execute("UPDATE ops_exception_snapshots SET oldest_opened_at=? WHERE event_id=?", (old, event["id"]))
    view = case.operations.refresh(case.manager, case.company["id"])
    alert = next(row for row in view["alerts"] if row["code"] == "exception_backlog")
    assert alert["state"] == "open" and view["exceptions"]["events"] == 1
    assert event["id"] not in str(view) and event["attachments"][0]["id"] not in str(view)
    with pytest.raises(AccountingError):
        case.intelligence.detail(case.manager, event["id"])
    detail = case.intelligence.detail(case.actors["finance"], event["id"])
    for finding in detail["findings"]:
        if finding["severity"] == "blocking" and not finding["resolved"]:
            case.intelligence.resolve(case.actors["finance"], finding["id"], "财务已人工核对原件，合成案例无需再次识别")
    after = case.operations.refresh(case.manager, case.company["id"])
    assert after["exceptions"]["blocking"] == 0
    assert next(row for row in after["alerts"] if row["id"] == alert["id"])["state"] == "resolved"


def test_http_business_and_queue_writes_refresh_safe_snapshots_without_registering_findings(operations_http):
    client = operations_http
    employee, finance, accountant = [http_auth(client, role) for role in ("employee", "finance", "accountant")]
    event, company = http_create(client, employee)
    assert client.put(f"/api/companies/{company['id']}/intelligence/settings", headers=accountant, json={"enabled": True}).status_code == 200
    modified = client.patch(f"/api/business/events/{event['id']}", headers=employee,
                            json={"expected_version": event["version"], "payload": {**event["payload"], "amount": "0.31"}})
    assert modified.status_code == 200, modified.text
    event = modified.json()
    evidence = client.get(f"/api/business/events/{event['id']}/intelligence", headers=employee).json()
    assert evidence["findings"] and all(finding["id"] is None for finding in evidence["findings"])
    url = f"/api/operations/companies/{company['id']}/overview"
    manual = client.get(url, headers=accountant).json()
    assert manual["exceptions"]["blocking"] >= 1 and event["id"] not in str(manual)
    queued = client.post("/api/intelligence/batches", headers=employee, json={"company_id": company["id"], "items": [{
        "event_id": event["id"], "attachment_id": event["attachments"][0]["id"], "expected_version": event["version"],
    }], "idempotency_key": "http-write-snapshot-queue"})
    assert queued.status_code == 202, queued.text
    waiting = client.get(url, headers=accountant).json()
    assert waiting["queue"]["queued"] == 1 and waiting["exceptions"]["blocking"] == 0
    cancelled = client.post(f"/api/intelligence/jobs/{queued.json()['jobs'][0]['id']}/cancel", headers=finance, json={"reason": "合成工作流改为人工核票，取消后台任务"})
    assert cancelled.status_code == 200, cancelled.text
    manual_again = client.get(url, headers=accountant).json()
    assert manual_again["queue"]["cancelled"] == 1 and manual_again["exceptions"]["blocking"] >= 1
    evidence_again = client.get(f"/api/business/events/{event['id']}/intelligence", headers=employee).json()
    assert all(finding["id"] is None for finding in evidence_again["findings"])
    assert client.provider.extract_calls == 0
