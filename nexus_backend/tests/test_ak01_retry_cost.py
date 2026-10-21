"""AK01: receipt/usage evidence, not retryable, controls automatic resend."""
from contextlib import contextmanager

import pytest

from app.accounting.domain import AccountingError
from app.assistant.meter import AssistantMeter
from app.assistant.worker import AssistantWorker
from app.intelligence.models import ProviderUsage
from app.intelligence.provider import ProviderError
from app.jobs.service import QueueService
from app.jobs.worker import QueueWorker
from test_intelligence_workflow import ai_case, create_event, extract, handoff
from test_model_cost_workflow import before_send, claimed_context, cost_case, cost_rows, policy
from test_queue_workflow import enqueue
from test_stage15_assistant_meter import chat_case, question
from test_ak01_service_limits import service_case, execute, costs


@pytest.mark.parametrize("scenario", ["ocr", "journal", "chat"])
@pytest.mark.parametrize("usage_kind", ["missing", "zero", "positive"])
def test_sent_429_keeps_usage_without_automatic_new_attempt(chat_case, scenario, usage_kind):
    c = chat_case
    policy(c, chat_reserve="0.10")
    usage = (
        None if usage_kind == "missing"
        else ProviderUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        if usage_kind == "zero"
        else ProviderUsage(prompt_tokens=6, completion_tokens=4, total_tokens=10)
    )
    error = ProviderError("http_error", "Synthetic HTTP 429", status=429, retryable=True)
    error.usage = usage
    if scenario == "chat":
        actor, conv, task = question(c)
        c.chat_provider.failure = error
        c.chat_worker.run_once()
        c.chat_worker = AssistantWorker(c.chat_service, c.chat_provider, AssistantMeter(c.db, c.meter))
        assert c.chat_worker.run_once() is None
        assert c.chat_provider.calls == 1
        result = c.chat_service.turn(actor, conv["id"], task["id"])["turn"]
    else:
        c.queue = QueueService(c.db, c.intelligence, clock=c.clock)
        c.business.proposal_queue = c.queue.proposals
        c.worker = QueueWorker(c.queue)
        event = create_event(c)
        if scenario == "journal":
            extract(c, event)
            mapped = handoff(c, event)
            task_id = mapped["automatic_job"]["id"]
        else:
            task_id = enqueue(c, [event])["jobs"][0]["id"]
        c.provider.failure = error
        assert c.worker.run_once()
        c.clock.advance(61)
        c.worker = QueueWorker(QueueService(c.db, c.intelligence, clock=c.clock))
        assert not c.worker.run_once(), "received 429 is not proof of no send or zero charge"
        assert (c.provider.proposal_calls if scenario == "journal" else c.provider.extract_calls) == 1
        with c.db.transaction() as conn:
            table = "proposal_jobs" if scenario == "journal" else "ocr_jobs"
            result = dict(conn.execute("SELECT * FROM " + table + " WHERE id=?", (task_id,)).fetchone())
    assert result["status"] in {"failed", "interrupted", "needs_attention"}
    kind = "proposal" if scenario == "journal" else scenario
    rows = [row for row in cost_rows(c) if row["kind"] == kind]
    assert len(rows) == 1
    row = rows[0]
    assert row["state"] == ("uncertain" if usage is None else "estimated")
    assert row["estimated_micro"] is None if usage is None else row["estimated_micro"] >= 0
    if usage is not None:
        assert (row["input_tokens"], row["output_tokens"]) == (usage.prompt_tokens, usage.completion_tokens)


@pytest.mark.parametrize("usage", [
    ProviderUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
    ProviderUsage(prompt_tokens=6, completion_tokens=4, total_tokens=10),
])
def test_reported_usage_is_never_discarded_by_a_local_rejection_hint(cost_case, usage):
    c = cost_case
    policy(c)
    context, _ = claimed_context(c)
    before_send(c, context)
    error = ProviderError("busy", "synthetic conflicting pre-send hint", retryable=True)
    error.llm_attempted = False
    error.usage = usage
    context.finish(error=error, attempted=True)
    row = cost_rows(c)[0]
    assert row["state"] == "estimated"
    assert (row["input_tokens"], row["output_tokens"]) == (usage.prompt_tokens, usage.completion_tokens)


@pytest.mark.parametrize("attempted", [False, True])
def test_explicit_sent_evidence_overrides_a_local_error_code(cost_case, attempted):
    c = cost_case
    policy(c)
    context, _ = claimed_context(c)
    before_send(c, context)
    error = ProviderError("busy", "synthetic sent response with a conflicting local code")
    error.llm_attempted = True
    context.finish(error=error, attempted=attempted)
    row = cost_rows(c)[0]
    assert row["state"] == "uncertain"
    assert row["estimated_micro"] is None


@pytest.mark.parametrize("scenario", ["ocr", "journal", "chat"])
def test_transport_authorization_failure_keeps_unsent_proof(service_case, monkeypatch, scenario):
    from app.concurrency import provider as capacity

    case = service_case.setup(scenario)

    @contextmanager
    def deny_capacity(*args, **kwargs):
        raise AccountingError("合成授权变更，请求未发送", 403)
        yield  # pragma: no cover

    monkeypatch.setattr(capacity, "provider_capacity", deny_capacity)
    if scenario == "chat":
        execute(case)
    else:
        with pytest.raises(AccountingError):
            execute(case)
    assert not case.calls
    assert costs(case)[0]["state"] == "released"
