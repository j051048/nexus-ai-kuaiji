"""Real restricted PostgreSQL handoff/dependency regressions, without AI calls."""

from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, ExportRequest
from app.business.models import HandoffRequest
from app.business.storage import AttachmentStore
from app.intelligence.service import IntelligenceService
from app.intelligence.requests import OCRRequest
from app.intelligence.endpoints import (
    AIEndpointPolicyService,
    CapabilityCreate,
    CompanyPolicyUpdate,
    EndpointCreate,
)
from app.jobs.service import QueueService
from app.jobs.requests import AutomationPolicyRequest
from app.jobs.worker import ProposalExecution, QueueWorker
from test_business_postgres import (
    ADMIN_URL,
    APP_URL,
    business_case,  # noqa: F401
    business_postgres,  # noqa: F401
)
from test_intelligence_workflow import FakeProvider, create_event, invoice_document
from test_queue_postgres import WORKER_URL

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL,
    reason="需要独立 PostgreSQL 管理员和受限 API 测试 DSN",
)


@pytest.fixture
def handoff_case(business_case, tmp_path):
    original = business_case
    storage = AttachmentStore(tmp_path / "proposal116-originals")
    provider = FakeProvider(invoice_document(buyer_name="业务范围测试企业", buyer_tax_id=None))
    intelligence = IntelligenceService(original.db, storage, provider)
    queue = QueueService(original.db, intelligence)
    intelligence.company_settings(original.owner, original.company, enabled=True)
    values = vars(original).copy()
    values.update(
        actors={
            "employee": original.employee,
            "approver": original.approver,
            "finance": original.finance,
            "accountant": original.reviewer,
        },
        manager=original.owner,
        company={"id": original.company},
        book={"id": original.book},
        business=intelligence.business,
        storage=storage,
        provider=provider,
        intelligence=intelligence,
        queue=queue,
    )
    case = SimpleNamespace(**values)
    yield case
    # Local fixture cleanup preserves immutable evidence but leaves no eligible
    # work behind for a later test's real cross-company dispatcher.
    with case.psycopg.connect(ADMIN_URL) as conn:
        for table in ("ocr_attempts", "proposal_attempts"):
            conn.execute(
                f"UPDATE accounting.{table} SET phase='finished',status='cancelled',"
                "finished_at=accounting.ocr_utc_now() WHERE firm_id=%s AND status='running'",
                (case.owner.firm_id,),
            )
        for table in ("ocr_jobs", "proposal_jobs"):
            conn.execute(
                f"UPDATE accounting.{table} SET status='cancelled',lease_token=NULL,"
                "lease_until=NULL,worker_id=NULL,finished_at=accounting.ocr_utc_now() "
                "WHERE firm_id=%s AND status IN ('waiting','queued','running','retry_wait')",
                (case.owner.firm_id,),
            )


def ready_event(case):
    event = create_event(case)
    event = case.business.submit(case.employee, event["id"], event["version"])
    event = case.business.approve_department(case.approver, event["id"], event["version"])
    return case.business.approve_finance(case.finance, event["id"], event["version"])


def handoff(case, event):
    return case.business.handoff(
        case.finance,
        event["id"],
        HandoffRequest(
            expected_version=event["version"],
            book_id=case.book["id"],
            idempotency_key="proposal116-" + event["id"],
        ),
    )


def manage_ai(case):
    service = AIEndpointPolicyService(case.db)
    settings = case.provider.settings
    parsed = urlsplit(settings.base_url)
    endpoint = next(
        (
            item
            for item in service.list_endpoints(case.owner)
            if item["host"] == parsed.hostname and item["path_prefix"] == parsed.path
        ),
        None,
    )
    if not endpoint:
        endpoint = service.create_endpoint(
            case.owner,
            EndpointCreate(
                host=parsed.hostname,
                path_prefix=parsed.path,
                service_identity="本地合成供应商，不发起网络请求",
                capabilities=[
                    CapabilityCreate(
                        scenario=scenario,
                        model=model,
                        supports_image=scenario == "ocr",
                        data_location="local-test-fixture",
                        reason="合成端点能力验收",
                    )
                    for scenario, model in (
                        ("ocr", settings.vision_model),
                        ("journal", settings.journal_model),
                    )
                ],
                reason="本地测试合成供应商，不发起网络请求",
            ),
        )
    request = CompanyPolicyUpdate(
        allowed_scenarios=["ocr", "journal"],
        allowed_data_categories=["document", "counterparty", "amount", "accounting"],
        allowed_endpoint_ids=[endpoint["id"]],
        reason="本地测试批准合成供应商，仅使用stub输出",
    )
    service.update_policy(case.owner, case.company["id"], request)
    return service, request


def counts(case, event):
    with case.db.transaction(actor=case.finance) as conn:
        return {
            table: conn.execute(
                f"SELECT count(*) FROM {table} WHERE event_id=?", (event["id"],)
            ).fetchone()[0]
            for table in (
                "business_mappings",
                "proposal_jobs",
                "proposal_dependencies",
                "ocr_jobs",
            )
        } | {
            table: conn.execute(
                f"SELECT count(*) FROM {table} WHERE book_id=?", (case.book["id"],)
            ).fetchone()[0]
            for table in ("sources", "drafts")
        }


def test_pg116_real_service_handoff_commits_draft_and_ocr_dependency_atomically(
    handoff_case,
):
    case = handoff_case
    event = ready_event(case)
    result = handoff(case, event)
    job = case.queue.proposals.get(case.finance, result["automatic_job"]["id"])
    assert job["status"] == "waiting"
    assert job["expected_fact_version"] == event["fact_version"]
    assert counts(case, event) == {
        "business_mappings": 1,
        "proposal_jobs": 1,
        "proposal_dependencies": 1,
        "ocr_jobs": 1,
        "sources": 1,
        "drafts": 1,
    }
    assert handoff(case, event) == result
    draft = case.service.get_draft(case.finance, result["book_id"], result["draft_id"])
    assert draft["status"] == "draft"
    assert not draft.get("approved_by")
    assert case.provider.extract_calls == case.provider.proposal_calls == 0


def test_pg116_handoff_failure_rolls_back_source_draft_and_dependency(handoff_case, monkeypatch):
    case = handoff_case
    event = ready_event(case)
    before = counts(case, event)
    dependencies = case.queue.proposals._dependencies

    def fail_after_dependency_insert(*args, **kwargs):
        assert dependencies(*args, **kwargs) == 1
        raise AccountingError("合成故障：依赖写入后中断，验证整单回滚", 409)

    monkeypatch.setattr(case.queue.proposals, "_dependencies", fail_after_dependency_insert)
    with pytest.raises(AccountingError, match="整单回滚"):
        handoff(case, event)
    assert counts(case, event) == before
    current = case.business.get_event(case.finance, event["id"])
    assert current["status"] == "finance_approved"
    assert current["version"] == event["version"]
    assert case.provider.extract_calls == case.provider.proposal_calls == 0


@pytest.mark.skipif(not WORKER_URL, reason="需要独立 PostgreSQL worker 测试 DSN")
@pytest.mark.parametrize("managed", [False, True])
def test_pg116_real_worker_ocr_and_proposal_stop_before_accountant_approval(
    handoff_case, monkeypatch, managed
):
    case = handoff_case
    if managed:
        manage_ai(case)
    result = handoff(case, ready_event(case))
    worker_db = Database(WORKER_URL)
    worker_intelligence = IntelligenceService(worker_db, case.storage, case.provider)
    worker_queue = QueueService(worker_db, worker_intelligence)
    worker = QueueWorker(worker_queue, worker_id="proposal116-stub-worker")

    def report_failure(job, status, code, detail):
        raise AssertionError(f"Unexpected worker failure: {status}/{code}: {detail}")

    monkeypatch.setattr(worker, "_failure", report_failure)
    ocr = worker_queue.claim(worker.worker_id)
    assert ocr["event_id"] == result["event"]["id"] and ocr["job_kind"] == "ocr"
    worker.process(ocr)
    # A managed policy uses its version/endpoint in both extraction and every
    # dependency/budget plan. Re-reading success must not enqueue or bill OCR.
    with case.db.transaction(actor=case.finance) as conn:
        event = case.business._event(conn, case.finance, result["event"]["id"])
        assert case.queue.proposals._dependency_plan(conn, event, case.finance) == ([], [])
        assert case.queue.automation._model_plan(conn, case.finance, event)["ocr"] == 0
    assert case.provider.extract_calls == 1
    with worker_queue.worker_transaction(dispatch=True) as conn:
        assert (
            conn.execute("SELECT accounting.worker_refresh_proposal_dependencies()").fetchone()[0]
            == 1
        )
    proposal = worker_queue.claim(worker.worker_id)
    assert proposal["id"] == result["automatic_job"]["id"]
    assert proposal["job_kind"] == "proposal"
    worker.process(proposal)
    job = case.queue.proposals.get(case.finance, proposal["id"])
    assert job["status"] == "succeeded", job
    draft = case.service.get_draft(case.finance, result["book_id"], result["draft_id"])
    assert draft["status"] == "draft" and not draft["issues"]
    assert case.provider.extract_calls == case.provider.proposal_calls == 1
    with pytest.raises(AccountingError):
        case.service.export(
            case.reviewer,
            result["book_id"],
            ExportRequest(draft_ids=[draft["id"]], idempotency_key="116-unreviewed"),
        )
    approved = case.service.approve(
        case.reviewer,
        result["book_id"],
        draft["id"],
        ApprovalRequest(expected_version=draft["version"]),
    )
    assert approved["status"] == "approved"
    assert (
        case.service.export(
            case.reviewer,
            result["book_id"],
            ExportRequest(draft_ids=[draft["id"]], idempotency_key="116-reviewed"),
        )["count"]
        == 1
    )


def test_pg116_cross_company_handoff_is_denied_without_partial_writes(handoff_case):
    case = handoff_case
    event = ready_event(case)
    before = counts(case, event)
    with pytest.raises(AccountingError) as denied:
        case.business.handoff(
            case.finance,
            event["id"],
            HandoffRequest(
                expected_version=event["version"],
                book_id=case.other_book,
                idempotency_key="proposal116-foreign-" + uuid4().hex,
            ),
        )
    assert denied.value.status in {403, 404}
    assert counts(case, event) == before


def test_pg116_production_without_ai_policy_preserves_handoff_for_human(handoff_case, monkeypatch):
    case = handoff_case
    event = ready_event(case)
    monkeypatch.setenv("ACCOUNTING_ENV", "production")
    result = handoff(case, event)
    job = case.queue.proposals.get(case.finance, result["automatic_job"]["id"])
    assert job["status"] == "needs_attention" and job["error_code"] == "policy_invalid"
    assert counts(case, event) == {
        "business_mappings": 1,
        "proposal_jobs": 1,
        "proposal_dependencies": 0,
        "ocr_jobs": 0,
        "sources": 1,
        "drafts": 1,
    }
    assert result["event"]["status"] == "handed_off"
    assert case.provider.extract_calls == case.provider.proposal_calls == 0


@pytest.mark.skipif(not WORKER_URL, reason="需要独立 PostgreSQL worker 测试 DSN")
def test_pg118_company_attention_watch_can_resume_after_authorized_remediation(
    handoff_case,
):
    case = handoff_case
    event = ready_event(case)
    case.intelligence.company_settings(case.owner, case.company["id"], enabled=False)
    result = handoff(case, event)
    job_id = result["automatic_job"]["id"]
    assert case.queue.proposals.get(case.finance, job_id)["error_code"] == "processing_disabled"
    with case.db.transaction(actor=case.finance) as conn:
        assert conn.execute(
            "SELECT generation,blocker FROM proposal_resume_watch WHERE job_id=?", (job_id,)
        ).fetchone()[:] == (1, "processing_disabled")
        assert not conn.execute(
            "SELECT has_table_privilege(current_user,'accounting.proposal_resume_watch','UPDATE')"
        ).fetchone()[0]
    case.queue.automation.update_policy(
        case.owner,
        case.company["id"],
        AutomationPolicyRequest(
            expected_version=0, enabled=True, reason="本地恢复测试：批准确定性条件恢复后重排"
        ),
    )
    case.intelligence.company_settings(case.owner, case.company["id"], enabled=True)
    worker_db = Database(WORKER_URL)
    worker_queue = QueueService(
        worker_db, IntelligenceService(worker_db, case.storage, case.provider)
    )
    assert worker_queue.automation.resume_pending() == 1
    resumed = case.queue.proposals.get(case.finance, job_id)
    assert resumed["status"] == "waiting"
    with case.db.transaction(actor=case.finance) as conn:
        assert (
            conn.execute("SELECT generation FROM proposal_jobs WHERE id=?", (job_id,)).fetchone()[0]
            == 2
        )
    # Historical watch rows remain; a resumed generation is no longer eligible.
    assert worker_queue.automation._candidates(12) == []
    case.queue.proposals.cancel(case.finance, job_id, "本地验收完成，取消未发送的合成任务")
    assert worker_queue.automation._candidates(12) == []
    assert case.provider.extract_calls == case.provider.proposal_calls == 0


@pytest.mark.skipif(not WORKER_URL, reason="需要独立 PostgreSQL worker 测试 DSN")
def test_pg118_worker_company_finish_can_capture_a_resumable_attention_watch(handoff_case):
    case = handoff_case
    result = handoff(case, ready_event(case))
    worker_db = Database(WORKER_URL)
    worker_queue = QueueService(
        worker_db, IntelligenceService(worker_db, case.storage, case.provider)
    )
    worker = QueueWorker(worker_queue, worker_id="resume118-stub-worker")
    ocr = worker_queue.claim(worker.worker_id)
    assert ocr["job_kind"] == "ocr"
    worker.process(ocr)
    with worker_queue.worker_transaction(dispatch=True) as conn:
        conn.execute("SELECT accounting.worker_refresh_proposal_dependencies()")
    proposal = worker_queue.claim(worker.worker_id)
    assert proposal["id"] == result["automatic_job"]["id"]
    with worker_db.transaction(
        write=True, actor=case.finance, company_id=case.company["id"], write_scope="company"
    ) as conn:
        worker._finish_proposal_attention(
            conn,
            proposal,
            ProposalExecution(worker_queue, proposal),
            "policy_invalid",
            "本地合成：发布前发现政策待处理，转人工后保留恢复监视",
        )
    assert case.queue.proposals.get(case.finance, proposal["id"])["status"] == "needs_attention"
    with case.db.transaction(actor=case.finance) as conn:
        assert (
            conn.execute(
                "SELECT blocker FROM proposal_resume_watch WHERE job_id=?", (proposal["id"],)
            ).fetchone()[0]
            == "policy_invalid"
        )
    draft = case.service.get_draft(case.finance, result["book_id"], result["draft_id"])
    assert draft["status"] == "draft"
    assert case.provider.extract_calls == 1 and case.provider.proposal_calls == 0


@pytest.mark.parametrize("outside_scope", ["company", "firm", "dispatcher"])
def test_pg118_watch_guard_rejects_foreign_scope_even_for_local_test_admin(
    handoff_case, outside_scope
):
    case = handoff_case
    event = ready_event(case)
    case.intelligence.company_settings(case.owner, case.company["id"], enabled=False)
    job_id = handoff(case, event)["automatic_job"]["id"]
    # This administrator connection is only a local fixture probe. Actual API
    # and worker roles have no direct watch DML privilege, verified above.
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.psycopg.connect(ADMIN_URL) as conn:
            if outside_scope == "dispatcher":
                conn.execute("SELECT set_config('app.write_dispatch','1',true)")
            else:
                firm = case.owner.firm_id if outside_scope == "company" else "foreign-firm"
                company = case.other_company if outside_scope == "company" else case.company["id"]
                conn.execute("SELECT set_config('app.write_scope_firm',%s,true)", (firm,))
                conn.execute("SELECT set_config('app.write_scope_company',%s,true)", (company,))
            conn.execute(
                "UPDATE accounting.proposal_resume_watch SET check_count=check_count+1 WHERE job_id=%s",
                (job_id,),
            )


def test_pg116_dependency_helper_checks_declared_company_before_mutation(handoff_case):
    case = handoff_case
    result = handoff(case, ready_event(case))
    job_id = result["automatic_job"]["id"]
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(
            write=True,
            actor=case.owner,
            company_id=case.other_company,
            write_scope="company",
        ) as conn:
            conn.execute("SELECT accounting.proposal_wait_dependencies(?)", (job_id,))
    assert case.queue.proposals.get(case.finance, job_id)["status"] == "waiting"


def test_pg116_dependency_helper_cannot_be_entered_from_dispatcher(handoff_case):
    case = handoff_case
    result = handoff(case, ready_event(case))
    with pytest.raises(case.psycopg.errors.ObjectNotInPrerequisiteState):
        with case.db.transaction(actor=case.owner) as conn:
            # The low-level dispatcher mutex itself is not granted to API roles.
            # Even a spoofed local marker cannot make this SECURITY DEFINER
            # company helper mutate dispatcher-owned work.
            conn.execute("SELECT set_config('app.write_dispatch','1',true)")
            conn.execute(
                "SELECT accounting.proposal_wait_dependencies(?)",
                (result["automatic_job"]["id"],),
            )


@pytest.mark.parametrize("change_at", ["before_send", "before_publish"])
def test_pg116_managed_ocr_policy_version_change_rejects_stale_work(
    handoff_case, monkeypatch, change_at
):
    case = handoff_case
    policy, request = manage_ai(case)
    event = create_event(case)

    def new_policy_version():
        policy.update_policy(case.owner, case.company["id"], request)

    if change_at == "before_send":
        from app.intelligence import service as intelligence_module

        prepare = intelligence_module.prepare_document

        def changed_after_prepare(*args, **kwargs):
            pages = prepare(*args, **kwargs)
            new_policy_version()
            return pages

        monkeypatch.setattr(intelligence_module, "prepare_document", changed_after_prepare)
    else:
        case.provider.extract_hook = new_policy_version

    with pytest.raises(AccountingError) as stale:
        case.intelligence.extract(
            case.employee,
            event["id"],
            OCRRequest(
                attachment_id=event["attachments"][0]["id"],
                expected_version=event["version"],
                idempotency_key="116-policy-change-" + event["id"],
            ),
        )
    assert stale.value.status == 409
    assert case.provider.extract_calls == (0 if change_at == "before_send" else 1)
    with case.db.transaction(actor=case.employee) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM ai_runs WHERE event_id=? AND status='succeeded'",
                (event["id"],),
            ).fetchone()[0]
            == 0
        )
    # The restricted API role deliberately has no direct cache-table SELECT.
    # Inspect absence using only the dedicated local test administrator.
    with case.psycopg.connect(ADMIN_URL) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM accounting.document_extractions WHERE company_id=%s",
                (case.company["id"],),
            ).fetchone()[0]
            == 0
        )
    current = case.business.get_event(case.employee, event["id"])
    assert current["payload"] == event["payload"]
    assert current["version"] == event["version"]


def test_pg116_only_result_helpers_move_to_company_domain(handoff_case):
    case = handoff_case
    with case.db.transaction(actor=case.owner) as conn:
        for name in (
            "proposal_wait_dependencies",
            "cancel_proposal_job",
            "cancel_resumable_proposal",
            "worker_bind_proposal_run",
            "worker_mark_proposal_sending",
            "worker_renew_proposal_lease",
            "worker_finish_proposal_job",
            "worker_finish_proposal_rule",
            "worker_finish_proposal_company_rule",
            "worker_resume_proposal",
        ):
            row = conn.execute(
                "SELECT p.prosrc,p.prosecdef,has_function_privilege('public',p.oid,'EXECUTE') AS public_exec "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='accounting' AND p.proname=?",
                (name,),
            ).fetchone()
            assert "accounting.lock_legacy_write()" not in row["prosrc"]
            assert "accounting.lock_company_write(" in row["prosrc"]
            assert row["prosecdef"] and not row["public_exec"]
        for name in (
            "claim_proposal_job",
            "recover_proposal_jobs",
            "worker_refresh_proposal_dependencies",
        ):
            body = conn.execute(
                "SELECT p.prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='accounting' AND p.proname=?",
                (name,),
            ).fetchone()[0]
            assert "accounting.lock_dispatch_write()" in body
            assert "accounting.lock_company_write(" not in body
