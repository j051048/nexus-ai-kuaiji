"""Incremental pilot/observability/replay contracts, synthetic data only."""

from copy import deepcopy
import pytest
from app.accounting.domain import AccountingError
from app.operations.efficiency import EfficiencyService
from app.operations.efficiency_models import SessionStart
from app.observability.service import FirmObservabilityService
from test_business import business  # noqa: F401
from test_model_cost_workflow import cost_case, ai_case, policy, claimed_context, before_send  # noqa: F401


@pytest.mark.parametrize("known_zero", [False, True])
def test_monitor_preserves_429_unknown_versus_explicit_zero(cost_case, known_zero):
    from app.intelligence.models import ProviderUsage
    from app.intelligence.provider import ProviderError

    c = cost_case
    policy(c)
    context, _ = claimed_context(c)
    before_send(c, context)
    error = ProviderError("http_error", "synthetic rate limit", status=429, retryable=True)
    error.usage = (
        ProviderUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0) if known_zero else None
    )
    context.finish(error=error, attempted=True)
    with c.db.transaction(actor=c.manager) as conn:
        period = conn.execute(
            "SELECT period FROM model_costs WHERE firm_id=?", (c.manager.firm_id,)
        ).fetchone()[0]
    value = FirmObservabilityService(c.db, c.ledger).snapshot(c.manager, period)
    assert value["cost_summary"]["sample_count"] == 1
    assert value["cost_summary"]["known_micro"] == (0 if known_zero else None)
    assert value["cost_summary"]["unknown_count"] == (0 if known_zero else 1)
    assert value["cost_summary"]["estimated_count"] == (1 if known_zero else 0)
    assert any(s["code"] == "cost_unknown" for s in value["signals"]) is (not known_zero)


def test_pilot_empty_time_is_unknown_and_current_review_denominator_is_explicit(business):
    c = business
    value = EfficiencyService(c.db).overview(c.manager, c.book["id"])
    pilot = value["pilot"]
    assert pilot["human_seconds"] is None and pilot["session_count"] == 0
    assert pilot["review"]["total_reviewed"] == 0
    assert pilot["measured_savings_seconds"] is None
    assert pilot["measurement"] == "observed_only"
    assert len(pilot["source_fingerprint"]) == 64


def test_pilot_explicit_zero_sample_is_not_missing_and_scope_is_current(business):
    c = business
    service = EfficiencyService(c.db)
    service.start(
        c.manager,
        SessionStart(book_id=c.book["id"], activity="review", idempotency_key="0712-start"),
    )
    value = service.overview(c.manager, c.book["id"])
    assert value["pilot"]["human_seconds"] == 0
    assert value["pilot"]["session_count"] == 1
    for who in ["employee", "peer"]:
        with pytest.raises(AccountingError):
            service.overview(c.actors[who], c.book["id"])


def test_monitor_without_samples_is_unknown_and_supplies_manual_next_step(business):
    c = business
    value = FirmObservabilityService(c.db, c.service).snapshot(c.manager, "2026-10")
    assert value["firm_id"] == c.manager.firm_id and value["period"] == "2026-10"
    assert value["cost_summary"]["sample_count"] == 0
    assert value["cost_summary"]["known_micro"] is None
    assert value["metrics"]["avg_lock_ms"] is None
    assert any(
        s["code"] == "worker_heartbeat_missing" and s["owner_role"] == "admin" and s["next_action"]
        for s in value["signals"]
    )
    assert all(s["automatic_action"] is False for s in value["signals"])


@pytest.mark.parametrize("period", ["2026-ab", "2026-00", "2026-13", "bad"])
def test_monitor_bad_period_is_controlled_error(business, period):
    with pytest.raises(AccountingError) as caught:
        FirmObservabilityService(business.db, business.service).snapshot(business.manager, period)
    assert caught.value.status == 422


@pytest.mark.parametrize("mutation", ["scope", "steps", "missing", "fingerprint"])
def test_task_replay_rejects_rehashed_wrong_evidence(business, monkeypatch, mutation):
    from app.assistant import investigation
    from tools.assistant_replay import investigation as replay

    monkeypatch.setenv(investigation.ENV_FLAG, "1")
    dataset, candidate = replay.load_dataset(), replay.candidate_identity()
    row = replay.observe(
        dataset,
        candidate,
        "dev_complete",
        business.service,
        business.manager,
        business.book["id"],
        "2026-10",
    )
    row = deepcopy(row)
    if mutation == "scope":
        row["result"]["evidence"]["firm_id"] = "foreign-firm"
    elif mutation == "steps":
        row["result"]["steps"] = []
    elif mutation == "missing":
        row["result"]["missing"] = ["delivery_status"]
    else:
        row["result"]["evidence"]["fingerprint"] = "0" * 64
    row["result_sha256"] = replay.digest(row["result"])
    report = replay.evaluate(dataset, [row], candidate)
    sample = next(s for s in report["samples"] if s["sample_id"] == "dev_complete")
    assert sample["passed"] is False
    assert report["denominator"] == 6
