from period_fixtures import restore_period_state
"""异常与补件的真实本地事务；不调用任何模型供应商。"""
import json
from uuid import uuid4
import pytest
from app.accounting.domain import AccountingError
from app.business.models import BusinessEventUpdate
from app.cases.models import CaseCreate, CaseCommand, CaseAssign, CaseComment, CaseRespond, CaseReviewResponse
from app.cases.service import CaseService
from test_business import business, create, submitted, approved, handoff


def request(c, row, model=CaseCommand, **changes):
    return model(expected_version=row["version"], idempotency_key=uuid4().hex, reason="已核对当前事实", **changes)


def setup(c, kind="supplement", **changes):
    cases = CaseService(c.db, c.service)
    event = approved(c)
    mapping = handoff(c, event)
    task = cases.create(c.actors["accountant"], CaseCreate(event_id=event["id"], kind=kind, title="补充采购用途", reason="请说明采购用途并附合同原件", idempotency_key=uuid4().hex, **changes))
    return cases, event, mapping, task


def test_internal_case_never_visible_to_applicant_department_or_other_employee(business):
    c = business
    cases, event, _, task = setup(c, "exception")
    for role in ("employee", "peer", "approver", "other_employee", "company_admin"):
        actor = c.actors[role]
        assert cases.list(actor)["total"] == 0
        with pytest.raises(AccountingError) as error:
            cases.get(actor, task["id"])
        assert error.value.status == 404
    assert cases.list(c.actors["accountant"])["total"] == 1
    assert cases.list(c.actors["finance"])["total"] == 1


def test_explicit_request_own_scope_and_internal_notes_are_separate(business):
    c = business
    cases, _, _, task = setup(c)
    assert cases.list(c.actors["employee"])["total"] == 1
    task = cases.command(c.actors["accountant"], task["id"], "comment", request(c, task, CaseComment, audience="internal"))
    own = cases.get(c.actors["employee"], task["id"])
    assert all(row["audience"] == "requester" for row in own["history"])
    assert all(key not in own for key in ("draft_id", "source_id", "book_id", "assignee_id", "created_by"))
    assert len(cases.get(c.actors["accountant"], task["id"])["history"]) == len(own["history"]) + 1
    for role in ("peer", "approver", "other_employee"):
        assert cases.list(c.actors[role])["total"] == 0


def test_unauthorized_assignment_does_not_grant_evidence_access(business):
    c = business
    cases, _, _, task = setup(c)
    with pytest.raises(AccountingError):
        cases.command(c.actors["accountant"], task["id"], "assign", request(c, task, CaseAssign, assignee_id="peer"))
    task = cases.command(c.actors["accountant"], task["id"], "assign", request(c, task, CaseAssign, assignee_id="finance", due_on="2026-10-01", priority="urgent"))
    assert task["assignee_id"] == "finance" and cases.list(c.actors["finance"], mine=True)["total"] == 1
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute("DELETE FROM company_memberships WHERE company_id=? AND user_id='finance'", (c.company["id"],))
    assert cases.list(c.actors["finance"])["total"] == 0


def test_supplement_acceptance_appends_original_and_invalidates_fact_without_editing_snapshot(business):
    c = business
    cases, event, mapping, task = setup(c, required_fields=["合同编号"], attachment_required=True)
    employee, reviewer = c.actors["employee"], c.actors["accountant"]
    with pytest.raises(AccountingError):
        cases.command(employee, task["id"], "respond", request(c, task, CaseRespond))
    stored = {"id": uuid4().hex, "storage_key": "aa/" + "a" * 32, "filename": "追加合同.pdf", "media_type": "application/pdf", "sha256": "a" * 64, "size_bytes": 100}
    task = cases.add_attachment(employee, task["id"], task["version"], uuid4().hex, stored)["case"]
    with c.db.transaction(actor=reviewer) as conn:
        source = dict(conn.execute("SELECT * FROM sources WHERE id=?", (mapping["source_id"],)).fetchone())
        previous_fp = cases.evidence_revision_in_transaction(conn, reviewer, source=source)
        old = conn.execute("SELECT fact_version,payload FROM business_events WHERE id=?", (event["id"],)).fetchone()
        snapshots = [tuple(row) for row in conn.execute("SELECT * FROM business_versions WHERE event_id=?", (event["id"],))]
        assert not conn.execute("SELECT 1 FROM business_attachments WHERE id=?", (stored["id"],)).fetchone()
        assert cases.blocking_issues_in_transaction(conn, reviewer, source)
    task = cases.command(employee, task["id"], "respond", request(c, task, CaseRespond, fields={"合同编号": "HT-2026-01"}))
    task = cases.command(reviewer, task["id"], "review-response", request(c, task, CaseReviewResponse, decision="accept"))
    assert task["state"] == "in_review" and task["evidence_revision"] == 1
    with c.db.transaction(actor=reviewer) as conn:
        new = conn.execute("SELECT fact_version,payload FROM business_events WHERE id=?", (event["id"],)).fetchone()
        assert new["fact_version"] == old["fact_version"] + 1 and new["payload"] == old["payload"]
        assert conn.execute("SELECT 1 FROM business_attachments WHERE id=? AND event_id=?", (stored["id"], event["id"])).fetchone()
        assert snapshots == [tuple(row) for row in conn.execute("SELECT * FROM business_versions WHERE event_id=?", (event["id"],))]
        assert cases.evidence_revision_in_transaction(conn, reviewer, source=source) != previous_fp
        assert cases.accepted_context_in_transaction(conn, reviewer, event["id"])[0]["fields"] == {"合同编号": "HT-2026-01"}
        assert cases.blocking_issues_in_transaction(conn, reviewer, source)
    task = cases.command(reviewer, task["id"], "close", request(c, task))
    with c.db.transaction(actor=reviewer) as conn:
        assert not cases.blocking_issues_in_transaction(conn, reviewer, source)
        assert conn.execute("SELECT status FROM drafts WHERE id=?", (mapping["draft_id"],)).fetchone()[0] == "draft"


def test_waiting_or_submitted_cannot_close_and_return_does_not_publish_files(business):
    c = business
    cases, _, _, task = setup(c)
    with pytest.raises(AccountingError):
        cases.command(c.actors["accountant"], task["id"], "close", request(c, task))
    task = cases.command(c.actors["employee"], task["id"], "respond", request(c, task, CaseRespond))
    with pytest.raises(AccountingError):
        cases.command(c.actors["accountant"], task["id"], "close", request(c, task))
    task = cases.command(c.actors["accountant"], task["id"], "review-response", request(c, task, CaseReviewResponse, decision="return"))
    assert task["state"] == "awaiting_info" and task["evidence_revision"] == 0


def test_case_command_versions_idempotency_and_paging_are_authorized(business):
    c = business
    cases, _, _, task = setup(c)
    actor = c.actors["accountant"]
    command = request(c, task, CaseComment, audience="internal")
    first = cases.command(actor, task["id"], "comment", command)
    second = cases.command(actor, task["id"], "comment", command)
    assert first["version"] == second["version"] == task["version"] + 1
    assert len(first["history"]) == len(second["history"])
    with pytest.raises(AccountingError) as error:
        cases.command(actor, task["id"], "comment", request(c, task, CaseComment, audience="internal"))
    assert error.value.status == 409
    assert cases.list(actor, limit=1, offset=1)["cases"] == []
    assert cases.list(c.actors["peer"], limit=1)["total"] == 0


def test_employee_cannot_change_approved_business_while_supplementing(business):
    c = business
    cases, event, _, task = setup(c)
    with pytest.raises(AccountingError):
        c.service.update(c.actors["employee"], event["id"], BusinessEventUpdate(expected_version=c.service.get_event(c.actors["employee"], event["id"])["version"], payload=event["payload"]))
    with pytest.raises(AccountingError):
        cases.command(c.actors["employee"], task["id"], "comment", request(c, task, CaseComment, audience="internal"))


def test_closed_period_prevents_upload_submission_and_acceptance(business):
    c = business
    cases, _, _, task = setup(c)
    with c.db.transaction(write=True, actor=c.manager) as conn:
        restore_period_state(c.ledger.db, c.book["id"], conn=conn)
    with pytest.raises(AccountingError):
        cases.require_upload(c.actors["employee"], task["id"], task["version"])
    with pytest.raises(AccountingError):
        cases.command(c.actors["employee"], task["id"], "respond", request(c, task, CaseRespond))


def test_replacement_records_keep_old_original_and_reject_cross_event_attachment(business):
    c = business
    cases, event, _, task = setup(c)
    stored = {"id": uuid4().hex, "storage_key": "bb/" + "b" * 32, "filename": "清晰扫描.pdf", "media_type": "application/pdf", "sha256": "b" * 64, "size_bytes": 100}
    task = cases.add_attachment(c.actors["employee"], task["id"], task["version"], uuid4().hex, stored)["case"]
    task = cases.command(c.actors["employee"], task["id"], "respond", request(c, task, CaseRespond))
    other_event = approved(c)
    with pytest.raises(AccountingError):
        cases.command(c.actors["accountant"], task["id"], "review-response", request(c, task, CaseReviewResponse, decision="accept", supersedes_attachment_ids=[other_event["attachments"][0]["id"]]))
    old_id = event["attachments"][0]["id"]
    task = cases.command(c.actors["accountant"], task["id"], "review-response", request(c, task, CaseReviewResponse, decision="accept", supersedes_attachment_ids=[old_id]))
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        assert cases.superseded_attachment_ids_in_transaction(conn, c.actors["accountant"], event["id"]) == {old_id}
        assert conn.execute("SELECT COUNT(*) FROM business_attachments WHERE event_id=?", (event["id"],)).fetchone()[0] == 2
        assert not cases.superseded_attachment_ids_in_transaction(conn, c.actors["finance"], other_event["id"])
    assert old_id not in {item["id"] for item in task["replacement_choices"]}


def test_bank_case_documents_require_independent_acceptance_and_keep_source_immutable(business):
    from app.accounting.models import ImportRequest
    c = business
    imported = c.ledger.import_sources(c.manager, c.book["id"], ImportRequest(source_name="合成测试流水", rows=[{"external_id": "case-bank-01", "date": "2026-10-04", "description": "办公室房租", "counterparty": "出租方", "amount": "10.00", "direction": "expense", "evidence_ref": ""}]))
    c.ledger.suggestions(c.manager, c.book["id"])
    draft = next(draft for draft in c.ledger.get_book(c.manager, c.book["id"])["drafts"] if draft["source_kind"] == "bank")
    cases = CaseService(c.db, c.service)
    actor = c.actors["accountant"]
    task = cases.create(actor, CaseCreate(book_id=c.book["id"], draft_id=draft["id"], title="核对银行租金原件", reason="缺少业务原件", idempotency_key=uuid4().hex))
    assert task["permissions"]["upload"]
    stored = {"id": uuid4().hex, "storage_key": "cc/" + "c" * 32, "filename": "租赁合同.pdf", "media_type": "application/pdf", "sha256": "c" * 64, "size_bytes": 100}
    task = cases.add_attachment(actor, task["id"], task["version"], uuid4().hex, stored)["case"]
    task = cases.command(actor, task["id"], "respond", request(c, task, CaseRespond))
    with pytest.raises(AccountingError):
        cases.command(actor, task["id"], "review-response", request(c, task, CaseReviewResponse, decision="accept"))
    task = cases.command(c.manager, task["id"], "review-response", request(c, task, CaseReviewResponse, decision="accept"))
    with c.db.transaction(actor=actor) as conn:
        assert conn.execute("SELECT evidence_ref FROM sources WHERE id=?", (draft["source_id"],)).fetchone()[0] == ""
        assert "case:" in conn.execute("SELECT evidence_ref FROM source_evidence WHERE source_id=?", (draft["source_id"],)).fetchone()[0]
        assert conn.execute("SELECT version FROM drafts WHERE id=?", (draft["id"],)).fetchone()[0] == draft["version"] + 1


def test_active_case_blocks_without_actor_and_closed_case_does_not_approve(business):
    c = business
    cases, _, mapping, task = setup(c, "exception")
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        source = dict(conn.execute("SELECT * FROM sources WHERE id=?", (mapping["source_id"],)).fetchone())
        assert cases.blocking_issues_in_transaction(conn, None, source) == ["异常待办未关闭，请由授权人员核对"]
    task = cases.command(c.actors["accountant"], task["id"], "close", request(c, task))
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        assert not cases.blocking_issues_in_transaction(conn, None, source)


def test_pre_handoff_case_follows_mapping_permissions_and_open_period(business):
    c = business
    cases = CaseService(c.db, c.service)
    event = approved(c)
    task = cases.create(c.actors["finance"], CaseCreate(event_id=event["id"], kind="supplement", title="移交前补充原件", reason="请补充合同", idempotency_key=uuid4().hex))
    assert task["book_id"] is None
    with pytest.raises(AccountingError):
        cases.get(c.actors["accountant"], task["id"])
    receipt = handoff(c, event)
    assigned = cases.get(c.actors["accountant"], task["id"])
    assert assigned["book_id"] == receipt["book_id"] and assigned["draft_id"] == receipt["draft_id"]
    assert cases.list(c.actors["accountant"], book_id=receipt["book_id"])["total"] == 1
    with c.db.transaction(write=True, actor=c.manager) as conn:
        restore_period_state(c.ledger.db, receipt["book_id"], conn=conn)
    with pytest.raises(AccountingError):
        cases.require_upload(c.actors["employee"], task["id"], task["version"])
    with pytest.raises(AccountingError):
        cases.command(c.actors["finance"], task["id"], "comment", request(c, task, CaseComment, audience="requester"))


def test_bank_assigned_finance_editor_collects_without_internal_notes_or_self_acceptance(business):
    from app.accounting.models import ImportRequest
    c = business
    c.ledger.import_sources(c.manager, c.book["id"], ImportRequest(source_name="合成银行流水", rows=[{"external_id": "case-bank-collect-01", "date": "2026-10-04", "description": "办公室房租", "counterparty": "出租方", "amount": "10.00", "direction": "expense", "evidence_ref": ""}]))
    c.ledger.suggestions(c.manager, c.book["id"])
    draft = next(row for row in c.ledger.get_book(c.manager, c.book["id"])["drafts"] if row["source_kind"] == "bank")
    cases = CaseService(c.db, c.service)
    reviewer, finance = c.actors["accountant"], c.actors["finance"]
    task = cases.create(reviewer, CaseCreate(book_id=c.book["id"], draft_id=draft["id"], title="内部银行调查", reason="会计内部原因不可见", idempotency_key=uuid4().hex))
    assert cases.list(finance)["total"] == 0
    task = cases.command(reviewer, task["id"], "assign", CaseAssign(expected_version=task["version"], idempotency_key=uuid4().hex, reason="请补充租赁合同和付款用途", assignee_id=finance.user_id))
    own = cases.get(finance, task["id"])
    assert own["title"] == "银行流水资料补充" and own["request_reason"] == "请补充租赁合同和付款用途"
    assert "会计内部原因不可见" not in str(own) and own["permissions"]["upload"] and not own["permissions"]["manage"]
    assert cases.list(finance, mine=True)["total"] == 1 and cases.list(c.actors["employee"])["total"] == 0
    stored = {"id": uuid4().hex, "storage_key": "dd/" + "d" * 32, "filename": "租赁合同.pdf", "media_type": "application/pdf", "sha256": "d" * 64, "size_bytes": 100}
    own = cases.add_attachment(finance, task["id"], own["version"], uuid4().hex, stored)["case"]
    own = cases.command(finance, task["id"], "respond", request(c, own, CaseRespond))
    with pytest.raises(AccountingError):
        cases.command(finance, task["id"], "review-response", request(c, own, CaseReviewResponse, decision="accept"))
    with pytest.raises(AccountingError):
        cases.command(finance, task["id"], "close", request(c, own))
    with pytest.raises(AccountingError):
        cases.command(finance, task["id"], "comment", request(c, own, CaseComment, audience="internal"))
    reviewed = cases.command(reviewer, task["id"], "review-response", request(c, own, CaseReviewResponse, decision="accept"))
    assert reviewed["state"] == "in_review" and reviewed["responses"][0]["created_by"] == finance.user_id


def test_finance_book_viewer_can_manage_company_material_but_cannot_mutate_or_review_journal(business):
    from app.accounting.models import BookMemberGrant, ApprovalRequest, DraftUpdate
    c = business
    event = approved(c)
    mapping = handoff(c, event)
    finance = c.actors["finance"]
    c.directory.grant_book_member(c.manager, c.book["id"], finance.user_id, BookMemberGrant(role="viewer"))
    cases = CaseService(c.db, c.service)
    row = cases.create(finance, CaseCreate(event_id=event["id"], kind="supplement", title="企业财务补件要求", reason="受限公司财务要求补充用途", idempotency_key=uuid4().hex))
    row = cases.command(c.actors["employee"], row["id"], "respond", request(c, row, CaseRespond))
    row = cases.command(finance, row["id"], "review-response", request(c, row, CaseReviewResponse, decision="accept"))
    assert row["state"] == "in_review"
    draft = c.ledger.get_draft(finance, c.book["id"], mapping["draft_id"])
    assert draft["status"] == "draft"
    with pytest.raises(AccountingError):
        c.ledger.update_draft(finance, c.book["id"], draft["id"], DraftUpdate(expected_version=draft["version"], description=draft["description"], lines=draft["lines"]))
    with pytest.raises(AccountingError):
        c.ledger.approve(finance, c.book["id"], draft["id"], ApprovalRequest(expected_version=draft["version"]))
    with c.db.transaction(actor=c.manager) as conn:
        audit = conn.execute("SELECT book_id,details FROM audit_events WHERE action='case.create' AND object_id=?", (row["id"],)).fetchone()
        assert audit["book_id"] is None and "受限公司财务" not in audit["details"]
