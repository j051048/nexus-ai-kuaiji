import pytest

from tools.ai_inference_lab.context import (
    BudgetExceeded,
    ContextError,
    Evidence,
    Fact,
    HistoryItem,
    compile_context_pack,
    load_context_pack,
)


def args(**changes):
    evidence = Evidence("a1", "x1", "d1", 1, "gross_amount", "100.00")
    base = dict(
        task="journal_suggest",
        scope={"firm_id": "f", "company_id": "c", "book_id": "b", "period": "2026-10"},
        facts=[Fact("gross_amount", "100.00", 2, "approved_fact", evidence.identity)],
        evidence=[evidence],
        rules=[{"key": "rule-1", "version": "1", "content": "办公费"}],
        budget={"input_tokens": 2000, "output_reserve": 200},
        prompt_version="p1",
        schema_version="s1",
        policy_version="pol1",
    )
    base.update(changes)
    return base


def test_b01_same_attachment_two_documents_keep_independent_identity():
    evidence = [
        Evidence("a1", "x1", "d1", 1, "gross_amount", "100.00"),
        Evidence("a1", "x1", "d2", 2, "gross_amount", "50.00"),
    ]
    pack = compile_context_pack(**args(evidence=evidence, facts=[]))
    assert len(pack.evidence) == 2
    assert {row["document_id"] for row in pack.evidence} == {"d1", "d2"}
    assert len({row["id"] for row in pack.evidence}) == 2


def test_b02_over_budget_keeps_facts_and_reports_omitted_history():
    history = [HistoryItem("q", "a" * 2000, 2) for _ in range(4)]
    pack = compile_context_pack(**args(history=history, budget={"input_tokens": 1200, "output_reserve": 200}))
    assert pack.coverage["included"]["facts"] == 1
    assert pack.coverage["included"]["evidence"] == 1
    assert pack.coverage["omitted"]["history"] >= 1
    assert pack.coverage["complete_required_facts"] is True


def test_b03_revised_fact_marks_old_history_stale():
    history = [HistoryItem("金额？", "旧金额 80", 1)]
    pack = compile_context_pack(**args(history=history))
    assert pack.history[0]["stale"] is True
    assert pack.history[0]["answer"] == ""


def test_b05_prompt_schema_policy_and_content_change_fingerprint():
    one = compile_context_pack(**args())
    two = compile_context_pack(**args(prompt_version="p2"))
    three = compile_context_pack(**args(schema_version="s2"))
    four = compile_context_pack(**args(policy_version="pol2"))
    five = compile_context_pack(**args(rules=[{"key": "rule-1", "version": "1", "content": "差旅费"}]))
    assert len({one.fingerprint, two.fingerprint, three.fingerprint, four.fingerprint, five.fingerprint}) == 5


def test_b06_b07_required_fact_and_budget_fail_closed():
    with pytest.raises(ContextError) as missing:
        compile_context_pack(**args(required_keys=["tax_amount"]))
    assert missing.value.code == "context_required_missing"
    with pytest.raises(BudgetExceeded):
        compile_context_pack(**args(budget={"input_tokens": 100, "output_reserve": 50}))


def test_b08_unknown_version_rejected_and_current_round_trips():
    current = compile_context_pack(**args())
    assert load_context_pack(current.as_dict()).fingerprint == current.fingerprint
    payload = current.as_dict()
    payload["version"] = "context-pack-v0"
    with pytest.raises(ContextError) as caught:
        load_context_pack(payload)
    assert caught.value.code == "context_version_unknown"
