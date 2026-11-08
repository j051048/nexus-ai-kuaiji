"""Synthetic pilot projections and read-only evidence under real restricted PG roles."""

import pytest
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest
from app.accounting.review_policy import ReviewPolicyService
from app.accounting.review_policy_models import ReviewPolicyChange
from app.operations.efficiency import EfficiencyService
from app.operations.efficiency_models import SessionStart
from app.observability.service import FirmObservabilityService
from test_postgres_integration import case, postgres, peer, create_draft, ADMIN_URL, APP_URL  # noqa: F401
from test_intelligence_workflow_postgres import pg_ai, business_case, business_postgres  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="显式本地隔离 PG 连接必需")


def test_pilot_scope_zero_review_classification_and_revocation(case):
    c = case
    service = EfficiencyService(c.db)
    assert service.overview(c.actor, c.book)["pilot"]["human_seconds"] is None
    service.start(
        c.actor, SessionStart(book_id=c.book, activity="review", idempotency_key="pilot-zero")
    )
    before = service.overview(c.actor, c.book)["pilot"]
    assert before["human_seconds"] == 0 and before["session_count"] == 1
    draft = create_draft(c, external_id="pilot-independent")
    c.service.approve(
        c.reviewer, c.book, draft["id"], ApprovalRequest(expected_version=draft["version"])
    )
    after = service.overview(c.reviewer, c.book)["pilot"]
    assert after["review"]["independent"] == after["review"]["total_reviewed"] == 1
    assert after["source_fingerprint"] != before["source_fingerprint"]
    ReviewPolicyService(c.db).configure(
        c.actor,
        c.book,
        ReviewPolicyChange(expected_version=0, mode="single_operator", reason="合成试点分类核对"),
    )
    assert service.overview(c.actor, c.book)["pilot"]["review"] == after["review"]
    foreign = peer(c, firm_role="owner", other_firm=True)
    with pytest.raises(AccountingError):
        service.overview(foreign, c.book)
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
            (c.book, c.reviewer.user_id),
        )
    with pytest.raises(AccountingError):
        service.overview(c.reviewer, c.book)


def test_monitor_restricted_current_firm_and_unknown_samples(case):
    c = case
    monitor = FirmObservabilityService(c.db, c.service)
    value = monitor.snapshot(c.actor, "2026-10")
    assert value["firm_id"] == c.actor.firm_id and value["period"] == "2026-10"
    assert value["cost_summary"] == {
        "sample_count": 0,
        "known_micro": None,
        "unknown_count": 0,
        "estimated_count": 0,
    }
    assert value["metrics"]["avg_lock_ms"] is None
    assert all(not s["automatic_action"] for s in value["signals"])
    with pytest.raises(AccountingError):
        monitor.snapshot(c.reviewer, "2026-10")
    with pytest.raises(AccountingError):
        monitor.snapshot(c.actor, "2026-ab")


def test_monitor_weights_transactions_not_bucket_means(case):
    import time

    c = case
    minute = int(time.time() // 60)
    with c.db.transaction(
        write=True, actor=c.actor, company_id=c.company, write_scope="company"
    ) as conn:
        for stream, count, total in [("one", 1, 100.0), ("nine", 9, 0.0)]:
            assert conn.execute(
                "SELECT accounting.save_transaction_bucket(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    c.actor.firm_id,
                    c.company,
                    stream,
                    minute,
                    count,
                    0,
                    0,
                    total,
                    total,
                    total,
                    100,
                    100,
                    100,
                ),
            ).fetchone()[0]
    view = FirmObservabilityService(c.db, c.service).snapshot(c.actor, "2026-10")
    assert view["metrics"]["transactions"] == 10
    assert view["metrics"]["avg_lock_ms"] == 10.0
    assert view["metrics"]["avg_connection_ms"] == 10.0


def test_monitor_unknown_model_cost_does_not_disappear_or_resend(pg_ai):
    from app.operations.meter import MeterService
    from app.operations.pricing import Pricing, Rate
    from test_model_cost_workflow import UsageProvider
    from test_intelligence_workflow import create_event, extract

    c = pg_ai
    fake = UsageProvider(document=c.provider.document, usage=None)
    c.intelligence.provider = fake
    c.intelligence.meter = MeterService(c.db, pricing=Pricing(ocr=Rate("1.000000", "2.000000")))
    event = create_event(c)
    result = extract(c, event, key="0712-unknown-cost")
    assert result["status"] == "succeeded" and fake.extract_calls == 1
    with c.db.transaction(actor=c.manager) as conn:
        period = conn.execute(
            "SELECT period FROM model_costs WHERE firm_id=?", (c.manager.firm_id,)
        ).fetchone()[0]
    monitor = FirmObservabilityService(c.db, c.ledger)
    for _ in range(2):
        value = monitor.snapshot(c.manager, period)
        assert (
            value["cost_summary"]["sample_count"] == 1
            and value["cost_summary"]["unknown_count"] == 1
        )
        assert value["cost_summary"]["known_micro"] is None
        assert any(s["code"] == "cost_unknown" for s in value["signals"])
    assert fake.extract_calls == 1
    with pytest.raises(AccountingError):
        monitor.snapshot(c.actors["employee"], period)


def test_task_replay_uses_real_pg_scope_and_rejects_rehashed_steps(case, monkeypatch):
    from copy import deepcopy
    from app.assistant.investigation import ENV_FLAG
    from tools.assistant_replay import investigation as replay

    c = case
    monkeypatch.setenv(ENV_FLAG, "1")
    dataset, candidate = replay.load_dataset(), replay.candidate_identity()
    row = replay.observe(dataset, candidate, "dev_complete", c.service, c.actor, c.book, "2026-10")
    assert row["result"]["status"] == "complete"
    assert row["result"]["model_calls"] == 0
    good = replay.evaluate(dataset, [row], candidate)
    assert next(s for s in good["samples"] if s["sample_id"] == "dev_complete")["passed"]
    bad = deepcopy(row)
    bad["result"]["steps"] = []
    bad["result_sha256"] = replay.digest(bad["result"])
    report = replay.evaluate(dataset, [bad], candidate)
    assert report["denominator"] == 6
    assert not next(s for s in report["samples"] if s["sample_id"] == "dev_complete")["passed"]
    foreign = peer(c, firm_role="owner", other_firm=True)
    denied = replay.observe(
        dataset, candidate, "dev_foreign", c.service, foreign, c.book, "2026-10"
    )
    assert not denied["result"]["findings"] and denied["result"]["model_calls"] == 0
