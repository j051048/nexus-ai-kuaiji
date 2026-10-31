"""Six synthetic tasks through the actual assistant queue, meter and publisher.

Reuse the existing FakeProvider/SQLite business fixture and the assistant's
MockTransport. Nothing here loads a production DSN or real supplier credential.
"""

import copy
import json
import os
import socket
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.assistant.models import ConversationCreate, TurnCreate
from app.assistant.worker import AssistantWorker
from app.accounting.service import fingerprint
from test_ak04_assistant_execution import execution_case
from test_ak03_assistant_facts import amount_claim, reply
from test_intelligence_workflow import ai_case, create_event, extract, handoff, FakeProvider
from test_model_cost_workflow import cost_case, cost_rows, policy
from tools.assistant_replay.baseline import (
    DEFAULT_MANIFEST,
    BaselineError,
    candidate_identity,
    evaluate,
    load_dataset,
    save_report,
)


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("离线任务禁止真实网络连接")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


@pytest.fixture(scope="module")
def baseline_run():
    dataset, identity, rows = load_dataset(DEFAULT_MANIFEST), candidate_identity(), []
    yield dataset, identity, rows
    assert candidate_identity() == identity, "候选运行中变化，不得合并验收证据"
    report = evaluate(dataset, rows, identity)
    assert report["engineering"] == "passed"
    assert report["denominator"] == 6
    # Batch two must close the original frozen task rubric without weakening gold.
    assert report["answer_eligible_count"] == 3
    assert report["answer_complete_count"] == 3
    if os.environ.get("ASSISTANT_TASK_REPORT_DIR"):
        save_report(report, os.environ["ASSISTANT_TASK_REPORT_DIR"], observations=rows)


def financial_state(c):
    with c.db.transaction() as conn:
        return fingerprint(
            {
                table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
                for table in (
                    "drafts",
                    "sources",
                    "business_events",
                    "exports",
                    "monthly_delivery_packages",
                )
            }
        )


@pytest.mark.parametrize("sid", ["normal", "missing", "conflict", "revoked", "quota", "unknown"])
def test_monthly_delivery_task_real_worker(execution_case, baseline_run, sid):
    dataset, identity, rows = baseline_run
    c, case = execution_case, dataset["cases"][sid]
    assert isinstance(c.provider, FakeProvider)
    # The second company's task is executed against a real distinct company,
    # not merely renamed in the report. The same owner may serve both.
    if case["company"] == "company_b":
        c.company, c.other_company = c.other_company, c.company
        c.book, c.other_book = c.other_book, c.book
        c.intelligence.company_settings(c.manager, c.company["id"], enabled=True)
    policy(c, monthly_limit="0.01" if sid == "quota" else "1.00", chat_reserve="0.10")
    actor = c.actors["accountant"] if sid == "revoked" else c.manager
    event_ids = []
    draft_id = None
    if sid in {"normal", "missing", "conflict"}:
        actor = c.actors["finance"]
        from app.business.models import BusinessEventCreate

        event = (
            c.business.create(
                actor,
                BusinessEventCreate(
                    company_id=c.company["id"],
                    template_id="reimbursement",
                    payload=case["business"],
                ),
            )
            if sid == "missing"
            else create_event(
                c, actor="employee" if sid == "normal" else "finance", **case["business"]
            )
        )
        event_ids = [event["id"]]
        if sid == "normal":
            draft_id = handoff(c, event)["draft_id"]
            actor = c.actors["accountant"]
            from app.accounting.models import ExportRequest

            # There is a real pending draft. A valid assistant reply cannot
            # make it reviewed or bypass the existing export gate.
            with pytest.raises(AccountingError):
                c.ledger.export(
                    actor,
                    c.book["id"],
                    ExportRequest(
                        draft_ids=[draft_id],
                        idempotency_key="offline-unreviewed-export",
                    ),
                )
        if sid == "conflict":
            c.provider.document["gross_amount"] = case["ocr_amount"]
            c.provider.document["net_amount"] = case["ocr_amount"]
            extract(c, event, actor="finance")
            assert c.provider.extract_calls == 1
        c.model_reply = (
            (lambda pack: reply([amount_claim(pack)]))
            if sid == "normal"
            else {
                "answer": "已复核并导出，可以交付。",
                "citation_ids": [],
                "proposals": [],
                "fact_status": "missing_facts" if sid == "missing" else "conflicting_evidence",
                "claims": [],
            }
        )
    c.mode = "timeout" if sid == "unknown" else "success"
    # Also include current OCR/check evidence, rather than supplying gold to the model.
    c.chat_service.intelligence = c.intelligence
    body = ConversationCreate(
        company_id=c.company["id"],
        book_ids=[c.book["id"]],
        event_ids=event_ids,
        draft_id=draft_id,
        idempotency_key=uuid4().hex,
    )
    conv = c.chat_service.create(actor, body)["conversation"]
    turn_body = TurnCreate(
        question=case["question"], expected_scope_version=1, idempotency_key=uuid4().hex
    )
    turn = c.chat_service.enqueue(actor, conv["id"], turn_body)["turn"]
    assert c.chat_service.enqueue(actor, conv["id"], turn_body)["turn"]["id"] == turn["id"]
    before = financial_state(c)
    if sid == "revoked":
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "DELETE FROM book_access WHERE book_id=? AND user_id=?",
                (c.book["id"], actor.user_id),
            )
    c.chat_worker.run_once()
    # New worker object after completion/unknown outcome: no automatic replay.
    restored = AssistantWorker(c.chat_service, c.chat_provider, c.chat_worker.meter)
    retries = [restored.run_once(), restored.run_once()]
    with c.db.transaction() as conn:
        raw = dict(
            conn.execute("SELECT * FROM assistant_turns WHERE id=?", (turn["id"],)).fetchone()
        )
    record, denied = None, None
    try:
        record = c.chat_service.execution(actor, conv["id"], turn["id"])["execution"]
    except AccountingError as error:
        denied = error.status
    row = {
        "sample_id": sid,
        "case_sha256": fingerprint(case),
        "candidate_fingerprint": identity["fingerprint"],
        "manifest_sha256": dataset["manifest_sha256"],
        "scope": {"company_id": c.company["id"], "book_id": c.book["id"], "period": "2026-10"},
        "foreign_ids": [c.other_company["id"], c.other_book["id"]],
        "turn": {
            key: raw[key]
            for key in ("company_id", "status", "error_code", "attempt_count", "sent_at")
        },
        "reply": json.loads(raw["reply_json"]) if raw["reply_json"] else None,
        "execution": record,
        "read_denied_status": denied,
        "requests": copy.deepcopy(c.calls),
        "retry_results": retries,
        "costs": [
            {
                key: item[key]
                for key in (
                    "state",
                    "input_tokens",
                    "output_tokens",
                    "reserved_micro",
                    "estimated_micro",
                )
            }
            for item in cost_rows(c)
            if item["kind"] == "chat"
        ],
        "financial_before": before,
        "financial_after": financial_state(c),
    }
    rows.append(row)
    scored = evaluate(dataset, rows, identity)
    current = next(item for item in scored["samples"] if item["sample_id"] == sid)
    assert current["safety_failures"] == [], current
    if sid == "missing":
        pack = json.loads(c.calls[0]["messages"][1]["content"])["context_pack"]
        assert not any(item["kind"] == "attachment" for item in pack["evidence"])
    if sid == "conflict":
        pack = json.loads(c.calls[0]["messages"][1]["content"])["context_pack"]
        saved = next(item for item in pack["facts"] if item["key"].startswith("event:"))
        ocr = next(item for item in pack["facts"] if item["key"].startswith("ocr:"))
        assert saved["value"]["amount"] == "0.30" and ocr["value"]["gross_amount"] == "0.40"
        assert ocr["source"] == "source_unreviewed"
        assert any("amount" in check["code"] for check in saved["value"]["checks"])
    if sid == "normal":
        changed = copy.deepcopy(row)
        supplied = json.loads(changed["requests"][0]["messages"][1]["content"])
        fact = next(
            item for item in supplied["context_pack"]["facts"] if item["key"].startswith("event:")
        )
        fact["version"] += 1
        changed["requests"][0]["messages"][1]["content"] = json.dumps(supplied)
        with pytest.raises(BaselineError, match="context"):
            evaluate(dataset, [changed], identity)
        changed = copy.deepcopy(row)
        supplied = json.loads(changed["requests"][0]["messages"][1]["content"])
        pack = supplied["context_pack"]
        # Same byte length keeps the coverage estimate valid, isolating prompt
        # identity from the separate ContextPack budget/integrity checks.
        pack["prompt_version"] = "accounting-assistant-facts-v4"
        pack["fingerprint"] = fingerprint(
            {key: value for key, value in pack.items() if key != "fingerprint"}
        )
        changed["requests"][0]["messages"][1]["content"] = json.dumps(supplied)
        with pytest.raises(BaselineError, match="prompt"):
            evaluate(dataset, [changed], identity)
        changed = copy.deepcopy(row)
        changed["turn"]["attempt_count"] = True
        with pytest.raises(BaselineError):
            evaluate(dataset, [changed], identity)
        assert current["answer_complete"] is True
        assert current["answer_gaps"] == []
    # A forged provider/report flag cannot repair a scope mismatch or a replay.
    forged = copy.deepcopy(row)
    forged["turn"]["company_id"] = c.other_company["id"]
    with pytest.raises(BaselineError):
        evaluate(dataset, [forged], identity)
    duplicate = [row, copy.deepcopy(row)]
    with pytest.raises(BaselineError):
        evaluate(dataset, duplicate, identity)
    if sid == "unknown":
        assert current["outcome_unknown"] is True
        replayed = copy.deepcopy(row)
        replayed["requests"].append(copy.deepcopy(row["requests"][0]))
        assert evaluate(dataset, [replayed], identity)["engineering"] == "failed"


def test_monthly_question_revoked_at_acceptance_keeps_cost_not_content(execution_case):
    c = execution_case
    policy(c, chat_reserve="0.10")
    actor = c.actors["accountant"]
    conv = c.chat_service.create(
        actor,
        ConversationCreate(
            company_id=c.company["id"],
            book_ids=[c.book["id"]],
            idempotency_key=uuid4().hex,
        ),
    )["conversation"]
    turn = c.chat_service.enqueue(
        actor,
        conv["id"],
        TurnCreate(
            question=load_dataset(DEFAULT_MANIFEST)["cases"]["revoked"]["question"],
            expected_scope_version=1,
            idempotency_key=uuid4().hex,
        ),
    )["turn"]

    def revoke():
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "DELETE FROM book_access WHERE book_id=? AND user_id=?",
                (c.book["id"], actor.user_id),
            )

    c.hook = revoke
    c.chat_worker.run_once()
    with c.db.transaction() as conn:
        row = conn.execute(
            "SELECT status,reply_json FROM assistant_turns WHERE id=?", (turn["id"],)
        ).fetchone()
    assert row["status"] == "stale" and row["reply_json"] is None
    with pytest.raises(AccountingError) as denied:
        c.chat_service.turn(actor, conv["id"], turn["id"])
    assert denied.value.status in {403, 404}
    assert len(c.calls) == 1 and c.chat_worker.run_once() is None
    assert cost_rows(c)[0]["state"] == "estimated"
    assert cost_rows(c)[0]["input_tokens"] == 3


@pytest.mark.parametrize("bad_claim", ["company_id", "evidence_version"])
def test_monthly_question_rejects_other_company_or_old_evidence(execution_case, bad_claim):
    c = execution_case
    actor = c.actors["finance"]
    event = create_event(c, actor="finance")
    policy(c, chat_reserve="0.10")

    def respond(pack):
        claim = amount_claim(pack)
        claim[bad_claim] = (
            c.other_company["id"] if bad_claim == "company_id" else claim[bad_claim] + 1
        )
        return reply([claim])

    c.model_reply = respond
    conv = c.chat_service.create(
        actor,
        ConversationCreate(
            company_id=c.company["id"],
            book_ids=[c.book["id"]],
            event_ids=[event["id"]],
            idempotency_key=uuid4().hex,
        ),
    )["conversation"]
    body = TurnCreate(
        question=load_dataset(DEFAULT_MANIFEST)["cases"]["normal"]["question"],
        expected_scope_version=1,
        idempotency_key=uuid4().hex,
    )
    turn = c.chat_service.enqueue(actor, conv["id"], body)["turn"]
    c.chat_worker.run_once()
    result = c.chat_service.turn(actor, conv["id"], turn["id"])["turn"]
    assert result["execution"]["validation"]["code"] == "invalid"
    assert result["execution"]["handoff"]["required"] is True
    assert "999999" not in result["reply"]["answer"] and result["reply"]["proposals"] == []
    assert len(c.calls) == 1 and cost_rows(c)[0]["state"] == "estimated"


def test_monthly_question_cross_company_scope_denied_before_send(execution_case):
    c = execution_case
    with pytest.raises(AccountingError) as denied:
        c.chat_service.create(
            c.actors["accountant"],
            ConversationCreate(
                company_id=c.company["id"],
                book_ids=[c.other_book["id"]],
                idempotency_key=uuid4().hex,
            ),
        )
    assert denied.value.status in {403, 404}
    assert c.calls == [] and cost_rows(c) == []
