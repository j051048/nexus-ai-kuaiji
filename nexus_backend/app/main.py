"""Local demo or PostgreSQL accounting with verified Supabase identity."""
import ipaddress
import os
import secrets
import sqlite3
import time
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
from .accounting.database import Database
from .accounting.directory import DirectoryService
from .accounting.models import (
    ApprovalRequest, BookCreate, DraftUpdate, EvidenceRequest, ExportRequest, ImportRequest,
    FirmCreate, CompanyCreate, FirmMemberGrant, BookMemberGrant, CompanyMemberGrant, DepartmentCreate,
)
from .accounting.seed import FIRM_NAME, LOCAL_ACTOR, USER_NAME, bootstrap
from .accounting.service import AccountingError, AccountingService, Actor
from .accounting.catalog import CatalogService
from .accounting.catalog_router import build_catalog_router
from .accounting.review import ReviewService
from .accounting.packages import ReviewPackagesService
from .accounting.review_router import build_review_router
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
from .delivery.router import build_delivery_router
from .delivery.middleware import DeliveryBodyLimitMiddleware
from .tools.accounting_tools import AccountingSuggestionTool
from .business.api import build_business_router
from .business.demo import PERSONAS, bootstrap_business_demo
from .business.service import BusinessService
from .business.storage import AttachmentStore, AttachmentBodyLimitMiddleware
from .intelligence.provider import ProviderError
from .intelligence.router import build_intelligence_router
from .intelligence.service import IntelligenceService
from .jobs.service import QueueService
from .jobs.worker import QueueWorker
from .jobs.router import build_queue_router
from .operations.meter import MeterService
from .operations.service import OperationsService
from .operations.router import build_operations_router
from .ledgers.service import LedgersService
from .ledgers.router import build_ledgers_router
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
from .revisions.history import SourceHistoryService
from .revisions.history_router import build_source_history_router
from .intake.service import IntakeService
from .intake.router import build_intake_router
from .import_wizard.service import ImportWizardService
from .import_wizard.router import build_import_wizard_router, ImportWizardBodyLimitMiddleware
from .workspace.service import WorkspaceService
from .workspace.router import build_workspace_router


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def validate_environment(settings, path):
    environment = os.getenv("ACCOUNTING_ENV", "local").lower()
    if environment not in {"local", "development", "test", "production"}:
        raise RuntimeError("ACCOUNTING_ENV 必须为 local、development、test 或 production")
    production = environment == "production" or any(os.getenv(key, "").lower() in {"production", "prod"} for key in ("APP_ENV", "ENV", "ENVIRONMENT"))
    postgres = str(path).startswith(("postgres://", "postgresql://"))
    if settings.mode == "supabase" and not postgres:
        raise RuntimeError("真实登录模式必须使用独立 PostgreSQL，禁止复用本地示例数据库")
    legacy_nonlocal = any(os.getenv(key, "").lower() not in {"", "local", "development", "test"} for key in ("APP_ENV", "ENV", "ENVIRONMENT"))
    if settings.mode == "local" and (postgres or production or legacy_nonlocal):
        raise RuntimeError("禁止本地演示身份进入非本地环境；真实企业请配置 Supabase 登录和 PostgreSQL")
    if production:
        if settings.mode != "supabase" or not postgres:
            raise RuntimeError("生产模式必须使用 PostgreSQL 与 Supabase 真实登录")
        if parse_qs(urlsplit(str(path)).query).get("sslmode", [""])[0] not in {"require", "verify-ca", "verify-full"}:
            raise RuntimeError("生产 PostgreSQL 连接必须显式配置 sslmode=require 或证书校验")
        if not os.getenv("ACCOUNTING_ALLOWED_HOSTS") or not os.getenv("ACCOUNTING_ALLOWED_ORIGINS"):
            raise RuntimeError("生产模式必须显式配置域名与网页来源")
    return production


def create_app(db_path: str | Path | None = None, seed: bool | None = None, auth_settings=None, authenticator=None, attachment_root=None, intelligence_provider=None, queue_worker_enabled=None, model_pricing=None):
    settings = auth_settings or AuthSettings.from_env()
    path = db_path or os.getenv("ACCOUNTING_DATABASE_URL") or os.getenv("ACCOUNTING_DB_PATH") or Path(__file__).resolve().parents[1] / "data" / "accounting.db"
    production = validate_environment(settings, path)
    configured_attachment_root = attachment_root or os.getenv("ACCOUNTING_ATTACHMENT_ROOT")
    storage_path = configured_attachment_root or Path(__file__).resolve().parents[1] / "private-attachments"
    hosts = [x.strip() for x in os.getenv("ACCOUNTING_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],::1").split(",") if x.strip()]
    origins = [x.strip().rstrip("/") for x in os.getenv("ACCOUNTING_ALLOWED_ORIGINS", "http://127.0.0.1:8080,http://localhost:8080,http://127.0.0.1:18080,http://localhost:18080").split(",") if x.strip()]
    if not hosts or "*" in hosts or not origins or "*" in origins:
        raise RuntimeError("域名和网页来源须使用明确白名单，不能配置通配符")
    for origin in origins:
        try:
            parsed = urlsplit(origin)
            valid = parsed.scheme in {"http", "https"} and parsed.hostname and parsed.path == "" and not parsed.query and not parsed.fragment and not parsed.username
            if production:
                valid = valid and parsed.scheme == "https"
        except ValueError:
            valid = False
        if not valid:
            raise RuntimeError("ACCOUNTING_ALLOWED_ORIGINS 必须为完整网页来源，生产须使用 HTTPS")
    if production and not configured_attachment_root:
        raise RuntimeError("生产模式须显式配置 ACCOUNTING_ATTACHMENT_ROOT 的持久私有存储目录")

    @asynccontextmanager
    async def lifespan(application):
        database = Database(path)
        if database.is_postgres:
            with closing(database.connect()) as conn:
                if conn.execute("SELECT pg_has_role(session_user,'accounting_worker','MEMBER')").fetchone()[0]:
                    raise RuntimeError("API与后台处理必须使用独立账号；API登录不得授予accounting_worker")
        application.state.service = AccountingService(database)
        application.state.ledgers = LedgersService(database)
        application.state.integrations = FinancialBridgeService(database)
        application.state.settlements = SettlementService(database)
        application.state.service.settlements = application.state.settlements
        application.state.service.ledgers = application.state.ledgers
        application.state.catalog = CatalogService(database)
        application.state.directory = DirectoryService(database)
        application.state.business = BusinessService(database)
        application.state.revisions = FactRevisionService(database, application.state.business, application.state.service)
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
        application.state.review_packages = ReviewPackagesService(database, application.state.review)
        application.state.bank_import = BankImportService(database)
        application.state.service.bank_import = application.state.bank_import
        application.state.delivery = DeliveryService(database)
        application.state.service.delivery = application.state.delivery
        application.state.attachment_store = AttachmentStore(storage_path)
        application.state.source_history = SourceHistoryService(database, application.state.attachment_store)
        application.state.intelligence = IntelligenceService(database, application.state.attachment_store, provider=intelligence_provider)
        application.state.meter = MeterService(database, pricing=model_pricing)
        application.state.intelligence.meter = application.state.meter
        application.state.operations = OperationsService(database, application.state.meter, intelligence=application.state.intelligence)
        application.state.telemetry = TelemetryService(database, application.state.operations)
        application.state.service.intelligence = application.state.intelligence
        application.state.reconciliation = ReconciliationService(database, intelligence=application.state.intelligence, bank_import=application.state.bank_import)
        application.state.business.intelligence = application.state.intelligence
        application.state.queue = QueueService(database, application.state.intelligence)
        application.state.business.proposal_queue = application.state.queue.proposal_queue
        application.state.intake = IntakeService(database, application.state.attachment_store, application.state.intelligence.provider,
            meter=application.state.meter, business=application.state.business, intelligence=application.state.intelligence)
        application.state.import_wizard = ImportWizardService(database, application.state.attachment_store, ledger=application.state.service)
        application.state.workspace = WorkspaceService(database, application.state.service, application.state.business, application.state.review, application.state.cases)
        application.state.revisions.after_replace = application.state.queue.proposal_queue.enqueue_in_transaction
        application.state.tokens = {}
        application.state.token_users = {}
        application.state.authenticator = authenticator or (SupabaseAuthenticator(settings) if settings.mode == "supabase" else None)
        if settings.mode == "local":
            seed_enabled = seed if seed is not None else os.getenv("ACCOUNTING_SEED", "1") != "0"
            bootstrap(application.state.service, seed_examples=seed_enabled)
            bootstrap_business_demo(database, seed_enabled)
        embedded = queue_worker_enabled if queue_worker_enabled is not None else os.getenv("ACCOUNTING_QUEUE_EMBEDDED_WORKER", "1" if settings.mode == "local" else "0") == "1"
        worker = None
        if embedded:
            if database.is_postgres or settings.mode != "local" or production:
                raise RuntimeError("嵌入后台处理仅供本地SQLite；真实企业须独立worker账号与进程")
            lease_seconds = int(os.getenv("ACCOUNTING_QUEUE_LEASE_SECONDS", "180"))
            if lease_seconds < 150:
                raise RuntimeError("部署队列租约至少150秒")
            worker = QueueWorker(application.state.queue, concurrency=int(os.getenv("ACCOUNTING_QUEUE_CONCURRENCY", "2")), lease_seconds=lease_seconds, poll_seconds=float(os.getenv("ACCOUNTING_QUEUE_POLL_SECONDS", "1")))
            worker.start()
        application.state.queue_worker = worker
        try:
            yield
        finally:
            if worker:
                worker.stop()
            application.state.tokens.clear()
            application.state.token_users.clear()
            if application.state.authenticator and authenticator is None:
                application.state.authenticator.close()

    app = FastAPI(title="衡账 · AI 会计工作台", version="0.12.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(AttachmentBodyLimitMiddleware)
    app.add_middleware(CaseAttachmentBodyLimitMiddleware)
    app.add_middleware(BridgeBodyLimitMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type", "X-Firm-Id"], allow_credentials=False)

    @app.middleware("http")
    async def authenticate_request(request: Request, call_next):
        if settings.mode == "local" and (not request.client or not is_loopback(request.client.host)):
            return JSONResponse({"detail": "本地验证服务仅允许回环地址访问"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin not in origins:
            return JSONResponse({"detail": "不允许该网页来源访问会计服务"}, status_code=403)
        public = {"/api/auth/config", "/api/session", "/api/health"}
        if request.method != "OPTIONS" and request.url.path.startswith("/api") and request.url.path not in public:
            authorization = request.headers.get("authorization", "")
            token = authorization[7:] if authorization.startswith("Bearer ") else ""
            if settings.mode == "local":
                if not token or request.app.state.tokens.get(token, 0) <= time.monotonic():
                    return JSONResponse({"detail": "本地会话未登录或已过期，请重新登录"}, status_code=401)
                request.state.identity = request.app.state.token_users.get(token, Identity(LOCAL_ACTOR.user_id, "local@example.invalid", USER_NAME))
            else:
                try:
                    request.state.identity = await run_in_threadpool(request.app.state.authenticator.authenticate, token)
                except AuthError as error:
                    return JSONResponse({"detail": error.detail}, status_code=error.status)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(AccountingError)
    @app.exception_handler(AuthError)
    async def handle_domain_error(request, error):
        return JSONResponse({"detail": error.detail}, status_code=error.status)

    @app.exception_handler(ProviderError)
    async def handle_provider_error(request, error):
        # Supplier response bodies, raw evidence and credentials are never reflected by HTTP.
        status = 422 if error.status == 422 else 503
        return JSONResponse({"detail": "票据与模型处理失败，请人工核对或稍后重试"}, status_code=status)

    @app.exception_handler(sqlite3.IntegrityError)
    @app.exception_handler(psycopg.IntegrityError)
    async def handle_integrity_error(request, error):
        return JSONResponse({"detail": "数据关联或唯一性校验失败，请核对企业、账套及成员信息"}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request, error):
        labels = {"amount": "金额", "debit": "借方金额", "credit": "贷方金额", "date": "日期", "period": "会计期间", "expected_version": "凭证版本", "name": "名称", "rows": "导入行", "lines": "分录", "evidence_ref": "证据引用", "email": "邮箱", "role": "权限角色", "company_id": "客户企业"}
        fields = list(dict.fromkeys(labels.get(str(x["loc"][-1]), str(x["loc"][-1])) for x in error.errors()))
        return JSONResponse({"detail": "请求参数校验失败：" + "、".join(fields) + "。请核对格式；金额须为非负、最多两位小数的字符串。"}, status_code=422)

    def service(request: Request):
        return request.app.state.service

    @app.exception_handler(psycopg.OperationalError)
    @app.exception_handler(sqlite3.OperationalError)
    async def handle_database_unavailable(request, error):
        return JSONResponse({"detail": "账务数据服务暂时不可用，请稍后重试；未完成的操作会整体回滚"}, status_code=503)

    @app.exception_handler(psycopg.errors.InsufficientPrivilege)
    async def handle_database_denial(request, error):
        return JSONResponse({"detail": "数据库拒绝该范围的访问，请核对账号授权与部署配置"}, status_code=403)

    def directory(request: Request):
        return request.app.state.directory

    def identity(request: Request):
        return request.state.identity

    def actor(current=Depends(identity), firm_id: str | None = Header(default=None, alias="X-Firm-Id"), dirs=Depends(directory)):
        if settings.mode == "local" and firm_id is None:
            return Actor(current.user_id, LOCAL_ACTOR.firm_id)
        profile = dirs.me(current.user_id, firm_id, settings.mode)
        if not profile["current_firm_id"]:
            raise AccountingError("尚未获得事务所或客户企业授权，请创建事务所或联系管理员", 403)
        return Actor(current.user_id, profile["current_firm_id"])

    def book_actor(book_id: str, current=Depends(actor), svc=Depends(service)):
        svc.require_access(current, book_id)
        return current

    @app.get("/api/auth/config")
    def auth_config():
        config = settings.public_config()
        if settings.mode == "local":
            config["personas"] = [{"id": row["id"], "label": row["label"]} for row in PERSONAS.values()]
        return config

    @app.get("/api/health")
    def health(request: Request):
        return {"status": "ok", "storage": request.app.state.service.db.dialect, "auth": settings.mode}

    @app.get("/api/session")
    async def session(request: Request, persona: str = "accountant"):
        if settings.mode != "local":
            raise AccountingError("真实登录环境不提供演示会话", 404)
        if persona not in PERSONAS:
            raise AccountingError("未知本地验证身份", 422)
        now = time.monotonic()
        request.app.state.tokens = {key: expiry for key, expiry in request.app.state.tokens.items() if expiry > now}
        request.app.state.token_users = {key: identity for key, identity in request.app.state.token_users.items() if key in request.app.state.tokens}
        token = secrets.token_urlsafe(32)
        request.app.state.tokens[token] = now + 7200
        record = PERSONAS[persona]
        request.app.state.token_users[token] = Identity(record["user_id"], persona + "@demo.example.invalid", record["name"])
        return {"token": token, "mode": "local", "firm_name": FIRM_NAME, "user_name": record["name"], "persona": persona}

    @app.get("/api/me")
    def me(current=Depends(identity), firm_id: str | None = Header(default=None, alias="X-Firm-Id"), dirs=Depends(directory)):
        if settings.mode == "supabase":
            dirs.register_identity(current)
        return dirs.me(current.user_id, firm_id, settings.mode)

    @app.post("/api/firms", status_code=201)
    def create_firm(payload: FirmCreate, current=Depends(identity), dirs=Depends(directory)):
        if settings.mode == "supabase":
            dirs.register_identity(current)
        return dirs.create_firm(current.user_id, payload)

    @app.get("/api/workbench")
    def workbench(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=1000000), filter: str = Query('all', pattern='^(all|pending|exception|approved)$'), search: str = Query('', max_length=120), focus_book_id: str | None = Query(None, max_length=100), current=Depends(actor), svc=Depends(service)):
        return svc.workbench(current, limit, offset, filter, search, focus_book_id)

    @app.get("/api/companies/{company_id}/books")
    def book_choices(company_id: str, period: str | None = Query(None, pattern='^20[0-9]{2}-(0[1-9]|1[0-2])$'), limit: int = Query(100,ge=1,le=100), offset: int = Query(0,ge=0,le=1000000),current=Depends(actor),svc=Depends(service)):
        return svc.book_choices(current,company_id,period,limit,offset)

    @app.post("/api/books", status_code=201)
    def create_book(payload: BookCreate, current=Depends(actor), svc=Depends(service)):
        return svc.create_book(current, payload)

    @app.get("/api/books/{book_id}")
    def get_book(book_id: str, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=1000000), source_offset: int = Query(0, ge=0, le=1000000), filter: str = Query('all', pattern='^(all|pending|exception|approved)$'), summaries: bool = False, focus_draft_id: str | None = Query(None, max_length=100), current=Depends(book_actor), svc=Depends(service)):
        return svc.get_book(current, book_id, limit, offset, source_offset, filter, summaries, focus_draft_id)

    @app.get("/api/books/{book_id}/drafts/{draft_id}")
    def get_draft(book_id: str, draft_id: str, current=Depends(book_actor), svc=Depends(service)):
        return svc.get_draft(current, book_id, draft_id)

    @app.post("/api/books/{book_id}/imports")
    def import_sources(book_id: str, payload: ImportRequest, current=Depends(book_actor), svc=Depends(service)):
        return svc.import_sources(current, book_id, payload)

    @app.post("/api/books/{book_id}/sources/{source_id}/evidence")
    def add_evidence(book_id: str, source_id: str, payload: EvidenceRequest, current=Depends(book_actor), svc=Depends(service)):
        return svc.add_evidence(current, book_id, source_id, payload)

    @app.post("/api/books/{book_id}/suggestions")
    def suggest(book_id: str, current=Depends(book_actor), svc=Depends(service)):
        return AccountingSuggestionTool(svc).execute(current, book_id)

    @app.patch("/api/books/{book_id}/drafts/{draft_id}")
    def update_draft(book_id: str, draft_id: str, payload: DraftUpdate, current=Depends(book_actor), svc=Depends(service)):
        return svc.update_draft(current, book_id, draft_id, payload)

    @app.post("/api/books/{book_id}/drafts/{draft_id}/approve")
    def approve(book_id: str, draft_id: str, payload: ApprovalRequest, current=Depends(book_actor), svc=Depends(service)):
        return svc.approve(current, book_id, draft_id, payload)

    @app.post("/api/books/{book_id}/exports")
    def export(book_id: str, payload: ExportRequest, current=Depends(book_actor), svc=Depends(service)):
        return svc.export(current, book_id, payload)

    @app.get("/api/books/{book_id}/exports")
    def list_exports(book_id: str, current=Depends(book_actor), svc=Depends(service)):
        return svc.list_exports(current, book_id)

    @app.get("/api/books/{book_id}/exports/{export_id}")
    def get_export(book_id: str, export_id: str, current=Depends(book_actor), svc=Depends(service)):
        return svc.get_export(current, book_id, export_id)

    @app.post("/api/books/{book_id}/periods/{period}/close")
    def close_period(book_id: str, period: str, payload: CloseRequest | None = None, current=Depends(book_actor), svc=Depends(service)):
        return svc.close_period(current, book_id, period, payload.snapshot_id if payload else None, payload.reason if payload else None)

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
    def grant_firm_member(payload: FirmMemberGrant, current=Depends(actor), dirs=Depends(directory)):
        return dirs.grant_firm_member(current, payload)

    @app.delete("/api/firm/members/{user_id}")
    def revoke_firm_member(user_id: str, current=Depends(actor), dirs=Depends(directory)):
        return dirs.revoke_firm_member(current, user_id)

    @app.get("/api/books/{book_id}/members")
    def book_members(book_id: str, current=Depends(actor), dirs=Depends(directory)):
        return dirs.list_book_members(current, book_id)

    @app.put("/api/books/{book_id}/members/{user_id}")
    def grant_book_member(book_id: str, user_id: str, payload: BookMemberGrant, current=Depends(actor), dirs=Depends(directory)):
        return dirs.grant_book_member(current, book_id, user_id, payload)

    @app.delete("/api/books/{book_id}/members/{user_id}")
    def revoke_book_member(book_id: str, user_id: str, current=Depends(actor), dirs=Depends(directory)):
        return dirs.revoke_book_member(current, book_id, user_id)

    @app.get("/api/companies/{company_id}/members")
    def company_members(company_id: str, current=Depends(actor), dirs=Depends(directory)):
        return dirs.list_company_members(current, company_id)

    @app.put("/api/companies/{company_id}/members")
    def grant_company_member(company_id: str, payload: CompanyMemberGrant, current=Depends(actor), dirs=Depends(directory)):
        return dirs.grant_company_member(current, company_id, payload)

    @app.delete("/api/companies/{company_id}/members/{user_id}")
    def revoke_company_member(company_id: str, user_id: str, current=Depends(actor), dirs=Depends(directory)):
        return dirs.revoke_company_member(current, company_id, user_id)

    @app.get("/api/companies/{company_id}/departments")
    def departments(company_id: str, current=Depends(actor), dirs=Depends(directory)):
        return dirs.list_departments(current, company_id)

    @app.post("/api/companies/{company_id}/departments", status_code=201)
    def create_department(company_id: str, payload: DepartmentCreate, current=Depends(actor), dirs=Depends(directory)):
        return dirs.create_department(current, company_id, payload)

    app.include_router(build_business_router(actor))
    app.include_router(build_intake_router(actor))
    app.include_router(build_import_wizard_router(actor))
    app.include_router(build_workspace_router(actor))
    app.include_router(build_intelligence_router(actor))
    app.include_router(build_queue_router(actor))
    app.include_router(build_operations_router(actor))
    app.include_router(build_catalog_router(actor))
    app.include_router(build_review_router(actor))
    app.include_router(build_cases_router(actor))
    app.include_router(build_learning_router(actor))
    app.include_router(build_quality_router(actor))
    app.include_router(build_bank_import_router(actor))
    app.include_router(build_reconciliation_router(actor))
    app.include_router(build_delivery_router(actor))
    app.include_router(build_ledgers_router(actor))
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
    return app


app = create_app()
