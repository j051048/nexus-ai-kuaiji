"""Shared compiler regressions, deliberately independent of provider/network/DB."""
import copy
from pathlib import Path
import subprocess
import sys

import pytest

from tools.ai_inference_lab import context as ctx


def arguments(**changes):
    evidence = ctx.Evidence("a", "x", "d", 1, "gross", "120.00", 2)
    result = dict(
        task="journal_suggest",
        scope={"firm_id": "f", "company_id": "c", "book_id": "b", "period": "2026-10"},
        facts=[ctx.Fact("gross", "120.00", 2, "approved_fact", evidence.identity)],
        evidence=[evidence],
        budget={"input_tokens": 4000, "output_reserve": 200},
        prompt_version="p1", schema_version="s1", policy_version="pol1",
    )
    result.update(changes)
    return result


def resign(payload):
    payload["fingerprint"] = ctx.fingerprint({key: value for key, value in payload.items() if key != "fingerprint"})
    return payload


@pytest.mark.parametrize("bad", [None, True, 10, [], "", "  "])
def test_scope_identity_is_not_coerced_to_text(bad):
    scope = arguments()["scope"]
    scope["company_id"] = bad
    with pytest.raises(ctx.ContextError):
        ctx.compile_context_pack(**arguments(scope=scope))


@pytest.mark.parametrize("period", ["2026-13", "2026-00", "2026-10-01", "0000-01"])
def test_period_is_a_real_accounting_month(period):
    scope = arguments()["scope"]
    scope["period"] = period
    with pytest.raises(ctx.ContextError):
        ctx.compile_context_pack(**arguments(scope=scope))


@pytest.mark.parametrize("value", [True, 3.5, "4000", None])
def test_budget_does_not_coerce_bool_fraction_or_text(value):
    with pytest.raises(ctx.ContextError):
        ctx.compile_context_pack(**arguments(budget={"input_tokens": value, "output_reserve": 200}))


def test_conflicting_same_evidence_identity_is_not_silently_deduplicated():
    one = arguments()["evidence"][0]
    two = ctx.Evidence("a", "x", "d", 1, "gross", "100.00", 2)
    with pytest.raises(ctx.ContextError):
        ctx.compile_context_pack(**arguments(evidence=[one, two]))


def test_evidence_page_and_fact_source_are_validated():
    with pytest.raises(ctx.ContextError):
        ctx.compile_context_pack(**arguments(evidence=[ctx.Evidence("a", "x", "d", True, "gross", "120.00")], facts=[]))
    with pytest.raises(ctx.ContextError):
        ctx.compile_context_pack(**arguments(facts=[ctx.Fact("gross", "120.00", 2, "made_up")]))


def test_json_values_are_detached_from_caller_and_export():
    value = {"nested": ["current"]}
    evidence = ctx.Evidence("a", "x", "d", 1, "details", value)
    pack = ctx.compile_context_pack(**arguments(evidence=[evidence], facts=[ctx.Fact("details", value, 1, "saved_fact", evidence.identity)]))
    value["nested"].append("changed")
    assert pack.facts[0]["value"] == {"nested": ["current"]}
    assert pack.evidence[0]["value"] == {"nested": ["current"]}
    exported = pack.as_dict()
    exported["facts"][0]["value"]["nested"].append("export-only")
    assert pack.facts[0]["value"] == {"nested": ["current"]}


def test_required_rule_cannot_be_cut_to_fit_budget():
    rule = {"key": "boundaries", "version": "1", "content": "禁止自动过账" * 2000, "required": True}
    with pytest.raises(ctx.BudgetExceeded):
        ctx.compile_context_pack(**arguments(rules=[rule], budget={"input_tokens": 1200, "output_reserve": 200}))


def test_optional_history_and_rules_report_trimming_but_keep_required_rules():
    rules = [
        {"key": "boundaries", "version": "1", "content": "仅建议", "required": True},
        {"key": "optional", "version": "1", "content": "参考" * 4000},
    ]
    pack = ctx.compile_context_pack(**arguments(rules=rules, history=[ctx.HistoryItem("q", "x" * 4000, 2)], budget={"input_tokens": 1600, "output_reserve": 200}))
    assert [row["key"] for row in pack.rules] == ["boundaries"]
    assert pack.rules[0]["required"] is True
    assert pack.coverage["omitted"]["rules"] == 1
    assert pack.coverage["omitted"]["history"] == 1
    assert pack.coverage["estimated_tokens"] == ctx.estimate_tokens(pack.as_dict())
    assert pack.coverage["estimated_tokens"] <= 1400
    assert ctx.load_context_pack(pack.as_dict()).as_dict() == pack.as_dict()


def test_utf8_estimate_and_final_metadata_are_inside_budget():
    assert ctx.estimate_tokens("中文") >= len(ctx.canonical("中文").encode("utf-8")) // 3
    # Keep the number of budget digits unchanged so that changing the cap does
    # not also shorten the serialized metadata by a token at this boundary.
    pack = ctx.compile_context_pack(**arguments(budget={"input_tokens": 999999, "output_reserve": 900000}))
    assert pack.coverage["estimated_tokens"] == ctx.estimate_tokens(pack.as_dict())
    limit = pack.coverage["estimated_tokens"] - 1
    with pytest.raises(ctx.BudgetExceeded):
        ctx.compile_context_pack(**arguments(budget={"input_tokens": limit + 900000, "output_reserve": 900000}))


def test_load_rejects_tampering_even_for_known_version():
    payload = ctx.compile_context_pack(**arguments()).as_dict()
    payload["facts"][0]["value"] = "0.00"
    with pytest.raises(ctx.ContextError):
        ctx.load_context_pack(payload)


@pytest.mark.parametrize("change", ["scope", "reference", "evidence_id", "coverage", "version", "history"])
def test_load_checks_structure_and_references_not_only_self_reported_hash(change):
    payload = ctx.compile_context_pack(**arguments()).as_dict()
    if change == "scope":
        payload["scope"]["company_id"] = None
    elif change == "reference":
        payload["facts"][0]["evidence_id"] = "missing"
    elif change == "evidence_id":
        payload["evidence"][0]["id"] = "arbitrary"
    elif change == "coverage":
        payload["coverage"]["included"]["evidence"] = 0
    elif change == "version":
        payload["fact_version"] = 0
    else:
        payload["history"] = [{"question": "q", "answer": "untrusted old answer", "fact_version": 0, "evidence_ids": [], "stale": False}]
    with pytest.raises(ctx.ContextError):
        ctx.load_context_pack(resign(payload))


def test_company_scope_and_source_evidence_do_not_invent_accounting_identity():
    source = ctx.SourceEvidence("attachment:1:sha", "raw_attachment", {"sha256": "a" * 64})
    scope = {"kind": "company", "firm_id": "f", "company_id": "c", "book_id": None, "period": None, "books": []}
    pack = ctx.compile_context_pack(**arguments(task="invoice_extract", scope=scope, evidence=[source], facts=[ctx.Fact("attachment", source.value, 1, "source_unreviewed", source.identity)]))
    assert pack.scope["book_id"] is None and pack.scope["period"] is None
    assert pack.evidence[0]["source_id"] == source.source_id
    assert "extraction_id" not in pack.evidence[0]
    assert ctx.load_context_pack(pack.as_dict()).fingerprint == pack.fingerprint
    multi = copy.deepcopy(scope)
    multi["books"] = [{"book_id": "b1", "period": "2026-10"}, {"book_id": "b2", "period": "2026-11"}]
    assert ctx.compile_context_pack(**arguments(task="assistant_reply", scope=multi)).scope["books"] == multi["books"]


def test_source_identity_changes_with_kind_or_version_and_requires_reference():
    one = ctx.SourceEvidence("source", "database_record", {"value": 10})
    two = ctx.SourceEvidence("source", "page_text", {"value": 10})
    three = ctx.SourceEvidence("source", "database_record", {"value": 10}, 2)
    assert len({one.identity, two.identity, three.identity}) == 3
    for origin in ("saved_fact", "source_unreviewed"):
        with pytest.raises(ctx.ContextError):
            ctx.compile_context_pack(**arguments(evidence=[], facts=[ctx.Fact("a", 1, 1, origin, "unknown")]))


def test_company_scope_cannot_mix_unscoped_book_or_duplicate_members():
    scope = {"kind": "company", "firm_id": "f", "company_id": "c", "book_id": "b", "period": None, "books": []}
    with pytest.raises(ctx.ContextError):
        ctx.compile_context_pack(**arguments(scope=scope))
    scope["book_id"] = None
    scope["books"] = [{"book_id": "b", "period": "2026-10"}] * 2
    with pytest.raises(ctx.ContextError):
        ctx.compile_context_pack(**arguments(scope=scope))


def test_duplicate_fact_keys_and_non_json_data_are_rejected():
    with pytest.raises(ctx.ContextError):
        ctx.compile_context_pack(**arguments(facts=[ctx.Fact("x", 1, 0, "user_text"), ctx.Fact("x", 2, 0, "user_text")]))
    with pytest.raises(ctx.ContextError):
        ctx.compile_context_pack(**arguments(facts=[ctx.Fact("x", {1: "not a JSON object key"}, 0, "user_text")]))


def test_backend_owns_the_single_implementation():
    from app.intelligence import context_pack

    assert ctx.compile_context_pack is context_pack.compile_context_pack
    assert ctx.ContextPack is context_pack.ContextPack


def test_root_only_offline_import_shares_class_identity_with_later_backend_import():
    root = Path(__file__).resolve().parents[2]
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(root)!r}); "
        "from tools.ai_inference_lab import context; "
        f"sys.path.insert(0, {str(root / 'nexus_backend')!r}); "
        "from app.intelligence import context_pack; "
        "assert context.ContextPack is context_pack.ContextPack; "
        "assert context.compile_context_pack is context_pack.compile_context_pack"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_backend_only_packaging_does_not_import_offline_tools():
    root = Path(__file__).resolve().parents[2]
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(root / 'nexus_backend')!r}); "
        "from app.intelligence import context_pack; "
        "assert context_pack.CONTEXT_VERSION == 'context-pack-v2'; "
        "assert 'tools' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
