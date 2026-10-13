"""Workspace evidence and job views under real FORCE RLS, current grants."""
import json
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.review import ReviewService
from app.agent_workspace.service import AgentWorkspaceService
from app.business.service import BusinessService
from app.cases.service import CaseService
from app.workspace.service import WorkspaceService
from test_batch_review_postgres import bank_draft
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_queue_postgres import queue_case
from test_stage13_migration_postgres import migration_case, _file, _grant, _job

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 管理员与受限 API 测试连接")


def services(c):
    business = BusinessService(c.db)
    workspace = WorkspaceService(c.db, c.service, business, ReviewService(c.db, c.service), CaseService(c.db, business))
    return workspace, AgentWorkspaceService(c.db, c.service, business, workspace)


def test_pg_task_amount_paging_and_company_search_exclude_other_company(business_case):
    c = business_case
    workspace, _ = services(c)
    own = bank_draft(c, c.book, "stage14-" + uuid4().hex)
    hidden = bank_draft(c, c.other_book, "stage14-hidden-" + uuid4().hex)
    value = workspace.list(c.reviewer, sort="amount", limit=1)
    assert value["pagination"]["total"] == 1 and value["tasks"][0]["amount"] == "100.00"
    assert value["tasks"][0]["ref"] == own["draft_id"] and hidden["draft_id"] not in json.dumps(value)
    companies = workspace.companies(c.reviewer, limit=1)
    assert companies["pagination"]["total"] == 1 and companies["companies"][0]["id"] == c.company
    assert workspace.companies(c.reviewer, offset=1)["companies"] == []


def test_pg_context_explanation_and_book_revocation_are_read_only(business_case):
    c = business_case
    _, agent = services(c)
    selected = bank_draft(c, c.book, "stage14-context-" + uuid4().hex)
    context = agent.context(c.reviewer, c.book, selected["draft_id"])
    assert context["scope"]["company_id"] == c.company and context["scope"]["draft_version"] == 1
    with c.db.transaction(actor=c.reviewer) as conn:
        before = conn.execute("SELECT COUNT(*) FROM audit_events WHERE book_id=?", (c.book,)).fetchone()[0]
    answer = agent.explain(c.reviewer, c.book, selected["draft_id"], "金额与借贷是否一致？")
    assert "100.00" in answer["answer"] and answer["mode"] == "evidence_summary"
    with c.db.transaction(actor=c.reviewer) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_events WHERE book_id=?", (c.book,)).fetchone()[0] == before
        assert conn.execute("SELECT status FROM drafts WHERE id=?", (selected["draft_id"],)).fetchone()[0] == "draft"
    with pytest.raises(AccountingError) as employee:
        agent.context(c.employee, c.book, selected["draft_id"])
    assert employee.value.status == 404
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s", (c.book, c.reviewer.user_id))
    with pytest.raises(AccountingError) as revoked:
        agent.explain(c.reviewer, c.book, selected["draft_id"], "下一步由谁处理？")
    assert revoked.value.status == 404


def test_pg_run_sql_authorizes_hidden_batch_members_before_count(queue_case, tmp_path):
    from app.business.models import BusinessEventCreate
    from app.business.storage import AttachmentStore
    from app.intelligence.service import IntelligenceService
    from app.jobs.requests import BatchItem, BatchRequest
    from app.jobs.service import QueueService
    from test_business_api import attachment_bytes
    from test_intelligence_workflow import FakeProvider

    c = queue_case
    _, agent = services(c)
    storage = AttachmentStore(tmp_path / "private-originals")
    provider = FakeProvider()
    intelligence = IntelligenceService(c.db, storage, provider)
    queue = QueueService(c.db, intelligence)
    # Exercise the shipping business commands and one real finance enqueue.
    # No table relocation or fixture-only partial batch INSERT circumvents the
    # immutable batch identity or employee visibility rules.
    events = []
    for applicant in (c.employee, c.colleague):
        event = intelligence.business.create(applicant, BusinessEventCreate(
            company_id=c.company, template_id="reimbursement", payload={
                "occurred_on": "2026-10-03", "description": "合成批次本人票据",
                "counterparty": "合成供应商", "amount": "0.30",
                "direction": "expense", "payment_status": "unpaid", "category": "office",
            }))
        attachment = storage.put("合成发票.png", "image/png", attachment_bytes())
        events.append(intelligence.business.add_attachment(applicant, event["id"], event["version"], attachment))
    batch = queue.enqueue(c.finance, BatchRequest(company_id=c.company,
        items=[BatchItem(event_id=event["id"], attachment_id=event["attachments"][0]["id"], expected_version=event["version"]) for event in events],
        idempotency_key=uuid4().hex))
    own = next(job for job in batch["jobs"] if job["event_id"] == events[0]["id"])
    hidden = next(job for job in batch["jobs"] if job["event_id"] == events[1]["id"])
    assert len(batch["jobs"]) == 2 and provider.extract_calls == 0
    value = agent.runs(c.employee, state="all", limit=1)
    assert value["pagination"]["total"] == 1 and value["runs"][0]["id"] == own["id"]
    assert value["runs"][0]["processed"] == 0 and value["runs"][0]["total"] == 1
    assert hidden["id"] not in json.dumps(value)
    assert agent.summary(c.employee)["counts"] == {"active": 1, "attention": 0, "done": 0, "total": 1}
    assert agent.runs(c.finance, state="all", limit=1)["pagination"]["total"] == 2
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("DELETE FROM accounting.company_memberships WHERE company_id=%s AND user_id=%s", (c.company, c.employee.user_id))
    assert agent.summary(c.employee)["counts"]["total"] == 0
    assert agent.companies(c.employee)["companies"] == []


def test_pg_migration_job_private_columns_are_never_selected_or_leaked(migration_case):
    c = migration_case
    _, agent = services(c)
    job = _job(c, _file(c))
    assert agent.runs(c.reviewer, state="all")["pagination"]["total"] == 0
    _grant(c, c.reviewer)
    value = agent.runs(c.reviewer, state="all")
    assert value["pagination"]["total"] == 1
    row = value["runs"][0]
    assert row["id"] == job and row["status"] == "queued" and row["total"] is None
    assert row["route"]["id"] == c.project
    assert not any(private in json.dumps(value) for private in ("lease_token", "lease_until", "worker_id", "checkpoint_json", "request_fingerprint"))
    with c.db.transaction(write=True, actor=c.owner) as conn:
        conn.execute("DELETE FROM migration_project_access WHERE project_id=? AND user_id=?", (c.project, c.reviewer.user_id))
    assert agent.runs(c.reviewer, state="all")["runs"] == []


def test_pg_postgres_numeric_export_amount_keeps_exact_decimal_string(business_case):
    from app.accounting.models import ApprovalRequest
    c = business_case
    workspace, _ = services(c)
    item = bank_draft(c, c.book, "stage14-export-" + uuid4().hex)
    c.service.approve(c.reviewer, c.book, item["draft_id"], ApprovalRequest(expected_version=item["expected_version"]))
    value = workspace.list(c.reviewer, kind="export", sort="amount")
    assert value["tasks"][0]["amount"] == "100.00"
