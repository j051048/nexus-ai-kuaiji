"""Actual RLS-enabled cost and worker integration with synthetic reported tokens."""
from threading import Event
from concurrent.futures import ThreadPoolExecutor
import pytest

from app.intelligence.models import ProviderUsage
from app.intelligence.service import IntelligenceService
from app.operations.meter import MeterService, BudgetError
from app.operations.pricing import Pricing, Rate
from app.operations.service import OperationsService
from app.operations.requests import PolicyRequest, ReconcileRequest
from app.jobs.service import QueueService
from app.jobs.worker import QueueWorker
from test_queue_worker_postgres import pg_queue, pg_ai, business_case, business_postgres, ADMIN_URL, APP_URL, WORKER_URL, enqueue
from test_model_cost_workflow import UsageProvider
from test_intelligence_workflow import create_event, extract

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL or not WORKER_URL, reason="需要独立PG管理员、API、worker测试连接")


@pytest.fixture
def pg_cost(pg_queue):
    case = pg_queue
    case.provider = UsageProvider(document=case.provider.document, usage=ProviderUsage(prompt_tokens=1000, completion_tokens=200, total_tokens=1200))
    pricing = Pricing(ocr=Rate("1.000000", "2.000000"), proposal=Rate("1.000000", "2.000000"))
    case.intelligence.provider = case.provider
    case.meter = MeterService(case.db, pricing=pricing)
    case.intelligence.meter = case.meter
    case.operations = OperationsService(case.db, case.meter, case.intelligence)
    case.operations.policy(case.manager, case.company["id"], PolicyRequest(expected_version=0, quota_enabled=True, monthly_limit="0.10", ocr_reserve="0.10", proposal_reserve="0.10"))
    worker_db = case.worker.queue.db
    intelligence = IntelligenceService(worker_db, case.storage, case.provider)
    intelligence.meter = MeterService(worker_db, pricing=pricing)
    case.worker = QueueWorker(QueueService(worker_db, intelligence), worker_id="pg-meter-worker")
    return case


def test_actual_pg_worker_meter_result_and_quota_scope(pg_cost):
    case = pg_cost
    event = create_event(case)
    batch = enqueue(case, event)
    assert case.worker.run_once()
    overview = case.operations.overview(case.manager, case.company["id"])
    assert overview["usage"]["estimated_cost"] == "0.001400", overview
    assert overview["usage"]["reserved_cost"] == "0.000000"
    assert overview["usage"]["input_tokens"] == 1000 and overview["workers"]["available"]
    assert case.queue.get_batch(case.actors["employee"], batch["id"])["progress"]["succeeded"] == 1
    assert case.operations.companies(case.actors["employee"]) == {"companies": []}
    assert event["id"] not in str(overview) and event["attachments"][0]["id"] not in str(overview)


def test_pg_inflight_last_budget_cannot_be_spent_twice(pg_cost):
    case = pg_cost
    event = create_event(case)
    other = create_event(case, actor="peer")
    entered, release = Event(), Event()
    def block():
        entered.set()
        assert release.wait(10)
    case.provider.extract_hook = block
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(extract, case, event, "employee", "budget-first-pg-request")
        try:
            assert entered.wait(10)
            with pytest.raises(BudgetError) as blocked:
                extract(case, other, "peer", "budget-last-pg-request")
            assert blocked.value.code == "quota_exhausted"
        finally:
            release.set()
        assert running.result(timeout=10)["status"] == "succeeded"
    assert case.provider.extract_calls == 1


def test_pg_cost_is_settled_after_original_evidence_permission_is_revoked(pg_cost):
    case = pg_cost
    event = create_event(case)
    case.provider.extract_hook = lambda: case.directory.revoke_company_member(case.manager, case.company["id"], case.actors["employee"].user_id)
    with pytest.raises(Exception):
        extract(case, event)
    view = case.operations.overview(case.manager, case.company["id"])
    assert view["usage"]["estimated_cost"] == "0.001400", view
    with case.psycopg.connect(ADMIN_URL) as conn:
        run = conn.execute("SELECT status,result_json FROM accounting.ai_runs WHERE event_id=%s", (event["id"],)).fetchone()
    assert run[0] == "stale" and run[1] is None


def test_pg_unknown_cost_is_held_and_only_budget_manager_can_reconcile(pg_cost):
    case = pg_cost
    case.provider.usage = None
    event = create_event(case)
    assert extract(case, event)["status"] == "succeeded"
    view = case.operations.overview(case.actors["finance"], case.company["id"])
    cost = view["costs"][0]
    assert cost["state"] == "uncertain" and view["usage"]["exposure"] == "0.100000"
    assert view["usage"]["input_tokens"] is None and view["usage"]["estimated_cost"] is None
    request = ReconcileRequest(expected_version=cost["version"], confirmed_cost="0.03", reason="已核对合成供应商结算，仅验证授权与金额")
    with pytest.raises(Exception):
        case.operations.reconcile(case.actors["finance"], cost["id"], request)
    settled = case.operations.reconcile(case.manager, cost["id"], request)
    assert settled["confirmed_cost"] == "0.030000" and settled["estimated_cost"] is None


def test_pg_ops_failure_alerts_do_not_open_employee_originals(pg_cost):
    case = pg_cost
    event = create_event(case)
    enqueue(case, event)
    case.operations.policy(case.manager, case.company["id"], PolicyRequest(expected_version=1, quota_enabled=True, monthly_limit="0.10", ocr_reserve="0.10", proposal_reserve="0.10", failure_threshold=1))
    from app.intelligence.provider import ProviderError
    case.provider.failure = ProviderError("schema_invalid", "合成模型响应无效")
    assert case.worker.run_once()
    view = case.operations.refresh(case.manager, case.company["id"])
    assert any(alert["code"] == "task_failures" and alert["state"] == "open" for alert in view["alerts"]), view
    with pytest.raises(Exception):
        case.intelligence.detail(case.manager, event["id"])
