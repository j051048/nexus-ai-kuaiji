"""AK03 real worker publication boundary; synthetic HTTP and isolated SQLite."""

import json

import httpx
import pytest

from app.assistant.provider import AssistantProvider
from app.assistant.worker import AssistantWorker
from app.intelligence.provider import AISettings, OpenAICompatibleProvider
from test_business import business, create
from test_ak02_assistant_context import runtime, run, thread


def configure(c, response):
    c.requests.clear()

    def respond(request):
        payload = json.loads(request.content)
        c.requests.append(payload)
        pack = json.loads(payload["messages"][1]["content"])["context_pack"]
        data = response(pack) if callable(response) else response
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        })

    settings = AISettings(base_url="https://supplier.example.test/v1", api_key="synthetic-key", chat_model="synthetic-chat")
    c.provider = AssistantProvider(OpenAICompatibleProvider(settings, httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)))
    c.service.provider = c.provider
    c.worker = AssistantWorker(c.service, c.provider, c.meter)


def amount_claim(pack):
    fact = next(row for row in pack["facts"] if row["key"].startswith("event:"))
    return {
        "fact_key": fact["key"], "path": "/amount", "field": "amount", "value": fact["value"]["amount"],
        "company_id": pack["scope"]["company_id"], "book_id": None,
        "evidence_version": fact["version"], "evidence_id": fact["evidence_id"],
        "source": fact["source"], "unit": "元", "currency": "CNY",
    }


def reply(claims=None, **updates):
    data = {"answer": "利润已确认999999元，凭证已审核并导出。", "citation_ids": [], "proposals": []}
    if claims is not None:
        data.update(fact_status="verified", claims=claims)
    data.update(updates)
    return data


def test_unbound_financial_answer_never_published_and_actions_are_removed(business):
    c = runtime(business)
    configure(c, reply(proposals=[{"kind": "ocr_batch", "reason": "利润已审核999999元"}]))
    result = run(c, thread(c, book_ids=[c.book]))
    assert result["status"] == "succeeded"
    assert "999999" not in result["reply"]["answer"]
    assert "人工" in result["reply"]["answer"]
    assert result["reply"]["proposals"] == []
    assert set(result["reply"]) == {"answer", "citation_ids", "proposals"}
    assert c.meter.finishes[-1]["result"].usage.total_tokens == 5


def test_verified_fact_uses_bound_current_value_and_safe_server_label(business):
    event = create(business, amount="120.00")
    c = runtime(business, business.actors["employee"])
    configure(c, lambda pack: reply([amount_claim(pack)]))
    result = run(c, thread(c, event_ids=[event["id"]]))
    assert result["status"] == "succeeded"
    answer = result["reply"]["answer"]
    assert "120.00" in answer and "已保存" in answer
    assert "999999" not in answer and "已审核并导出" not in answer
    assert result["reply"]["citation_ids"] == [f"event:{event['id']}:v{event['fact_version']}"]


@pytest.mark.parametrize("defect", ["value", "company", "book", "version", "evidence", "path", "field", "currency", "unit", "qualification"])
def test_claim_binding_failures_do_not_publish_financial_content(business, defect):
    event = create(business, amount="120.00")
    c = runtime(business, business.actors["employee"])

    def invalid(pack):
        claim = amount_claim(pack)
        key, value = {
            "value": ("value", "100.00"), "company": ("company_id", business.other["id"]),
            "book": ("book_id", business.other_book["id"]), "version": ("evidence_version", 999),
            "evidence": ("evidence_id", "event:foreign:v1"), "path": ("path", "/description"),
            "field": ("field", "profit"), "currency": ("currency", "USD"),
            "unit": ("unit", "万元"), "qualification": ("source", "approved"),
        }[defect]
        claim[key] = value
        return reply([claim])

    configure(c, invalid)
    result = run(c, thread(c, event_ids=[event["id"]]))
    assert result["status"] == "succeeded"
    assert "120.00" not in result["reply"]["answer"] and "999999" not in result["reply"]["answer"]
    assert "人工" in result["reply"]["answer"]
    assert len(c.requests) == 1 and c.meter.finishes[-1]["result"].usage.total_tokens == 5


@pytest.mark.parametrize("status", ["missing_facts", "conflicting_evidence", "unsupported_case", "requires_accountant"])
def test_explicit_abstention_renders_fixed_safe_reason(business, status):
    c = runtime(business)
    configure(c, reply([], fact_status=status))
    result = run(c, thread(c))
    assert result["status"] == "succeeded"
    assert "999999" not in result["reply"]["answer"] and "人工" in result["reply"]["answer"]
    assert result["reply"]["proposals"] == []


def test_valid_citation_does_not_authorize_an_arbitrary_claim_or_history_answer(business):
    event = create(business, amount="120.00")
    c = runtime(business, business.actors["employee"])
    configure(c, reply(citation_ids=[f"event:{event['id']}:v{event['fact_version']}"]))
    conv = thread(c, event_ids=[event["id"]])
    assert "999999" not in run(c, conv)["reply"]["answer"]
    assert "999999" not in run(c, conv, "继续说明") ["reply"]["answer"]
    pack = json.loads(c.requests[-1]["messages"][1]["content"])["context_pack"]
    assert "999999" not in json.dumps(pack)


def test_unrecognized_citation_remains_hard_failure(business):
    c = runtime(business)
    configure(c, reply(citation_ids=["book:foreign"]))
    result = run(c, thread(c))
    assert result["status"] == "failed" and result["error_code"] == "citation_invalid"


def test_source_gross_total_cannot_be_relabelled_profit(business):
    c = runtime(business)

    def invalid(pack):
        fact = next(row for row in pack["facts"] if row["key"].startswith("book:"))
        claim = {"fact_key": fact["key"], "path": "/source_gross_total", "field": "profit", "value": fact["value"]["source_gross_total"],
                 "company_id": c.company, "book_id": c.book, "evidence_version": fact["version"], "evidence_id": fact["evidence_id"],
                 "source": fact["source"], "currency": "CNY", "unit": "元"}
        return reply([claim])

    configure(c, invalid)
    result = run(c, thread(c, book_ids=[c.book]))
    assert result["status"] == "succeeded" and "利润" not in result["reply"]["answer"]
    assert "人工" in result["reply"]["answer"]


def ocr_pack(gross="120.00"):
    from test_ak02_assistant_context import compile_pack, minimal_snapshot

    snapshot = minimal_snapshot()
    snapshot["events"] = [{"id": "event-a", "fact_version": 2, "amount": "120.00", "documents": [
        {"attachment_id": "attachment-a", "extraction_id": "run-a", "document_index": 0,
         "gross_amount": gross, "net_amount": "100.00", "tax_amount": "20.00", "currency": "CNY"},
    ]}]
    snapshot["evidence"] = [
        {"id": "event:event-a:v2", "kind": "event", "event_id": "event-a"},
        {"id": "attachment:attachment-a", "kind": "attachment", "event_id": "event-a", "attachment_id": "attachment-a"},
    ]
    return compile_pack(snapshot)


def ocr_claim(pack):
    fact = next(row for row in pack.as_dict()["facts"] if row["key"].startswith("ocr:"))
    return {
        "fact_key": fact["key"], "path": "/gross_amount", "field": "gross_amount", "value": fact["value"]["gross_amount"],
        "company_id": "company-a", "book_id": None, "evidence_id": fact["evidence_id"],
        "evidence_version": fact["version"], "source": fact["source"], "unit": "元", "currency": "CNY",
    }


def test_ocr_claim_retains_unreviewed_qualification_and_original_attachment_citation():
    from app.assistant.fact_validation import render_assistant_reply

    pack = ocr_pack()
    rendered = render_assistant_reply(reply([ocr_claim(pack)]), pack)
    assert "120.00" in rendered.answer and "待人工核验的 OCR" in rendered.answer
    assert rendered.citation_ids == ["attachment:attachment-a"]
    assert "999999" not in rendered.answer and "已审核" not in rendered.answer
    assert set(rendered.model_dump()) == {"answer", "citation_ids", "proposals"}


@pytest.mark.parametrize("value", [None, "0.00"])
def test_unknown_is_not_zero_but_explicit_source_zero_is_known(value):
    from app.assistant.fact_validation import render_assistant_reply

    pack = ocr_pack(value)
    claim = ocr_claim(pack)
    claim["value"] = "0.00"
    rendered = render_assistant_reply(reply([claim]), pack)
    assert ("0.00" in rendered.answer) is (value == "0.00")


def test_no_partial_fact_publication_when_one_claim_is_invalid():
    from app.assistant.fact_validation import render_assistant_reply

    pack = ocr_pack()
    claim = ocr_claim(pack)
    invalid = {**claim, "path": "/tax_amount", "field": "tax_amount", "value": "999.00"}
    rendered = render_assistant_reply(reply([claim, invalid]), pack)
    assert "120.00" not in rendered.answer and "人工" in rendered.answer


def test_duplicate_fact_claims_cannot_amplify_a_verified_amount():
    from app.assistant.fact_validation import render_assistant_reply

    pack = ocr_pack()
    claim = ocr_claim(pack)
    rendered = render_assistant_reply(reply([claim, claim]), pack)
    assert "120.00" not in rendered.answer and "人工" in rendered.answer


def test_book_totals_keep_source_label_and_count_units_with_explicit_book_binding():
    from app.assistant.fact_validation import render_assistant_reply
    from test_ak02_assistant_context import compile_pack, minimal_snapshot

    snapshot = minimal_snapshot()
    snapshot["books"] = [{"id": "book-a", "period": "2026-10", "source_gross_total": "120.00", "source_count": 2}]
    snapshot["evidence"] = [{"id": "book:book-a", "kind": "book", "book_id": "book-a"}]
    pack = compile_pack(snapshot)
    fact = next(row for row in pack.facts if row["key"] == "book:book-a")
    claims = []
    for field, value, unit, currency in [("source_gross_total", "120.00", "元", "CNY"), ("source_count", 2, "条", "")]:
        claims.append({"fact_key": fact["key"], "path": "/" + field, "field": field, "value": value,
                       "company_id": "company-a", "book_id": "book-a", "evidence_id": fact["evidence_id"],
                       "evidence_version": fact["version"], "source": fact["source"], "unit": unit, "currency": currency})
    rendered = render_assistant_reply(reply(claims), pack)
    assert "资料来源绝对金额合计（含隔离及替代记录）：120.00元" in rendered.answer
    assert "资料来源数量：2条" in rendered.answer
    assert "利润" not in rendered.answer and rendered.citation_ids == ["book:book-a"]


def test_supplier_strict_schema_requires_every_envelope_field_but_local_legacy_fallback_remains():
    from app.assistant.models import AssistantModelReply

    schema = AssistantModelReply.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    for definition in schema.get("$defs", {}).values():
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False
            assert set(definition["required"]) == set(definition["properties"])
    legacy = AssistantModelReply.model_validate({"answer": "未绑定旧答复", "citation_ids": [], "proposals": []})
    assert legacy.fact_status == "requires_accountant" and legacy.claims == []
