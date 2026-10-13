import json
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import Field, StrictStr

from app.accounting.models import InputModel
from .models import FileMapping, MappingSave, MigrationConfirm, ProjectCreate, VersionCommand
from .normalization import MAX_BYTES
from .learning import (
    LegacyCandidateCreate,
    LegacyLearningReview,
    LegacyLearningShadow,
    LegacyLearningPublish,
    LegacyLearningRetire,
)
from .settlements import LegacyAllocationPreview, LegacyAllocationConfirm, LegacyAllocationReverse


class Remap(VersionCommand):
    mapping: FileMapping


class Grant(InputModel):
    user_id: StrictStr = Field(min_length=1, max_length=100)
    role: StrictStr = Field(min_length=1, max_length=20)
    reason: StrictStr = Field(min_length=1, max_length=1000)


class Reason(InputModel):
    reason: StrictStr = Field(min_length=1, max_length=1000)


def build_legacy_migration_router(actor_dependency):
    router = APIRouter(prefix="/api/books/{book_id}/legacy-migrations", tags=["独立旧账迁移与接管"])

    def service(request: Request):
        return request.app.state.legacy_migration

    def learning(request: Request):
        return request.app.state.legacy_learning

    def settlements(request: Request):
        return request.app.state.legacy_settlements

    @router.get("")
    def projects(
        book_id: str,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1_000_000),
        actor=Depends(actor_dependency),
        migration=Depends(service),
    ):
        return migration.list(actor, book_id, limit, offset)

    @router.post("", status_code=201)
    def create(
        book_id: str,
        body: ProjectCreate,
        actor=Depends(actor_dependency),
        migration=Depends(service),
    ):
        return migration.create(actor, book_id, body)

    @router.get("/template")
    def template(
        book_id: str,
        external_book_id: str = Query("SYNTHETIC-EXAMPLE", min_length=1, max_length=100),
        history_start: str | None = None,
        history_end: str | None = None,
        cutover_date: str | None = None,
        actor=Depends(actor_dependency),
        migration=Depends(service),
    ):
        value = migration.template(
            actor, book_id, external_book_id, history_start, history_end, cutover_date
        )
        return Response(
            json.dumps(value, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="SYNTHETIC-legacy-example.json"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/{project_id}")
    def project(
        book_id: str, project_id: str, actor=Depends(actor_dependency), migration=Depends(service)
    ):
        return migration.get(actor, book_id, project_id)

    @router.post("/{project_id}/files", status_code=201)
    async def upload(
        book_id: str,
        project_id: str,
        file: UploadFile = File(...),
        category: str = Form(...),
        mapping_json: str = Form("{}"),
        expected_version: int = Form(...),
        idempotency_key: str = Form(...),
        replaces_file_id: str | None = Form(None),
        reason: str = Form("上传旧账资料并确认范围"),
        actor=Depends(actor_dependency),
        migration=Depends(service),
    ):
        from app.accounting.domain import AccountingError
        from starlette.concurrency import run_in_threadpool

        with migration.db.transaction(actor=actor) as conn:
            migration._project(conn, actor, book_id, project_id, "manage")
        try:
            data = await file.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise AccountingError("旧账单个文件最多10MB，请分拆文件", 413)
            if not 8 <= len(idempotency_key) <= 120:
                raise AccountingError("上传编号无效")
            try:
                profile = FileMapping.model_validate_json(mapping_json).model_dump()
            except ValueError:
                raise AccountingError("请核对列映射、日期、金额及状态转换规则") from None
            if not reason.strip() or len(reason) > 1000:
                raise AccountingError("请填写原件范围及核对依据")
            return await run_in_threadpool(
                migration.upload,
                actor,
                book_id,
                project_id,
                file.filename,
                data,
                category,
                profile,
                expected_version,
                idempotency_key,
                replaces_file_id,
                reason,
            )
        finally:
            await file.close()

    @router.get("/{project_id}/files/{file_id}/inspection")
    def inspect(
        book_id: str,
        project_id: str,
        file_id: str,
        actor=Depends(actor_dependency),
        migration=Depends(service),
    ):
        return migration.inspection(actor, book_id, project_id, file_id)

    @router.post("/{project_id}/files/{file_id}/remap", status_code=201)
    def remap(
        book_id: str,
        project_id: str,
        file_id: str,
        body: Remap,
        actor=Depends(actor_dependency),
        migration=Depends(service),
    ):
        return migration.remap(actor, book_id, project_id, file_id, body.mapping.model_dump(), body)

    @router.get("/{project_id}/files/{file_id}")
    def original(
        book_id: str,
        project_id: str,
        file_id: str,
        actor=Depends(actor_dependency),
        migration=Depends(service),
    ):
        value = migration.original(actor, book_id, project_id, file_id)
        return Response(
            value["data"],
            media_type=value["media_type"],
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(value["filename"]),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/{project_id}/records")
    def records(
        book_id: str,
        project_id: str,
        kind: str | None = None,
        period: str | None = None,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1_000_000),
        actor=Depends(actor_dependency),
        migration=Depends(service),
    ):
        return migration.records(actor, book_id, project_id, kind, period, limit, offset)

    @router.get("/{project_id}/issues")
    def issues(
        book_id: str,
        project_id: str,
        severity: str | None = None,
        kind: str | None = None,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1_000_000),
        actor=Depends(actor_dependency),
        migration=Depends(service),
    ):
        return migration.issues(actor, book_id, project_id, severity, kind, limit, offset)

    @router.get("/{project_id}/report")
    def report(
        book_id: str, project_id: str, actor=Depends(actor_dependency), migration=Depends(service)
    ):
        return Response(
            json.dumps(migration.report(actor, book_id, project_id), ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="migration-validation.json"',
                "Cache-Control": "no-store",
            },
        )

    @router.post("/{project_id}/mapping", status_code=201)
    def mapping(
        book_id: str,
        project_id: str,
        body: MappingSave,
        actor=Depends(actor_dependency),
        migration=Depends(service),
    ):
        return migration.save_mapping(actor, book_id, project_id, body)

    @router.post("/{project_id}/parse", status_code=202)
    def parse(
        book_id: str,
        project_id: str,
        body: VersionCommand,
        actor=Depends(actor_dependency),
        migration=Depends(service),
    ):
        return migration.enqueue(actor, book_id, project_id, "parse", body)

    @router.post("/{project_id}/validate", status_code=202)
    def validate(
        book_id: str,
        project_id: str,
        body: VersionCommand,
        actor=Depends(actor_dependency),
        migration=Depends(service),
    ):
        return migration.enqueue(actor, book_id, project_id, "validate", body)

    @router.get("/{project_id}/jobs/{job_id}")
    def job(
        book_id: str,
        project_id: str,
        job_id: str,
        actor=Depends(actor_dependency),
        migration=Depends(service),
    ):
        return migration.job(actor, book_id, project_id, job_id)

    @router.post("/{project_id}/jobs/{job_id}/retry", status_code=202)
    def retry(
        book_id: str,
        project_id: str,
        job_id: str,
        body: VersionCommand,
        actor=Depends(actor_dependency),
        migration=Depends(service),
    ):
        return migration.retry(actor, book_id, project_id, job_id, body)

    @router.post("/{project_id}/jobs/{job_id}/cancel")
    def cancel_job(
        book_id: str,
        project_id: str,
        job_id: str,
        body: VersionCommand,
        actor=Depends(actor_dependency),
        migration=Depends(service),
    ):
        return migration.cancel_job(actor, book_id, project_id, job_id, body)

    @router.post("/{project_id}/accept", status_code=201)
    def accept(
        book_id: str,
        project_id: str,
        body: MigrationConfirm,
        actor=Depends(actor_dependency),
        migration=Depends(service),
    ):
        return migration.accept(actor, book_id, project_id, body)

    @router.post("/{project_id}/activate", status_code=201)
    def activate(
        book_id: str,
        project_id: str,
        body: MigrationConfirm,
        actor=Depends(actor_dependency),
        migration=Depends(service),
    ):
        return migration.activate(actor, book_id, project_id, body)

    @router.get("/{project_id}/access")
    def access(
        book_id: str, project_id: str, actor=Depends(actor_dependency), migration=Depends(service)
    ):
        return migration.access_overview(actor, book_id, project_id)

    @router.post("/{project_id}/access")
    def grant(
        book_id: str,
        project_id: str,
        body: Grant,
        actor=Depends(actor_dependency),
        migration=Depends(service),
    ):
        return migration.grant_access(
            actor, book_id, project_id, body.user_id, body.role, body.reason
        )

    @router.delete("/{project_id}/access/{user_id}")
    def revoke(
        book_id: str,
        project_id: str,
        user_id: str,
        body: Reason,
        actor=Depends(actor_dependency),
        migration=Depends(service),
    ):
        return migration.revoke_access(actor, book_id, project_id, user_id, body.reason)

    @router.get("/{project_id}/sample-candidates")
    def candidates(
        book_id: str,
        project_id: str,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1_000_000),
        actor=Depends(actor_dependency),
        reuse=Depends(learning),
    ):
        return reuse.list(actor, book_id, project_id, limit, offset)

    @router.post("/{project_id}/sample-candidates", status_code=201)
    def create_candidate(
        book_id: str,
        project_id: str,
        body: LegacyCandidateCreate,
        actor=Depends(actor_dependency),
        reuse=Depends(learning),
    ):
        return reuse.create(actor, book_id, project_id, body)

    @router.post("/{project_id}/sample-candidates/{candidate_id}/review")
    def review_candidate(
        book_id: str,
        project_id: str,
        candidate_id: str,
        body: LegacyLearningReview,
        actor=Depends(actor_dependency),
        reuse=Depends(learning),
    ):
        return reuse.review(actor, book_id, project_id, candidate_id, body)

    @router.post("/{project_id}/sample-candidates/{candidate_id}/shadow", status_code=201)
    def shadow(
        book_id: str,
        project_id: str,
        candidate_id: str,
        body: LegacyLearningShadow,
        actor=Depends(actor_dependency),
        reuse=Depends(learning),
    ):
        return reuse.shadow(actor, book_id, project_id, candidate_id, body)

    @router.post("/{project_id}/sample-candidates/{candidate_id}/publish")
    def publish_candidate(
        book_id: str,
        project_id: str,
        candidate_id: str,
        body: LegacyLearningPublish,
        actor=Depends(actor_dependency),
        reuse=Depends(learning),
    ):
        return reuse.publish(actor, book_id, project_id, candidate_id, body)

    @router.post("/{project_id}/sample-candidates/{candidate_id}/retire")
    def retire_candidate(
        book_id: str,
        project_id: str,
        candidate_id: str,
        body: LegacyLearningRetire,
        actor=Depends(actor_dependency),
        reuse=Depends(learning),
    ):
        return reuse.retire(actor, book_id, project_id, candidate_id, body)

    @router.get("/{project_id}/settlements/items")
    def items(
        book_id: str,
        project_id: str,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1_000_000),
        actor=Depends(actor_dependency),
        clearance=Depends(settlements),
    ):
        return clearance.list(actor, book_id, project_id, limit, offset)

    @router.get("/{project_id}/settlements/banks")
    def bank_sources(
        book_id: str,
        project_id: str,
        bank_book_id: str,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1_000_000),
        actor=Depends(actor_dependency),
        clearance=Depends(settlements),
    ):
        return clearance.eligible_current_bank_sources(
            actor, book_id, project_id, bank_book_id, limit, offset
        )

    @router.post("/{project_id}/settlements/previews", status_code=201)
    def clearance_preview(
        book_id: str,
        project_id: str,
        body: LegacyAllocationPreview,
        actor=Depends(actor_dependency),
        clearance=Depends(settlements),
    ):
        return clearance.preview(actor, book_id, project_id, body)

    @router.post("/{project_id}/settlements/previews/{preview_id}/confirm", status_code=201)
    def confirm_clearance(
        book_id: str,
        project_id: str,
        preview_id: str,
        body: LegacyAllocationConfirm,
        actor=Depends(actor_dependency),
        clearance=Depends(settlements),
    ):
        return clearance.confirm(actor, book_id, project_id, preview_id, body)

    @router.post("/{project_id}/settlements/commands/{command_id}/reverse", status_code=201)
    def reverse_clearance(
        book_id: str,
        project_id: str,
        command_id: str,
        body: LegacyAllocationReverse,
        actor=Depends(actor_dependency),
        clearance=Depends(settlements),
    ):
        return clearance.reverse(actor, book_id, project_id, command_id, body)

    return router


class MigrationBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not re.fullmatch(
                r"/api/books/[^/]+/legacy-migrations/[^/]+/files", scope.get("path", "")
            )
        ):
            return await self.app(scope, receive, send)
        buffered = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            buffered.extend(message.get("body", b""))
            if len(buffered) > MAX_BYTES + 128 * 1024:
                return await JSONResponse(
                    {"detail": "旧账上传请求过大，单文件最多10MB"}, status_code=413
                )(scope, receive, send)
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(buffered), "more_body": False}
            return await receive()

        return await self.app(scope, bounded_receive, send)
