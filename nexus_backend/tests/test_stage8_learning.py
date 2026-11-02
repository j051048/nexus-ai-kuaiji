from period_fixtures import restore_period_state
import json
import sqlite3

import pytest
from pydantic import ValidationError

from app.accounting.catalog import CatalogService
from app.accounting.catalog_models import AccountDefinition, AccountUpdate
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, DraftUpdate, ImportRequest, SourceRow
from app.learning.models import RuleCreate, RuleDefinition, RuleEvaluate, RulePublish, RuleRetire, RuleUpdate
from app.learning.service import LearningService
from test_business import business, approved, handoff
from test_catalog import case
from app.accounting.seed import LOCAL_ACTOR
from review_policy_fixtures import independent_reviewer


def bank_sample(ledger, learning, book, actor, external="bank-one", counterparty="供应商甲", amount="113.00", corrected=True, code="6602.99"):
    ledger.learning = learning
    ledger.import_sources(actor, book, ImportRequest(source_name="学习测试", rows=[SourceRow(external_id=external, date="2026-10-03", description="办公耗材", counterparty=counterparty, amount=amount, direction="expense", evidence_ref="已核验归档票据")]))
    ledger.suggestions(actor, book)
    with ledger.db.transaction(actor=actor) as conn:
        draft_id = conn.execute("SELECT d.id FROM drafts d JOIN sources s ON s.id=d.source_id WHERE s.book_id=? AND s.external_id=?", (book, external)).fetchone()[0]
    draft = ledger.get_draft(actor, book, draft_id)
    if corrected:
        lines = [{**line, "account_code": code if line["account_code"] != "1002" else "1002"} for line in draft["lines"]]
        draft = ledger.update_draft(actor, book, draft_id, DraftUpdate(expected_version=draft["version"], description="会计核实为其他管理费用", lines=lines, correction_reason="合同用途与原办公分类不同"))
    reviewer = independent_reviewer(ledger.db, actor, book)
    ledger.approve(reviewer, book, draft_id, ApprovalRequest(expected_version=draft["version"]))
    with ledger.db.transaction(actor=actor) as conn:
        return dict(conn.execute("SELECT * FROM learning_samples WHERE draft_id=?", (draft_id,)).fetchone())


@pytest.fixture
def ready(case):
    ledger, catalog, book = case
    learning = LearningService(ledger.db)
    positive = bank_sample(ledger, learning, book, LOCAL_ACTOR)
    negative = bank_sample(ledger, learning, book, LOCAL_ACTOR, external="bank-negative", counterparty="供应商乙", corrected=False)
    rule = learning.overview(LOCAL_ACTOR, book)["rules"][0]
    return ledger, catalog, learning, book, positive, negative, rule


def evaluate_publish(ready):
    _, _, learning, book, _, negative, rule = ready
    result = learning.evaluate(LOCAL_ACTOR, book, rule["id"], RuleEvaluate(expected_version=rule["current_version"], negative_sample_ids=[negative["id"]]))
    assert result["passed"]
    rule = learning.publish(LOCAL_ACTOR, book, rule["id"], RulePublish(expected_version=rule["current_version"], expected_state_version=rule["state_version"], evaluation_id=result["id"], reason="已核对真实合同用途，正反例通过"))
    return rule, result


def test_approval_captures_original_final_provenance_and_only_draft_rule(ready):
    ledger, _, learning, book, positive, _, rule = ready
    result = learning.get_sample(LOCAL_ACTOR, book, positive["id"])
    assert result["corrected"] and result["reviewed_version"] == 2
    assert result["original"]["lines"][0]["account_code"] == "6602.02"
    assert result["final"]["lines"][0]["account_code"] == "6602.99"
    assert result["corrections"][0]["reason"] == "合同用途与原办公分类不同"
    assert "evidence_ref" not in result["snapshot"]["source"]
    assert len(result["evidence_fingerprint"]) == 64
    assert rule["status"] == "draft"
    with ledger.db.transaction(actor=LOCAL_ACTOR) as conn:
        assert learning.rule_generate_in_transaction(conn, LOCAL_ACTOR, book, "daily_cashflow", result["snapshot"]["payload"], 11300, source_kind="bank") is None


def test_published_rule_uses_new_amount_not_sample_amount_and_never_business(ready):
    ledger, _, learning, book, positive, _, _ = ready
    rule, _ = evaluate_publish(ready)
    payload = {**json.loads(positive["snapshot_json"])["payload"], "amount": "27.01"}
    with ledger.db.transaction(actor=LOCAL_ACTOR) as conn:
        generated = learning.rule_generate_in_transaction(conn, LOCAL_ACTOR, book, "daily_cashflow", payload, 2701, source_kind="bank")
        assert generated["lines"] == [("6602.99", 2701, 0), ("1002", 0, 2701)]
        assert generated["rule_id"] == rule["id"]
        assert learning.rule_generate_in_transaction(conn, LOCAL_ACTOR, book, "daily_cashflow", payload, 2701) is None


def test_publish_requires_real_distinct_negative_and_passing_receipt(ready):
    _, _, learning, book, positive, negative, rule = ready
    with pytest.raises(AccountingError, match="不同"):
        learning.evaluate(LOCAL_ACTOR, book, rule["id"], RuleEvaluate(expected_version=1, negative_sample_ids=[positive["id"]]))
    with pytest.raises(AccountingError, match="先通过"):
        learning.publish(LOCAL_ACTOR, book, rule["id"], RulePublish(expected_version=1, expected_state_version=1, evaluation_id="fabricated", reason="不能伪造评估"))
    changed = {**rule["definition"], "conditions": {**rule["definition"]["conditions"], "counterparty": "供应商乙"}}
    with pytest.raises(AccountingError, match="正例"):
        learning.save_rule(LOCAL_ACTOR, book, RuleCreate(definition=changed, sample_ids=[positive["id"]], reason="不匹配正例"))
    assert negative["id"] != positive["id"]


def test_evaluation_checks_known_matches_beyond_selected_positive(ready):
    ledger, _, learning, book, _, negative, rule = ready
    conflicting = bank_sample(ledger, learning, book, LOCAL_ACTOR, external="different-treatment", amount="59.00", code="6602.03")
    result = learning.evaluate(LOCAL_ACTOR, book, rule["id"], RuleEvaluate(expected_version=1, negative_sample_ids=[negative["id"]]))
    assert not result["passed"] and any(item["sample_id"] == conflicting["id"] for item in result["failures"])
    with pytest.raises(AccountingError, match="先通过"):
        learning.publish(LOCAL_ACTOR, book, rule["id"], RulePublish(expected_version=1, expected_state_version=1, evaluation_id=result["id"], reason="错误规则不应发布"))


def test_new_sample_or_chart_revision_invalidates_evaluation(ready):
    ledger, catalog, learning, book, _, negative, rule = ready
    result = learning.evaluate(LOCAL_ACTOR, book, rule["id"], RuleEvaluate(expected_version=1, negative_sample_ids=[negative["id"]]))
    catalog.update_account(LOCAL_ACTOR, book, "1002", AccountUpdate(code="1002", name="客户银行科目", expected_version=1))
    with pytest.raises(AccountingError, match="变化"):
        learning.publish(LOCAL_ACTOR, book, rule["id"], RulePublish(expected_version=1, expected_state_version=1, evaluation_id=result["id"], reason="旧评估不能发布"))
    result = learning.evaluate(LOCAL_ACTOR, book, rule["id"], RuleEvaluate(expected_version=1, negative_sample_ids=[negative["id"]]))
    bank_sample(ledger, learning, book, LOCAL_ACTOR, external="more-sample", counterparty="供应商丙", corrected=False)
    with pytest.raises(AccountingError, match="变化"):
        learning.publish(LOCAL_ACTOR, book, rule["id"], RulePublish(expected_version=1, expected_state_version=1, evaluation_id=result["id"], reason="新样例后重新评估"))


def test_rule_revision_is_unpublished_immutable_and_stale_state_rejected(ready):
    ledger, _, learning, book, positive, _, rule = ready
    published, _ = evaluate_publish(ready)
    with pytest.raises(AccountingError, match="变化"):
        learning.retire(LOCAL_ACTOR, book, rule["id"], RuleRetire(expected_version=1, expected_state_version=1, reason="过期命令"))
    revised = learning.save_rule(LOCAL_ACTOR, book, RuleUpdate(definition={**rule["definition"], "name": "显式修订"}, sample_ids=[positive["id"]], reason="新版本需重新评估", expected_version=1, expected_state_version=published["state_version"]), rule["id"])
    assert revised["current_version"] == 2 and revised["status"] == "draft" and revised["evaluation_id"] is None
    history = learning.get_rule(LOCAL_ACTOR, book, rule["id"])["versions"]
    assert len(history) == 2 and history[-1]["definition"]["name"] == rule["definition"]["name"]
    with ledger.db.transaction(actor=LOCAL_ACTOR) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE learning_rule_versions SET reason='覆写' WHERE rule_id=?", (rule["id"],))


def test_retire_stops_consumption_and_changes_policy_fingerprint(ready):
    ledger, _, learning, book, positive, _, _ = ready
    published, _ = evaluate_publish(ready)
    with ledger.db.transaction(actor=LOCAL_ACTOR) as conn:
        before = learning.policy_fingerprint(conn, LOCAL_ACTOR, book)
    retired = learning.retire(LOCAL_ACTOR, book, published["id"], RuleRetire(expected_version=1, expected_state_version=published["state_version"], reason="业务口径变化暂停规则"))
    assert retired["status"] == "retired"
    with ledger.db.transaction(actor=LOCAL_ACTOR) as conn:
        assert before != learning.policy_fingerprint(conn, LOCAL_ACTOR, book)
        assert learning.rule_generate_in_transaction(conn, LOCAL_ACTOR, book, "daily_cashflow", json.loads(positive["snapshot_json"])["payload"], 11300, source_kind="bank") is None


def test_capture_is_atomic_with_approval_and_frozen_after_export(ready):
    ledger, _, learning, book, positive, _, _ = ready
    with ledger.db.transaction(actor=LOCAL_ACTOR) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE learning_samples SET final_json='{}' WHERE id=?", (positive["id"],))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM learning_samples WHERE id=?", (positive["id"],))
    with pytest.raises(AccountingError, match="当前完整审核"):
        with ledger.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
            draft = conn.execute("SELECT * FROM drafts WHERE id=?", (positive["draft_id"],)).fetchone()
            learning.capture_in_transaction(conn, LOCAL_ACTOR, {"id": book}, draft, {"issues": ["blocking"]}, positive["reviewed_version"])


def test_book_scope_blocks_employee_other_book_and_editor_publish(business):
    c = business
    learning = LearningService(c.db)
    sample = bank_sample(c.ledger, learning, c.book["id"], c.manager)
    for actor in (c.actors["employee"], c.actors["peer"]):
        with pytest.raises(AccountingError):
            learning.overview(actor, c.book["id"])
    with pytest.raises(AccountingError, match="不存在"):
        learning.get_sample(c.manager, c.other_book["id"], sample["id"])
    rule = learning.overview(c.manager, c.book["id"])["rules"][0]
    with pytest.raises(AccountingError):
        learning.save_rule(c.actors["finance"], c.book["id"], RuleCreate(definition=rule["definition"], sample_ids=[sample["id"]], reason="编辑不能发布规则"))
    with pytest.raises(AccountingError):
        learning.save_rule(c.manager, c.other_book["id"], RuleCreate(definition=rule["definition"], sample_ids=[sample["id"]], reason="不跨账套学习原件"))


def test_business_review_sample_has_fact_snapshot_and_safe_candidate(business):
    c = business
    # Isolate learning from an explicitly manual business review; OCR evidence
    # validation has its own enabled-processing regression suites.
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute("UPDATE ai_company_settings SET enabled=0 WHERE firm_id=? AND company_id=?",
                     (c.manager.firm_id, c.company["id"]))
    learning = LearningService(c.db)
    c.ledger.learning = learning
    event = approved(c, template="procurement_expense", payment_status="paid")
    mapped = handoff(c, event)
    draft = c.ledger.get_draft(c.manager, c.book["id"], mapped["draft_id"])
    lines = [{"account_code": "6602.99", "debit": "0.30", "credit": "0.00"}, {"account_code": "1002", "debit": "0.00", "credit": "0.30"}]
    draft = c.ledger.update_draft(c.manager, c.book["id"], draft["id"], DraftUpdate(expected_version=draft["version"], description="核实事项用途", lines=lines, correction_reason="真实业务处理依据"))
    c.ledger.approve(c.actors["accountant"], c.book["id"], draft["id"], ApprovalRequest(expected_version=draft["version"]))
    overview = learning.overview(c.actors["accountant"], c.book["id"])
    sample = learning.get_sample(c.actors["accountant"], c.book["id"], overview["samples"][0]["id"])
    assert sample["snapshot"]["fact_version"] == event["fact_version"] and sample["snapshot"]["context"]["applicant_id"] == "employee"
    assert overview["rules"][0]["status"] == "draft" and overview["rules"][0]["definition"]["source_kind"] == "business"


@pytest.mark.parametrize("recognized", [True, False])
def test_tax_split_candidate_requires_known_evidence_amounts(business, recognized):
    """Explicit synthetic OCR fixture and human-approved amounts, no network."""
    c = business
    # This historical fixture supplies only the amounts needed by learning,
    # not a full OCR document. It intentionally uses the manual-review path.
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute("UPDATE ai_company_settings SET enabled=0 WHERE firm_id=? AND company_id=?",
                     (c.manager.firm_id, c.company["id"]))
    learning = LearningService(c.db)
    c.ledger.learning = learning
    CatalogService(c.db).create_account(c.manager, c.book["id"], AccountDefinition(code="2221.01", name="客户会计明确配置税额科目"))
    event = approved(c, template="procurement_expense", payment_status="paid")
    mapping = handoff(c, event)
    if recognized:
        with c.db.transaction(write=True, actor=c.manager) as conn:
            output = {"documents": [{"currency": "CNY", "gross_amount": "0.30", "net_amount": "0.20", "tax_amount": "0.10"}]}
            conn.execute("INSERT INTO ai_runs(id,firm_id,company_id,event_id,kind,attachment_id,input_version,input_fingerprint,idempotency_key,status,result_json,provider,model,prompt_version,created_by,created_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,'succeeded',?,'synthetic-only','synthetic-only','synthetic-only',?,'2026-10-12T00:00:00+00:00','2026-10-12T00:00:01+00:00')", ("tax-fixture", c.manager.firm_id, c.company["id"], event["id"], "ocr", event["attachments"][0]["id"], event["fact_version"], "synthetic-fp", "synthetic-tax-key", json.dumps(output), c.manager.user_id))
    draft = c.ledger.get_draft(c.manager, c.book["id"], mapping["draft_id"])
    draft = c.ledger.update_draft(c.manager, c.book["id"], draft["id"], DraftUpdate(expected_version=draft["version"], description="会计核实的税额拆分", correction_reason="按归档票据的真实金额复核", lines=[
        {"account_code": "6602.02", "debit": "0.20", "credit": "0.00"},
        {"account_code": "2221.01", "debit": "0.10", "credit": "0.00"},
        {"account_code": "1002", "debit": "0.00", "credit": "0.30"},
    ]))
    c.ledger.approve(c.actors["accountant"], c.book["id"], draft["id"], ApprovalRequest(expected_version=draft["version"]))
    overview = learning.overview(c.actors["accountant"], c.book["id"])
    assert len(overview["samples"]) == 1
    if recognized:
        assert [line["amount_field"] for line in overview["rules"][0]["definition"]["lines"]] == ["net", "tax", "gross"]
        assert overview["rules"][0]["status"] == "draft"
    else:
        assert overview["rules"] == []  # Never infer tax from the final numbers.


@pytest.mark.parametrize("change", [
    {"source_kind": "global"}, {"conditions": {"counterparty": "   "}},
    {"lines": [{"account_code": "6602.02", "side": "debit", "amount_field": "eval(amount)"}]},
])
def test_rules_reject_arbitrary_scope_and_executable_formulas(ready, change):
    definition = ready[-1]["definition"]
    if "conditions" in change:
        change = {"conditions": {**definition["conditions"], **change["conditions"]}}
    with pytest.raises(ValidationError):
        RuleDefinition.model_validate({**definition, **change})


def test_closed_book_read_history_but_no_publish_or_generate(ready):
    ledger, _, learning, book, positive, negative, rule = ready
    restore_period_state(ledger.db, book)
    assert learning.overview(LOCAL_ACTOR, book)["can_manage"] is False
    with pytest.raises(AccountingError, match="关闭"):
        learning.evaluate(LOCAL_ACTOR, book, rule["id"], RuleEvaluate(expected_version=1, negative_sample_ids=[negative["id"]]))
    with ledger.db.transaction(actor=LOCAL_ACTOR) as conn:
        with pytest.raises(AccountingError, match="关闭"):
            learning.rule_generate_in_transaction(conn, LOCAL_ACTOR, book, "daily_cashflow", json.loads(positive["snapshot_json"])["payload"], 11300, source_kind="bank")


def test_sample_pagination_is_bounded_and_current_access_required(ready):
    _, _, learning, book, _, _, _ = ready
    first, second = learning.overview(LOCAL_ACTOR, book, 1), learning.overview(LOCAL_ACTOR, book, 1, 1)
    assert first["pagination"]["total"] == 2 and first["pagination"]["has_more"]
    assert first["samples"][0]["id"] != second["samples"][0]["id"]
    with pytest.raises(AccountingError, match="分页"):
        learning.overview(LOCAL_ACTOR, book, 101)
