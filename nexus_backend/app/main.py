"""Local demo or PostgreSQL accounting with verified Supabase identity."""

import ipaddress
import logging
import os
import secrets
import sqlite3
import time
from uuid import uuid4
from contextlib import asynccontextmanager, closing
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import psycopg
from fastapi import Depends, FastAPI, Header, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .auth import AuthSettings, AuthError, Identity, SupabaseAuthenticator
from .auth.admin_security import allow_firm_signup, requires_admin_mfa
from .accounting.database import Database, DatabaseCapacityError
from .accounting.db_diagnostics import log_database_unavailable
from .agent_workspace.service import AgentWorkspaceService
from .agent_workspace.router import build_agent_workspace_router
from .assistant.service import AssistantService
from .assistant.provider import AssistantProvider
from .assistant.meter import AssistantMeter
from .assistant.actions import AssistantActionService
from .assistant.worker import AssistantWorker
from .assistant.router import build_assistant_router
from .accounting.directory import DirectoryService
from .accounting.admin_service import AdminService
from .accounting.admin_router import build_admin_router
from .accounting.admin_identity import AdminIdentityService
from .accounting.admin_identity_router import build_admin_identity_router
from .accounting.handover import HandoverRequest, HandoverService
from .accounting.models import (
    ApprovalRequest,
    BookCreate,
    DraftUpdate,
    EvidenceRequest,
    ExportRequest,
    ImportRequest,
    FirmCreate,
    CompanyCreate,
    FirmMemberGrant,
    BookMemberGrant,
    CompanyMemberGrant,
    DepartmentCreate,
)
from .accounting.seed import FIRM_NAME, LOCAL_ACTOR, USER_NAME, bootstrap
from .accounting.service import AccountingError, AccountingService, Actor
from .accounting.catalog import CatalogService
from .accounting.catalog_router import build_catalog_router
from .accounting.review import ReviewService
from .accounting.packages import ReviewPackagesService
from .accounting.review_router import build_review_router
from .accounting.review_policy_router import build_review_policy_router
from .accounting.bank_imports import BankImportService
from .accounting.bank_import_router import build_bank_import_router
from .cases.service import CaseService
from .cases.router import build_cases_router
from .cases.middleware import CaseAttachmentBodyLimitMiddleware
from .learning.service import LearningService
from .learning.router import build_learning_router
from .quality.service import QualityService
from .quality.router import build_quality_router
from .reconciliation.service import ReconciliationService
from .reconciliation.router import build_reconciliation_router
from .delivery.service import DeliveryService
from .delivery.monthly_package import MonthlyPackageService
from .delivery.followups import DeliveryFollowupService
from .delivery.followup_router import build_delivery_followup_router
from .delivery.router import build_delivery_router
from .delivery.middleware import DeliveryBodyLimitMiddleware
from .tools.accounting_tools import AccountingSuggestionTool
from .business.api import build_business_router
from .business.demo import PERSONAS, bootstrap_business_demo
from .business.service import BusinessService
from .business.storage import AttachmentBodyLimitMiddleware
from .storage.factory import create_attachment_store
from .storage.settings import StorageSettings
from .intelligence.provider import ProviderError
from .intelligence.router import build_intelligence_router
from .intelligence.service import IntelligenceService
from .intelligence.endpoints import AIEndpointPolicyService
from .intelligence.policy_router import build_ai_policy_router
from .jobs.service import QueueService
from .jobs.worker import QueueWorker
from .jobs.router import build_queue_router
from .batch_ops.router import build_batch_ops_router
from .handoff.router import build_handoff_router
from .handoff.service import HandoffService
from .batch_ops.service import BatchOperationsService
from .batch_ops.worker import BatchWorker
from .cockpit.router import build_cockpit_router
from .cockpit.service import FirmCockpitService
from .firm_tasks.router import build_firm_tasks_router
from .firm_tasks.service import FirmTasksService
from .onboarding.router import build_onboarding_router
from .onboarding.service import OnboardingService
from .observability.router import build_observability_router
from .observability.service import FirmObservabilityService
from .ingestion.middleware import IngestionBodyLimitMiddleware
from .ingestion.router import build_ingestion_router
from .ingestion.service import IngestionService
from .operations.meter import MeterService
from .operations.service import OperationsService
from .operations.router import build_operations_router
from .ledgers.service import LedgersService
from .ledgers.router import build_ledgers_router
from .opening.router import build_opening_router
from .opening.service import OpeningService
from .posting.router import build_posting_router
from .posting.service import PostingService
from .subledger.router import build_subledger_router
from .subledger.service import SubledgerService
from .periodic.router import build_periodic_router
from .periodic.service import PeriodicService
from .reports.router import build_report_router
from .reports.service import ReportService
from .tax.router import build_tax_router
from .tax.workpaper import TaxWorkpaperService
from .b04_close.router import build_close_router
from .b04_close.service import CloseService
from .integrations.service import FinancialBridgeService
from .settlements.service import SettlementService
from .settlements.router import build_settlement_router
from .integrations.router import build_integrations_router
from .integrations.middleware import BridgeBodyLimitMiddleware
from .revisions.service import FactRevisionService
from .revisions.router import build_revisions_router
from .dispositions.service import SourceDispositionService
from .dispositions.router import build_dispositions_router
from .operations.efficiency import EfficiencyService
from .operations.efficiency_router import build_efficiency_router
from .operations.telemetry import TelemetryService
from .operations.telemetry_router import build_telemetry_router
from .closing.service import ClosingService
from .closing.router import build_closing_router
from .closing.models import CloseRequest
from .monthly.service import MonthlyService
from .monthly.router import build_monthly_router
from .monthly.workbench import MonthlyWorkbenchService
from .monthly.workbench_router import build_monthly_workbench_router
from .monthly.batch import MonthlyBatchService
from .monthly.briefing import BriefingService
from .service_plans.service import ServicePlansService
from .service_plans.router import build_service_plans_router
from .service_plans.attachments import MaterialAttachmentService, build_material_attachments_router
from .uploads import UploadIntentService, build_uploads_router
from .invoices.router import build_invoice_imports_router
from .invoices.service import InvoiceImportService
from .revisions.history import SourceHistoryService
from .revisions.history_router import build_source_history_router
from .intake.service import IntakeService
from .intake.router import build_intake_router
from .import_wizard.service import ImportWizardService
from .import_wizard.router import build_import_wizard_router, ImportWizardBodyLimitMiddleware
from .workspace.service import WorkspaceService
from .workspace.router import build_workspace_router
from .legacy_migration.service import LegacyMigrationService
from .legacy_migration.worker import MigrationWorker
from .legacy_migration.router import build_legacy_migration_router, MigrationBodyLimitMiddleware
from .legacy_migration.learning import LegacyLearningService
from .legacy_migration.settlements import LegacySettlementService


logger = logging.getLogger(__name__)


def _drain_embedded_workers(application, workers, timeout=30):
    """Signal all lanes before sharing one shutdown deadline across their joins."""
    deadline = time.monotonic() + timeout
    complete = True
    for worker in reversed(workers):
        try:
            worker.stop()
        except Exception:
            complete = False
            logger.warning("嵌入后台停止信号失败，数据库将保留至进程退出")
    for worker in reversed(workers):
        try:
            if worker.wait_for_idle(max(0, deadline - time.monotonic())) is not True:
                complete = False
        except Exception:
            complete = False
            logger.warning("嵌入后台排空状态未知，数据库将保留至进程退出")
    application.state.unified_drain_incomplete = not complete
    return complete


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def validate_environment(settings, path):
    environment = os.getenv("ACCOUNTING_ENV", "local").lower()
    if environment not in {"local", "development", "test", "production"}:
        raise RuntimeError("ACCOUNTING_ENV 必须为 local、development、test 或 production")
    production = environment == "production" or any(
        os.getenv(key, "").lower() in {"production", "prod"}
        for key in ("APP_ENV", "ENV", "ENVIRONMENT")
    )
    postgres = str(path).startswith(("postgres://", "postgresql://"))
    if settings.mode == "supabase" and not postgres:
        raise RuntimeError("真实登录模式必须使用独立 PostgreSQL，禁止复用本地示例数据库")
    legacy_nonlocal = any(
        os.getenv(key, "").lower() not in {"", "local", "development", "test"}
        for key in ("APP_ENV", "ENV", "ENVIRONMENT")
    )
    if settings.mode == "local" and (postgres or production or legacy_nonlocal):
        raise RuntimeError(
            "禁止本地演示身份进入非本地环境；真实企业请配置 Supabase 登录和 PostgreSQL"
        )
    if production:
        if settings.mode != "supabase" or not postgres:
            raise RuntimeError("生产模式必须使用 PostgreSQL 与 Supabase 真实登录")
        if parse_qs(urlsplit(str(path)).query).get("sslmode", [""])[0] not in {
            "require",
            "verify-ca",
            "verify-full",
        }:
            raise RuntimeError("生产 PostgreSQL 连接必须显式配置 sslmode=require 或证书校验")
        if not os.getenv("ACCOUNTING_ALLOWED_HOSTS") or not os.getenv("ACCOUNTING_ALLOWED_ORIGINS"):
            raise RuntimeError("生产模式必须显式配置域名与网页来源")
    return production


def create_app(
    db_path: str | Path | None = None,
    seed: bool | None = None,
    auth_settings=None,
    authenticator=None,
    attachment_root=None,
    intelligence_provider=None,
    queue_worker_enabled=None,
    model_pricing=None,
    assistant_provider=None,
):
    settings = auth_settings or AuthSettings.from_env()
    firm_signup_enabled = allow_firm_signup(settings.mode, os.environ)
    path = (
        db_path
        or os.getenv("ACCOUNTING_DATABASE_URL")
        or os.getenv("ACCOUNTING_DB_PATH")
        or Path(__file__).resolve().parents[1] / "data" / "accounting.db"
    )
    production = validate_environment(settings, path)
    storage_settings = StorageSettings.from_env()
    configured_attachment_root = attachment_root or os.getenv("ACCOUNTING_ATTACHMENT_ROOT")
    storage_path = (
        configured_attachment_root or Path(__file__).resolve().parents[1] / "private-attachments"
    )
    from .jobs.restore_hold import enforce_restore_hold

    enforce_restore_hold(database_path=path, attachment_root=storage_path)
    hosts = [
        x.strip()
        for x in os.getenv("ACCOUNTING_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],::1").split(",")
        if x.strip()
    ]
    origins = [
        x.strip().rstrip("/")
        for x in os.getenv(
            "ACCOUNTING_ALLOWED_ORIGINS",
            "http://127.0.0.1:8080,http://localhost:8080,http://127.0.0.1:18080,http://localhost:18080",
        ).split(",")
        if x.strip()
    ]
    if not hosts or "*" in hosts or not origins or "*" in origins:
        raise RuntimeError("域名和网页来源须使用明确白名单，不能配置通配符")
    for origin in origins:
        try:
            parsed = urlsplit(origin)
            valid = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and parsed.path == ""
                and not parsed.query
                and not parsed.fragment
                and not parsed.username
            )
            if production:
                valid = valid and parsed.scheme == "https"
        except ValueError:
            valid = False
        if not valid:
            raise RuntimeError("ACCOUNTING_ALLOWED_ORIGINS 必须为完整网页来源，生产须使用 HTTPS")
    if production and storage_settings.backend == "local" and not configured_attachment_root:
        raise RuntimeError(
            "本地原件存储的生产模式须配置 ACCOUNTING_ATTACHMENT_ROOT 持久目录；独立容器可改用私有 Supabase Storage"
        )

    @asynccontextmanager
    async def lifespan(application):
        database = Database(path)
        projection_database = None
        workers = []
        try:
            projection_url = os.getenv("ACCOUNTING_PROJECTION_DATABASE_URL", "")
            if projection_url.startswith(("postgres://", "postgresql://")):
                projection_database = Database(projection_url, default_role="accounting_projection")
            database.assert_restore_hold()
            if database.is_postgres:
                with closing(database.connect()) as conn:
                    if conn.execute(
                        "SELECT pg_has_role(session_user,'accounting_worker','MEMBER')"
                    ).fetchone()[0]:
                        raise RuntimeError(
                            "API与后台处理必须使用独立账号；API登录不得授予accounting_worker"
                        )
            application.state.service = AccountingService(database)
            application.state.ledgers = LedgersService(database)
            application.state.opening = OpeningService(database)
            application.state.posting = PostingService(database)
            application.state.ai_policy = AIEndpointPolicyService(database)
            application.state.subledger = SubledgerService(database)
            application.state.periodic = PeriodicService(database)
            application.state.reports = ReportService(database)
            application.state.tax = TaxWorkpaperService(database)
            application.state.b04_close = CloseService(database)
            application.state.integrations = FinancialBridgeService(database)
            application.state.settlements = SettlementService(database)
            application.state.service.settlements = application.state.settlements
            application.state.service.ledgers = application.state.ledgers
            application.state.catalog = CatalogService(database)
            application.state.directory = DirectoryService(database)
            application.state.admin = AdminService(database)
            application.state.admin_identity = AdminIdentityService(database)
            application.state.business = BusinessService(database)
            application.state.revisions = FactRevisionService(
                database, application.state.business, application.state.service
            )
            application.state.dispositions = SourceDispositionService(database)
            application.state.service.revisions = application.state.revisions
            application.state.service.dispositions = application.state.dispositions
            application.state.efficiency = EfficiencyService(database)
            application.state.closing = ClosingService(database, application.state.service)
            application.state.learning = LearningService(database)
            application.state.quality = QualityService(database)
            application.state.cases = CaseService(database, application.state.business)
            application.state.service.learning = application.state.learning
            application.state.service.cases = application.state.cases
            application.state.review = ReviewService(database, application.state.service)
            application.state.review_packages = ReviewPackagesService(
                database, application.state.review
            )
            application.state.bank_import = BankImportService(database)
            application.state.service.bank_import = application.state.bank_import
            application.state.delivery = DeliveryService(database)
            application.state.service.delivery = application.state.delivery
            application.state.attachment_store = create_attachment_store(
                storage_path, settings=storage_settings
            )
            await run_in_threadpool(application.state.attachment_store.verify_private)
            application.state.subledger.storage = application.state.attachment_store
            application.state.uploads = UploadIntentService(
                database, application.state.attachment_store
            )
            application.state.invoice_imports = InvoiceImportService(
                database, application.state.attachment_store, application.state.uploads
            )
            application.state.source_history = SourceHistoryService(
                database, application.state.attachment_store
            )
            application.state.intelligence = IntelligenceService(
                database, application.state.attachment_store, provider=intelligence_provider
            )
            application.state.meter = MeterService(database, pricing=model_pricing)
            application.state.intelligence.meter = application.state.meter
            application.state.operations = OperationsService(
                database, application.state.meter, intelligence=application.state.intelligence
            )
            application.state.telemetry = TelemetryService(database, application.state.operations)
            application.state.service.intelligence = application.state.intelligence
            application.state.reconciliation = ReconciliationService(
                database,
                intelligence=application.state.intelligence,
                bank_import=application.state.bank_import,
            )
            application.state.business.intelligence = application.state.intelligence
            application.state.queue = QueueService(database, application.state.intelligence)
            application.state.business.proposal_queue = application.state.queue.proposal_queue
            application.state.intake = IntakeService(
                database,
                application.state.attachment_store,
                application.state.intelligence.provider,
                meter=application.state.meter,
                business=application.state.business,
                intelligence=application.state.intelligence,
            )
            application.state.import_wizard = ImportWizardService(
                database,
                application.state.attachment_store,
                ledger=application.state.service,
                uploads=application.state.uploads,
            )
            application.state.workspace = WorkspaceService(
                database,
                application.state.service,
                application.state.business,
                application.state.review,
                application.state.cases,
                projection_db=projection_database,
            )
            application.state.monthly = MonthlyService(database, application.state.service)
            application.state.service_plans = ServicePlansService(
                database, application.state.service, application.state.business
            )
            application.state.service_plans.attachments = MaterialAttachmentService(
                application.state.service_plans, application.state.attachment_store
            )
            application.state.monthly_packages = MonthlyPackageService(
                database, application.state.service_plans
            )
            application.state.delivery_followups = DeliveryFollowupService(
                database,
                application.state.monthly_packages,
                application.state.service_plans,
            )
            application.state.onboarding = OnboardingService(
                database,
                application.state.admin,
                application.state.service,
                application.state.ledgers,
                application.state.service_plans,
                application.state.catalog,
            )
            application.state.monthly_workbench = MonthlyWorkbenchService(
                database,
                application.state.service,
                application.state.business,
                application.state.cases,
            )
            application.state.monthly_batch = MonthlyBatchService(
                database,
                application.state.service,
                application.state.ledgers,
                application.state.service_plans,
                application.state.business,
            )
            application.state.batch_operations = BatchOperationsService(
                database, application.state.monthly_batch
            )
            application.state.handoff = HandoffService(database)
            application.state.firm_cockpit = FirmCockpitService(database, application.state.service)
            application.state.firm_tasks = FirmTasksService(database, application.state.service)
            application.state.firm_observability = FirmObservabilityService(
                database, application.state.service
            )
            # Deliberately code-level off: the receive/consumer loop is not
            # complete. Enabling it requires a reviewed code change, not a
            # deployment-time environment variable.
            application.state.ingestion = IngestionService(
                database, application.state.service, enabled=False
            )
            application.state.handover = HandoverService(
                database,
                application.state.service,
                application.state.service_plans,
                application.state.cases,
            )
            application.state.monthly_briefing = BriefingService(
                database, application.state.service
            )
            application.state.legacy_migration = LegacyMigrationService(
                database, application.state.attachment_store
            )
            application.state.legacy_learning = LegacyLearningService(
                database, application.state.legacy_migration
            )
            application.state.legacy_settlements = LegacySettlementService(
                database, application.state.legacy_migration, application.state.service
            )
            application.state.legacy_migration.settlements = application.state.legacy_settlements
            application.state.service.legacy_settlements = application.state.legacy_settlements
            application.state.service.legacy_learning = application.state.legacy_learning
            application.state.learning.legacy_settlements = application.state.legacy_settlements
            application.state.learning.legacy_learning = application.state.legacy_learning
            application.state.agent_workspace = AgentWorkspaceService(
                database,
                application.state.service,
                application.state.business,
                application.state.workspace,
                application.state.legacy_migration,
            )
            application.state.assistant_provider = assistant_provider or AssistantProvider(
                application.state.intelligence.provider
            )
            application.state.assistant = AssistantService(
                database,
                application.state.service,
                application.state.business,
                agent_workspace=application.state.agent_workspace,
                provider=application.state.assistant_provider,
                intelligence=application.state.intelligence,
            )
            application.state.assistant_actions = AssistantActionService(
                database,
                application.state.assistant,
                application.state.queue,
                application.state.service,
            )
            application.state.revisions.after_replace = (
                application.state.queue.proposal_queue.enqueue_in_transaction
            )
            application.state.tokens = {}
            application.state.token_users = {}
            application.state.authenticator = authenticator or (
                SupabaseAuthenticator(settings) if settings.mode == "supabase" else None
            )
            if settings.mode == "local":
                seed_enabled = (
                    seed if seed is not None else os.getenv("ACCOUNTING_SEED", "1") != "0"
                )
                bootstrap(application.state.service, seed_examples=seed_enabled)
                bootstrap_business_demo(database, seed_enabled)
            embedded = (
                queue_worker_enabled
                if queue_worker_enabled is not None
                else os.getenv(
                    "ACCOUNTING_QUEUE_EMBEDDED_WORKER", "1" if settings.mode == "local" else "0"
                )
                == "1"
            )
            worker = None
            if embedded:
                if database.is_postgres or settings.mode != "local" or production:
                    raise RuntimeError("嵌入后台处理仅供本地SQLite；真实企业须独立worker账号与进程")
                lease_seconds = int(os.getenv("ACCOUNTING_QUEUE_LEASE_SECONDS", "180"))
                if lease_seconds < 150:
                    raise RuntimeError("部署队列租约至少150秒")
                worker = QueueWorker(
                    application.state.queue,
                    concurrency=int(os.getenv("ACCOUNTING_QUEUE_CONCURRENCY", "2")),
                    lease_seconds=lease_seconds,
                    poll_seconds=float(os.getenv("ACCOUNTING_QUEUE_POLL_SECONDS", "1")),
                )
                workers.append(worker)
                worker.start()
            application.state.queue_worker = worker
            migration_worker = (
                MigrationWorker(application.state.legacy_migration) if embedded else None
            )
            if migration_worker:
                workers.append(migration_worker)
                migration_worker.start()
            application.state.migration_worker = migration_worker
            assistant_worker = (
                AssistantWorker(
                    application.state.assistant,
                    application.state.assistant_provider,
                    AssistantMeter(database, application.state.meter),
                )
                if embedded
                else None
            )
            if assistant_worker:
                workers.append(assistant_worker)
                assistant_worker.start()
            application.state.assistant_worker = assistant_worker
            batch_worker = BatchWorker(application.state.batch_operations) if embedded else None
            if batch_worker:
                workers.append(batch_worker)
                batch_worker.start()
            application.state.batch_worker = batch_worker
            yield
        finally:
            drained = _drain_embedded_workers(application, workers)
            try:
                getattr(application.state, "tokens", {}).clear()
                getattr(application.state, "token_users", {}).clear()
                provider = getattr(application.state, "authenticator", None)
                if provider and authenticator is None:
                    provider.close()
            finally:
                if drained:
                    try:
                        database.close()
                    finally:
                        if projection_database is not None:
                            projection_database.close()

    app = FastAPI(
        title="衡账 · 会计 Agent 工作空间",
        version="0.19.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(AttachmentBodyLimitMiddleware)
    app.add_middleware(CaseAttachmentBodyLimitMiddleware)
    app.add_middleware(BridgeBodyLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Firm-Id"],
        expose_headers=["X-Request-Id"],
        allow_credentials=False,
    )

    @app.middleware("http")
    async def authenticate_request(request: Request, call_next):
        if settings.mode == "local" and (
            not request.client or not is_loopback(request.client.host)
        ):
            return JSONResponse({"detail": "本地验证服务仅允许回环地址访问"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin not in origins:
            return JSONResponse({"detail": "不允许该网页来源访问会计服务"}, status_code=403)
        public = {"/api/auth/config", "/api/session", "/api/health"}
        public_webhook = (
            request.url.path == "/api/integrations/webhook"
            or request.url.path.startswith("/api/integrations/webhook/")
        )
        if (
            request.method != "OPTIONS"
            and request.url.path.startswith("/api")
            and request.url.path not in public
            and not public_webhook
        ):
            authorization = request.headers.get("authorization", "")
            token = authorization[7:] if authorization.startswith("Bearer ") else ""
            if settings.mode == "local":
                if not token or request.app.state.tokens.get(token, 0) <= time.monotonic():
                    return JSONResponse(
                        {"detail": "本地会话未登录或已过期，请重新登录"}, status_code=401
                    )
                request.state.identity = request.app.state.token_users.get(
                    token, Identity(LOCAL_ACTOR.user_id, "local@example.invalid", USER_NAME)
                )
            else:
                try:
                    request.state.identity = await run_in_threadpool(
                        request.app.state.authenticator.authenticate, token
                    )
                except AuthError as error:
                    return JSONResponse({"detail": error.detail}, status_code=error.status)
                if (
                    requires_admin_mfa(request.url.path, request.method)
                    and request.state.identity.aal != "aal2"
                ):
                    return JSONResponse(
                        {
                            "detail": "管理操作需要二次验证，请进入管理后台完成验证",
                            "code": "admin_mfa_required",
                        },
                        status_code=403,
                        headers={"Cache-Control": "no-store"},
                    )
        response = await call_next(request)
        private = "private" in {
            part.strip().lower() for part in response.headers.get("Cache-Control", "").split(",")
        }
        response.headers["Cache-Control"] = "private, no-store" if private else "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.middleware("http")
    async def trace_request(request: Request, call_next):
        trace_id = request.headers.get("x-request-id", "").strip()
        if not trace_id or len(trace_id) > 120:
            trace_id = uuid4().hex
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = trace_id
        return response

    @app.exception_handler(AccountingError)
    @app.exception_handler(AuthError)
    async def handle_domain_error(request, error):
        return JSONResponse({"detail": error.detail}, status_code=error.status)

    @app.exception_handler(ProviderError)
    async def handle_provider_error(request, error):
        # Supplier response bodies, raw evidence and credentials are never reflected by HTTP.
        status = 422 if error.status == 422 else 503
        return JSONResponse(
            {"detail": "票据与模型处理失败，请人工核对或稍后重试"}, status_code=status
        )

    @app.exception_handler(sqlite3.IntegrityError)
    @app.exception_handler(psycopg.IntegrityError)
    async def handle_integrity_error(request, error):
        return JSONResponse(
            {"detail": "数据关联或唯一性校验失败，请核对企业、账套及成员信息"}, status_code=409
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request, error):
        labels = {
            "amount": "金额",
            "debit": "借方金额",
            "credit": "贷方金额",
            "date": "日期",
            "period": "会计期间",
            "expected_version": "凭证版本",
            "name": "名称",
            "rows": "导入行",
            "lines": "分录",
            "evidence_ref": "证据引用",
            "email": "邮箱",
            "role": "权限角色",
            "company_id": "客户企业",
        }
        fields = list(
            dict.fromkeys(labels.get(str(x["loc"][-1]), str(x["loc"][-1])) for x in error.errors())
        )
        return JSONResponse(
            {
                "detail": "请求参数校验失败："
                + "、".join(fields)
                + "。请核对格式；金额须为非负、最多两位小数的字符串。"
            },
            status_code=422,
        )

    def service(request: Request):
        return request.app.state.service

    @app.exception_handler(DatabaseCapacityError)
    async def handle_database_capacity(request, error):
        return JSONResponse(
            {"detail": "账务数据服务繁忙，请稍后重试", "code": "database_busy"},
            status_code=503,
            headers={"Retry-After": "2"},
        )

    @app.exception_handler(psycopg.OperationalError)
    @app.exception_handler(sqlite3.OperationalError)
    async def handle_database_unavailable(request, error):
        log_database_unavailable(request, error)
        return JSONResponse(
            {"detail": "账务数据服务暂时不可用，请稍后重试；未完成的操作会整体回滚"},
            status_code=503,
        )

    @app.exception_handler(psycopg.errors.InsufficientPrivilege)
    async def handle_database_denial(request, error):
        return JSONResponse(
            {"detail": "数据库拒绝该范围的访问，请核对账号授权与部署配置"}, status_code=403
        )

    def directory(request: Request):
        return request.app.state.directory

    def handover(request: Request):
        return request.app.state.handover

    def identity(request: Request):
        return request.state.identity

    def actor(
        current=Depends(identity),
        firm_id: str | None = Header(default=None, alias="X-Firm-Id"),
        dirs=Depends(directory),
    ):
        if settings.mode == "local" and firm_id is None:
            return Actor(current.user_id, LOCAL_ACTOR.firm_id)
        return dirs.resolve_actor(current.user_id, firm_id)

    def book_actor(book_id: str, current=Depends(actor), svc=Depends(service)):
        svc.require_access(current, book_id)
        return current

    @app.get("/api/auth/config")
    def auth_config():
        config = settings.public_config()
        config["allow_firm_signup"] = firm_signup_enabled
        config["admin_requires_mfa"] = settings.mode == "supabase"
        if settings.mode == "local":
            config["personas"] = [
                {"id": row["id"], "label": row["label"]} for row in PERSONAS.values()
            ]
        return config

    @app.get("/api/health")
    def health(request: Request):
        return {
            "status": "ok",
            "storage": request.app.state.service.db.dialect,
            "auth": settings.mode,
        }

    @app.get("/api/session")
    async def session(request: Request, persona: str = "accountant"):
        if settings.mode != "local":
            raise AccountingError("真实登录环境不提供演示会话", 404)
        if persona not in PERSONAS:
            raise AccountingError("未知本地验证身份", 422)
        now = time.monotonic()
        request.app.state.tokens = {
            key: expiry for key, expiry in request.app.state.tokens.items() if expiry > now
        }
        request.app.state.token_users = {
            key: identity
            for key, identity in request.app.state.token_users.items()
            if key in request.app.state.tokens
        }
        token = secrets.token_urlsafe(32)
        request.app.state.tokens[token] = now + 7200
        record = PERSONAS[persona]
        request.app.state.token_users[token] = Identity(
            record["user_id"], persona + "@demo.example.invalid", record["name"]
        )
        return {
            "token": token,
            "mode": "local",
            "firm_name": FIRM_NAME,
            "user_name": record["name"],
            "persona": persona,
        }

    @app.get("/api/me")
    def me(
        current=Depends(identity),
        firm_id: str | None = Header(default=None, alias="X-Firm-Id"),
        dirs=Depends(directory),
    ):
        if settings.mode == "supabase":
            dirs.register_identity(current)
        return dirs.me(current.user_id, firm_id, settings.mode)

    @app.get("/api/workspace/bootstrap")
    def workspace_bootstrap(
        request: Request,
        current=Depends(identity),
        firm_id: str | None = Header(default=None, alias="X-Firm-Id"),
        dirs=Depends(directory),
    ):
        from .workspace.bootstrap import home_bootstrap

        return home_bootstrap(dirs, request.app.state.workspace, current, firm_id, settings.mode)

    @app.post("/api/firms", status_code=201)
    def create_firm(payload: FirmCreate, current=Depends(identity), dirs=Depends(directory)):
        if not firm_signup_enabled:
            raise AccountingError("当前环境仅允许管理员开通事务所，请联系管理人员", 403)
        if settings.mode == "supabase":
            dirs.register_identity(current)
        return dirs.create_firm(current.user_id, payload)

    @app.get("/api/workbench")
    def workbench(
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1000000),
        filter: str = Query("all", pattern="^(all|pending|exception|approved)$"),
        search: str = Query("", max_length=120),
        focus_book_id: str | None = Query(None, max_length=100),
        current=Depends(actor),
        svc=Depends(service),
    ):
        return svc.workbench(current, limit, offset, filter, search, focus_book_id)

    @app.get("/api/companies/{company_id}/books")
    def book_choices(
        company_id: str,
        period: str | None = Query(None, pattern="^20[0-9]{2}-(0[1-9]|1[0-2])$"),
        limit: int = Query(100, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1000000),
        current=Depends(actor),
        svc=Depends(service),
    ):
        return svc.book_choices(current, company_id, period, limit, offset)

    @app.post("/api/books", status_code=201)
    def create_book(payload: BookCreate, current=Depends(actor), svc=Depends(service)):
        return svc.create_book(current, payload)

    @app.get("/api/books/{book_id}")
    def get_book(
        book_id: str,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1000000),
        source_offset: int = Query(0, ge=0, le=1000000),
        filter: str = Query("all", pattern="^(all|pending|exception|approved)$"),
        summaries: bool = False,
        focus_draft_id: str | None = Query(None, max_length=100),
        current=Depends(book_actor),
        svc=Depends(service),
    ):
        return svc.get_book(
            current, book_id, limit, offset, source_offset, filter, summaries, focus_draft_id
        )

    @app.get("/api/books/{book_id}/drafts/{draft_id}")
    def get_draft(book_id: str, draft_id: str, current=Depends(book_actor), svc=Depends(service)):
        return svc.get_draft(current, book_id, draft_id)

    @app.post("/api/books/{book_id}/imports")
    def import_sources(
        book_id: str, payload: ImportRequest, current=Depends(book_actor), svc=Depends(service)
    ):
        return svc.import_sources(current, book_id, payload)

    @app.post("/api/books/{book_id}/sources/{source_id}/evidence")
    def add_evidence(
        book_id: str,
        source_id: str,
        payload: EvidenceRequest,
        current=Depends(book_actor),
        svc=Depends(service),
    ):
        return svc.add_evidence(current, book_id, source_id, payload)

    @app.post("/api/books/{book_id}/suggestions")
    def suggest(book_id: str, current=Depends(book_actor), svc=Depends(service)):
        return AccountingSuggestionTool(svc).execute(current, book_id)

    @app.patch("/api/books/{book_id}/drafts/{draft_id}")
    def update_draft(
        book_id: str,
        draft_id: str,
        payload: DraftUpdate,
        current=Depends(book_actor),
        svc=Depends(service),
    ):
        return svc.update_draft(current, book_id, draft_id, payload)

    @app.post("/api/books/{book_id}/drafts/{draft_id}/approve")
    def approve(
        book_id: str,
        draft_id: str,
        payload: ApprovalRequest,
        current=Depends(book_actor),
        svc=Depends(service),
    ):
        return svc.approve(current, book_id, draft_id, payload)

    @app.post("/api/books/{book_id}/exports")
    def export(
        book_id: str, payload: ExportRequest, current=Depends(book_actor), svc=Depends(service)
    ):
        return svc.export(current, book_id, payload)

    @app.get("/api/books/{book_id}/exports")
    def list_exports(book_id: str, current=Depends(book_actor), svc=Depends(service)):
        return svc.list_exports(current, book_id)

    @app.get("/api/books/{book_id}/exports/{export_id}")
    def get_export(book_id: str, export_id: str, current=Depends(book_actor), svc=Depends(service)):
        return svc.get_export(current, book_id, export_id)

    @app.post("/api/books/{book_id}/periods/{period}/close")
    def close_period(
        book_id: str,
        period: str,
        payload: CloseRequest | None = None,
        current=Depends(book_actor),
        svc=Depends(service),
    ):
        return svc.close_period(
            current,
            book_id,
            period,
            payload.snapshot_id if payload else None,
            payload.reason if payload else None,
        )

    @app.get("/api/companies")
    def companies(current=Depends(actor), dirs=Depends(directory)):
        return dirs.list_companies(current)

    @app.post("/api/companies", status_code=201)
    def create_company(payload: CompanyCreate, current=Depends(actor), dirs=Depends(directory)):
        return dirs.create_company(current, payload)

    @app.get("/api/firm/members")
    def firm_members(current=Depends(actor), dirs=Depends(directory)):
        return dirs.list_firm_members(current)

    @app.put("/api/firm/members")
    def grant_firm_member(
        payload: FirmMemberGrant, current=Depends(actor), dirs=Depends(directory)
    ):
        return dirs.grant_firm_member(current, payload)

    @app.delete("/api/firm/members/{user_id}")
    def revoke_firm_member(user_id: str, current=Depends(actor), dirs=Depends(directory)):
        return dirs.revoke_firm_member(current, user_id)

    @app.get("/api/firm/members/{user_id}/handover")
    def handover_preview(
        user_id: str,
        to: str = Query(min_length=1, max_length=100),
        current=Depends(actor),
        service=Depends(handover),
    ):
        return service.preview(current, user_id, to)

    @app.post("/api/firm/members/{user_id}/handover")
    def handover_execute(
        user_id: str,
        payload: HandoverRequest,
        current=Depends(actor),
        service=Depends(handover),
    ):
        return service.execute(current, user_id, payload)

    @app.get("/api/books/{book_id}/members")
    def book_members(book_id: str, current=Depends(actor), dirs=Depends(directory)):
        return dirs.list_book_members(current, book_id)

    @app.put("/api/books/{book_id}/members/{user_id}")
    def grant_book_member(
        book_id: str,
        user_id: str,
        payload: BookMemberGrant,
        current=Depends(actor),
        dirs=Depends(directory),
    ):
        return dirs.grant_book_member(current, book_id, user_id, payload)

    @app.delete("/api/books/{book_id}/members/{user_id}")
    def revoke_book_member(
        book_id: str, user_id: str, current=Depends(actor), dirs=Depends(directory)
    ):
        return dirs.revoke_book_member(current, book_id, user_id)

    @app.get("/api/companies/{company_id}/members")
    def company_members(company_id: str, current=Depends(actor), dirs=Depends(directory)):
        return dirs.list_company_members(current, company_id)

    @app.put("/api/companies/{company_id}/members")
    def grant_company_member(
        company_id: str,
        payload: CompanyMemberGrant,
        current=Depends(actor),
        dirs=Depends(directory),
    ):
        return dirs.grant_company_member(current, company_id, payload)

    @app.delete("/api/companies/{company_id}/members/{user_id}")
    def revoke_company_member(
        company_id: str, user_id: str, current=Depends(actor), dirs=Depends(directory)
    ):
        return dirs.revoke_company_member(current, company_id, user_id)

    @app.get("/api/companies/{company_id}/departments")
    def departments(company_id: str, current=Depends(actor), dirs=Depends(directory)):
        return dirs.list_departments(current, company_id)

    @app.post("/api/companies/{company_id}/departments", status_code=201)
    def create_department(
        company_id: str, payload: DepartmentCreate, current=Depends(actor), dirs=Depends(directory)
    ):
        return dirs.create_department(current, company_id, payload)

    from .execution.router import build_execution_router

    from .accounting.access_policy_router import build_access_policy_router

    app.include_router(build_access_policy_router(actor))
    app.include_router(build_execution_router(actor))
    app.include_router(build_admin_router(actor))
    app.include_router(build_admin_identity_router(actor, identity))
    app.include_router(build_business_router(actor))
    app.include_router(build_intake_router(actor))
    app.include_router(build_import_wizard_router(actor))
    app.include_router(build_workspace_router(actor))
    app.include_router(build_monthly_router(actor))
    app.include_router(build_monthly_workbench_router(actor))
    app.include_router(build_service_plans_router(actor))
    app.include_router(build_material_attachments_router(actor))
    app.include_router(build_uploads_router(actor))
    app.include_router(build_invoice_imports_router(actor))
    app.include_router(build_agent_workspace_router(actor))
    app.include_router(build_assistant_router(actor))
    app.include_router(build_legacy_migration_router(actor))
    app.include_router(build_intelligence_router(actor))
    app.include_router(build_ai_policy_router(actor))
    app.include_router(build_queue_router(actor))
    app.include_router(build_batch_ops_router(actor))
    app.include_router(build_handoff_router(actor))
    app.include_router(build_cockpit_router(actor))
    app.include_router(build_firm_tasks_router(actor))
    app.include_router(build_observability_router(actor))
    app.include_router(build_ingestion_router(actor))
    app.include_router(build_onboarding_router(actor))
    app.include_router(build_operations_router(actor))
    app.include_router(build_catalog_router(actor))
    app.include_router(build_review_router(actor))
    app.include_router(build_review_policy_router(actor))
    from .accounting.legacy_review_router import build_legacy_review_router

    app.include_router(build_legacy_review_router(actor))
    app.include_router(build_cases_router(actor))
    app.include_router(build_learning_router(actor))
    app.include_router(build_quality_router(actor))
    app.include_router(build_bank_import_router(actor))
    app.include_router(build_reconciliation_router(actor))
    app.include_router(build_delivery_router(actor))
    app.include_router(build_delivery_followup_router(actor))
    app.include_router(build_ledgers_router(actor))
    app.include_router(build_opening_router(actor))
    app.include_router(build_posting_router(actor))
    app.include_router(build_subledger_router(actor))
    app.include_router(build_periodic_router(actor))
    app.include_router(build_report_router(actor))
    app.include_router(build_tax_router(actor))
    app.include_router(build_close_router(actor))
    app.include_router(build_integrations_router(actor))
    app.include_router(build_settlement_router(actor))
    app.include_router(build_revisions_router(actor))
    app.include_router(build_dispositions_router(actor))
    app.include_router(build_efficiency_router(actor))
    app.include_router(build_telemetry_router(actor))
    app.include_router(build_closing_router(actor))
    app.include_router(build_source_history_router(actor))
    app.add_middleware(DeliveryBodyLimitMiddleware)
    app.add_middleware(ImportWizardBodyLimitMiddleware)
    app.add_middleware(MigrationBodyLimitMiddleware)
    app.add_middleware(IngestionBodyLimitMiddleware)
    return app


app = create_app()
