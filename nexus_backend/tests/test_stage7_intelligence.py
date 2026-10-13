"""Stage 7 reuse tests use synthetic documents and real local transactions."""
from dataclasses import replace
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import DraftUpdate
from app.business.models import BusinessEventCreate, BusinessEventUpdate
from app.intelligence.requests import OCRRequest, ProposalRequest
from test_intelligence_workflow import ai_case, create_event, handoff, draft, propose, invoice_document


def identify(case, event, key=None, actor="employee", **options):
    return case.intelligence.extract(case.actors[actor], event["id"], OCRRequest(
        attachment_id=event["attachments"][0]["id"], expected_version=event["version"],
        idempotency_key=key or uuid4().hex, **options))


def test_fact_version_tracks_only_payload_and_attachment_changes(ai_case):
    event = create_event(ai_case)
    assert event["version"] == event["fact_version"] == 2
    same = ai_case.business.update(ai_case.actors["employee"], event["id"], BusinessEventUpdate(expected_version=2, payload=event["payload"]))
    assert same["version"] == 3 and same["fact_version"] == 2
    changed = ai_case.business.update(ai_case.actors["employee"], event["id"], BusinessEventUpdate(expected_version=3, payload={**same["payload"], "description": "补充业务用途"}))
    assert changed["version"] == 4 and changed["fact_version"] == 3
    moved = handoff(ai_case, changed)["event"]
    assert moved["status"] == "handed_off" and moved["version"] == 8 and moved["fact_version"] == 3


def test_ocr_cache_generates_new_bound_run_without_charge(ai_case):
    event = create_event(ai_case)
    first, second = identify(ai_case, event), identify(ai_case, event)
    assert first["id"] != second["id"] and second["attachment_id"] == event["attachments"][0]["id"]
    assert first["cache_hit"] == 0 and second["cache_hit"] == 1
    assert ai_case.provider.extract_calls == 1 and second["usage"] == {}
    with ai_case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM model_costs").fetchone()[0] == 1
    detail = ai_case.intelligence.detail(ai_case.actors["employee"], event["id"])
    assert detail["runs"][0]["id"] == second["id"] and not detail["findings"]


def test_same_company_peer_content_reuse_does_not_disclose_origin(ai_case):
    original, peer = create_event(ai_case), create_event(ai_case, actor="peer")
    original_run = identify(ai_case, original)
    reused = identify(ai_case, peer, actor="peer")
    assert reused["cache_hit"] == 1 and reused["event_id"] == peer["id"]
    assert reused["attachment_id"] == peer["attachments"][0]["id"]
    assert original["id"] not in str(reused) and original_run["id"] not in str(reused)
    assert ai_case.provider.extract_calls == 1
    with pytest.raises(AccountingError):
        ai_case.intelligence.detail(ai_case.actors["peer"], original["id"])


def test_same_sha_cannot_reuse_across_companies(ai_case):
    first = create_event(ai_case)
    identify(ai_case, first)
    other = ai_case.business.create(ai_case.actors["outsider"], BusinessEventCreate(
        company_id=ai_case.other_company["id"], template_id="reimbursement", payload=first["payload"]))
    metadata = ai_case.business.download_metadata(ai_case.actors["employee"], first["attachments"][0]["id"])
    attachment = ai_case.storage.put("另一企业合成票据.png", "image/png", ai_case.storage.read(metadata))
    other = ai_case.business.add_attachment(ai_case.actors["outsider"], other["id"], other["version"], attachment)
    ai_case.intelligence.company_settings(ai_case.manager, ai_case.other_company["id"], True)
    second = identify(ai_case, other, actor="outsider")
    assert second["cache_hit"] == 0 and ai_case.provider.extract_calls == 2


def test_model_configuration_invalidates_reuse(ai_case):
    event = create_event(ai_case)
    identify(ai_case, event)
    ai_case.provider.settings = replace(ai_case.provider.settings, vision_model="synthetic-vision-next")
    result = identify(ai_case, event)
    assert result["cache_hit"] == 0 and ai_case.provider.extract_calls == 2


def test_cache_requires_current_document_integrity(ai_case):
    event = create_event(ai_case)
    identify(ai_case, event)
    metadata = ai_case.business.download_metadata(ai_case.actors["employee"], event["attachments"][0]["id"])
    path = ai_case.storage.root / metadata["storage_key"]
    path.write_bytes(b"tampered")
    with pytest.raises(AccountingError):
        identify(ai_case, event)
    assert ai_case.provider.extract_calls == 1


def test_company_opt_out_blocks_even_cached_document(ai_case):
    event = create_event(ai_case)
    identify(ai_case, event)
    ai_case.intelligence.company_settings(ai_case.manager, ai_case.company["id"], False)
    with pytest.raises(AccountingError, match="尚未启用"):
        identify(ai_case, event)
    assert ai_case.provider.extract_calls == 1


def test_local_cached_result_needs_no_secret_or_model_budget(ai_case):
    event = create_event(ai_case)
    identify(ai_case, event)
    ai_case.provider.settings = replace(ai_case.provider.settings, api_key="")
    result = identify(ai_case, event)
    assert result["status"] == "succeeded" and result["cache_hit"] == 1 and ai_case.provider.extract_calls == 1


def test_force_refresh_updates_only_validated_derived_cache(ai_case):
    event = create_event(ai_case)
    first = identify(ai_case, event)
    ai_case.provider.document = invoice_document(amount="0.40", net_amount="0.40")
    refreshed = identify(ai_case, event, force_refresh=True)
    reused = identify(ai_case, event)
    assert ai_case.provider.extract_calls == 2 and refreshed["cache_hit"] == 0 and reused["cache_hit"] == 1
    assert first["result"]["documents"][0]["gross_amount"] == "0.30"
    assert refreshed["result"] == reused["result"] and reused["result"]["documents"][0]["gross_amount"] == "0.40"
    assert any(item["code"] == "document_business_amount" for item in ai_case.intelligence.detail(ai_case.actors["employee"], event["id"])["findings"])


def test_invalid_field_evidence_never_enters_cache(ai_case):
    event = create_event(ai_case)
    ai_case.provider.document = invoice_document()
    ai_case.provider.document["evidence"] = []
    identify(ai_case, event)
    result = identify(ai_case, event)
    assert result["cache_hit"] == 0 and ai_case.provider.extract_calls == 2
    with ai_case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM document_extractions").fetchone()[0] == 0


def test_approval_only_transition_does_not_discard_late_ocr(ai_case):
    event = create_event(ai_case)
    ai_case.provider.extract_hook = lambda: ai_case.business.submit(ai_case.actors["employee"], event["id"], event["version"])
    result = identify(ai_case, event)
    assert result["status"] == "succeeded"
    current = ai_case.business.get_event(ai_case.actors["employee"], event["id"])
    assert current["fact_version"] == event["fact_version"] and current["version"] == event["version"] + 1


def test_late_ocr_with_changed_payload_is_discarded(ai_case):
    event = create_event(ai_case)
    ai_case.provider.extract_hook = lambda: ai_case.business.update(ai_case.actors["employee"], event["id"], BusinessEventUpdate(expected_version=event["version"], payload={**event["payload"], "amount": "0.40"}))
    with pytest.raises(AccountingError, match="业务事实"):
        identify(ai_case, event)
    with ai_case.db.transaction() as conn:
        assert conn.execute("SELECT status FROM ai_runs WHERE event_id=?", (event["id"],)).fetchone()[0] == "stale"
        assert conn.execute("SELECT COUNT(*) FROM document_extractions").fetchone()[0] == 0


def test_expected_fact_version_accepts_later_approval_status(ai_case):
    event = create_event(ai_case)
    moved = ai_case.business.submit(ai_case.actors["employee"], event["id"], event["version"])
    stale_workflow = {**moved, "version": event["version"]}
    result = identify(ai_case, stale_workflow, expected_fact_version=event["fact_version"])
    assert result["input_version"] == event["fact_version"] and result["status"] == "succeeded"


def test_fact_change_rejects_reused_ocr_command_key(ai_case):
    event = create_event(ai_case)
    identify(ai_case, event, key="one-ocr-command-key")
    current = ai_case.business.update(ai_case.actors["employee"], event["id"], BusinessEventUpdate(expected_version=event["version"], payload={**event["payload"], "description": "新的用途"}))
    with pytest.raises(AccountingError, match="幂等键"):
        identify(ai_case, current, key="one-ocr-command-key")
    reused = identify(ai_case, current)
    assert reused["cache_hit"] == 1 and ai_case.provider.extract_calls == 1


def test_automatic_apply_never_overwrites_manual_edit(ai_case):
    event = create_event(ai_case)
    identify(ai_case, event)
    receipt = handoff(ai_case, event)
    before = draft(ai_case, receipt["draft_id"])
    edited = ai_case.ledger.update_draft(ai_case.actors["accountant"], ai_case.book["id"], before["id"], DraftUpdate(expected_version=1, description="会计人工用途", lines=before["lines"]))
    candidate = propose(ai_case, receipt["draft_id"], version=edited["version"])
    with pytest.raises(AccountingError, match="人工修改"):
        with ai_case.db.transaction(write=True, actor=ai_case.actors["accountant"]) as conn:
            ai_case.intelligence._apply_in_transaction(conn, ai_case.actors["accountant"], candidate["id"], edited["version"], automatic=True)
    assert draft(ai_case, receipt["draft_id"])["description"] == "会计人工用途"


class ProposalLease:
    def __init__(self, case, fail_finish=False):
        self.case, self.fail_finish, self.steps = case, fail_finish, []

    def check(self, conn):
        self.steps.append("check")

    def inputs(self, event, current_draft):
        assert event["fact_version"] == 2 and current_draft["version"] == 1
        self.steps.append("inputs")

    def bind(self, conn, run):
        assert run["status"] == "running"
        self.steps.append("bind")

    def before_send(self, conn):
        self.steps.append("before_send")

    def finish(self, conn, run, error):
        assert error is None and run["status"] == "succeeded"
        assert conn.execute("SELECT status FROM ai_runs WHERE id=?", (run["id"],)).fetchone()[0] == "succeeded"
        self.case.intelligence._apply_in_transaction(conn, self.case.actors["accountant"], run["id"], 1, automatic=True)
        self.steps.append("finish")
        if self.fail_finish:
            raise AccountingError("合成任务租约已过期", 409)


@pytest.mark.parametrize("fail_finish", [False, True])
def test_proposal_result_and_automatic_application_share_lease_receipt_transaction(ai_case, fail_finish):
    event = create_event(ai_case)
    identify(ai_case, event)
    receipt = handoff(ai_case, event)
    lease = ProposalLease(ai_case, fail_finish=fail_finish)
    request = ProposalRequest(expected_version=1, idempotency_key=uuid4().hex)
    if fail_finish:
        with pytest.raises(AccountingError, match="租约"):
            ai_case.intelligence.propose(ai_case.actors["accountant"], ai_case.book["id"], receipt["draft_id"], request, execution=lease)
    else:
        result = ai_case.intelligence.propose(ai_case.actors["accountant"], ai_case.book["id"], receipt["draft_id"], request, execution=lease)
        assert result["status"] == "succeeded"
    assert lease.steps == ["check", "inputs", "bind", "inputs", "before_send", "check", "inputs", "finish"]
    current = draft(ai_case, receipt["draft_id"])
    assert current["status"] == "draft" and current["version"] == (1 if fail_finish else 2)
    with ai_case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_proposal_applications").fetchone()[0] == int(not fail_finish)
        assert conn.execute("SELECT status FROM ai_runs WHERE kind='proposal'").fetchone()[0] == ("stale" if fail_finish else "succeeded")


@pytest.mark.parametrize("wrong_department", [False, True])
def test_model_auxiliary_values_are_bound_to_current_business_facts(ai_case, wrong_department):
    from app.accounting.catalog import CatalogService
    from app.accounting.catalog_models import AccountDefinition
    CatalogService(ai_case.db).create_account(ai_case.manager, ai_case.book["id"], AccountDefinition(
        code="6602.98", name="按部门核算办公耗材", category="expense", auxiliary_requirements=["department"]))
    event = create_event(ai_case)
    identify(ai_case, event)
    receipt = handoff(ai_case, event)
    def add_aux(output):
        output["lines"][0]["account_code"] = "6602.98"
        output["lines"][0]["auxiliary_values"] = {"department": "foreign-department" if wrong_department else event["department_id"]}
    ai_case.provider.proposal_mutator = add_aux
    candidate = propose(ai_case, receipt["draft_id"])
    if wrong_department:
        assert candidate["status"] == "failed" and candidate["error_code"] == "invalid_proposal"
        assert draft(ai_case, receipt["draft_id"])["version"] == 1
    else:
        applied = ai_case.intelligence.apply(ai_case.actors["accountant"], candidate["id"], 1)
        assert applied["lines"][0]["auxiliary_values"] == {"department": event["department_id"]}
        assert applied["issues"] == []


def test_client_chart_change_discards_late_model_candidate(ai_case):
    from app.accounting.catalog import CatalogService
    from app.accounting.catalog_models import AccountUpdate
    event = create_event(ai_case)
    identify(ai_case, event)
    receipt = handoff(ai_case, event)
    ai_case.provider.proposal_hook = lambda: CatalogService(ai_case.db).update_account(ai_case.manager, ai_case.book["id"], "6602.02", AccountUpdate(
        expected_version=1, code="6602.02", name="新版办公费", category="expense"))
    with pytest.raises(AccountingError, match="资料已改变"):
        propose(ai_case, receipt["draft_id"])
    assert draft(ai_case, receipt["draft_id"])["version"] == 1


def test_journal_auxiliary_schema_has_only_fixed_nullable_keys():
    from app.intelligence.models import JournalSuggestion, JournalLine
    schema = JournalSuggestion.model_json_schema()["$defs"]["JournalLine"]
    assert set(schema["required"]) == set(schema["properties"])
    auxiliary = schema["properties"]["auxiliary_values"]
    assert auxiliary["additionalProperties"] is False
    assert set(auxiliary["properties"]) == {"department", "counterparty", "employee", "project"}
    assert set(auxiliary["required"]) == set(auxiliary["properties"])
    empty = JournalLine.model_validate({"account_code": "1002", "debit": "1.00", "credit": "0.00", "auxiliary_values": {key: None for key in auxiliary["properties"]}})
    assert empty.auxiliary_values == {}


@pytest.mark.parametrize("forged_project", [False, True])
def test_model_project_is_bound_to_recorded_business_project(ai_case, forged_project):
    from app.accounting.catalog import CatalogService
    from app.accounting.catalog_models import AccountDefinition
    CatalogService(ai_case.db).create_account(ai_case.manager, ai_case.book["id"], AccountDefinition(
        code="6602.97", name="按项目核算办公耗材", category="expense", auxiliary_requirements=["project"]))
    event = create_event(ai_case, project="试点客户系统建设项目")
    identify(ai_case, event)
    receipt = handoff(ai_case, event)
    original = draft(ai_case, receipt["draft_id"])
    def choose_project(output):
        output["lines"][0]["account_code"] = "6602.97"
        output["lines"][0]["auxiliary_values"] = {"project": "模型臆造的项目" if forged_project else event["payload"]["project"]}
    ai_case.provider.proposal_mutator = choose_project
    candidate = propose(ai_case, receipt["draft_id"])
    if forged_project:
        assert candidate["status"] == "failed" and candidate["error_code"] == "invalid_proposal"
        assert candidate["result"] is None and draft(ai_case, receipt["draft_id"]) == original
        with pytest.raises(AccountingError):
            ai_case.intelligence.apply(ai_case.actors["accountant"], candidate["id"], original["version"])
        with ai_case.db.transaction() as conn:
            assert conn.execute("SELECT COUNT(*) FROM ai_proposal_applications WHERE run_id=?", (candidate["id"],)).fetchone()[0] == 0
    else:
        assert candidate["status"] == "succeeded"
        applied = ai_case.intelligence.apply(ai_case.actors["accountant"], candidate["id"], original["version"])
        assert applied["status"] == "draft" and not applied["issues"]
        assert applied["lines"][0]["auxiliary_values"] == {"project": event["payload"]["project"]}


def test_model_cannot_assign_a_different_valid_company_employee(ai_case):
    from app.accounting.catalog import CatalogService
    from app.accounting.catalog_models import AccountDefinition
    CatalogService(ai_case.db).create_account(ai_case.manager, ai_case.book["id"], AccountDefinition(
        code="6602.96", name="按人员核算办公耗材", category="expense", auxiliary_requirements=["employee"]))
    event = create_event(ai_case)
    identify(ai_case, event)
    receipt = handoff(ai_case, event)
    original = draft(ai_case, receipt["draft_id"])
    def assign_peer(output):
        output["lines"][0]["account_code"] = "6602.96"
        output["lines"][0]["auxiliary_values"] = {"employee": ai_case.actors["peer"].user_id}
    ai_case.provider.proposal_mutator = assign_peer
    candidate = propose(ai_case, receipt["draft_id"])
    assert candidate["status"] == "failed" and candidate["error_code"] == "invalid_proposal"
    assert draft(ai_case, receipt["draft_id"]) == original
