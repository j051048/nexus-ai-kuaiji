from typing import Literal
from urllib.parse import quote
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response
from app.accounting.domain import AccountingError
from app.business.storage import MAX_FILE_BYTES
from .models import (
    CaseCreate,
    CaseCommand,
    CaseAssign,
    CaseComment,
    CaseRespond,
    CaseReviewResponse,
)


def build_cases_router(actor_dependency):
    router = APIRouter(prefix="/api/cases", tags=["异常指派与补件"])

    def service(request: Request):
        return request.app.state.cases

    @router.get("")
    def listing(
        company_id: str | None = None,
        book_id: str | None = None,
        event_id: str | None = None,
        state: Literal["open", "awaiting_info", "response_submitted", "in_review", "closed"]
        | None = None,
        overdue: bool = False,
        mine: bool = False,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        actor=Depends(actor_dependency),
        cases=Depends(service),
    ):
        return cases.list(actor, company_id, book_id, event_id, state, overdue, mine, limit, offset)

    @router.post("", status_code=201)
    def create(payload: CaseCreate, actor=Depends(actor_dependency), cases=Depends(service)):
        return cases.create(actor, payload)

    @router.get("/attachments/{attachment_id}/content")
    def download(
        attachment_id: str,
        request: Request,
        actor=Depends(actor_dependency),
        cases=Depends(service),
    ):
        metadata = cases.download_metadata(actor, attachment_id)
        data = request.app.state.attachment_store.read(metadata)
        return Response(
            content=data,
            media_type=metadata["media_type"],
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(metadata['filename'], safe='')}",
                "Content-Security-Policy": "sandbox; default-src 'none'",
            },
        )

    @router.get("/{case_id}")
    def detail(case_id: str, actor=Depends(actor_dependency), cases=Depends(service)):
        return cases.get(actor, case_id)

    @router.post("/{case_id}/assign")
    def assign(
        case_id: str, payload: CaseAssign, actor=Depends(actor_dependency), cases=Depends(service)
    ):
        return cases.command(actor, case_id, "assign", payload)

    @router.get("/{case_id}/assignees")
    def assignees(case_id: str, actor=Depends(actor_dependency), cases=Depends(service)):
        return cases.assignees(actor, case_id)

    @router.post("/{case_id}/comments")
    def comment(
        case_id: str, payload: CaseComment, actor=Depends(actor_dependency), cases=Depends(service)
    ):
        return cases.command(actor, case_id, "comment", payload)

    @router.post("/{case_id}/respond")
    def respond(
        case_id: str, payload: CaseRespond, actor=Depends(actor_dependency), cases=Depends(service)
    ):
        return cases.command(actor, case_id, "respond", payload)

    @router.post("/{case_id}/review-response")
    def review(
        case_id: str,
        payload: CaseReviewResponse,
        actor=Depends(actor_dependency),
        cases=Depends(service),
    ):
        return cases.command(actor, case_id, "review-response", payload)

    @router.post("/{case_id}/close")
    def close(
        case_id: str, payload: CaseCommand, actor=Depends(actor_dependency), cases=Depends(service)
    ):
        return cases.command(actor, case_id, "close", payload)

    @router.post("/{case_id}/attachments")
    def upload(
        case_id: str,
        request: Request,
        expected_version: int = Query(..., ge=1),
        idempotency_key: str = Query(..., min_length=8, max_length=120),
        file: UploadFile | None = File(default=None),
        upload_id: str | None = Form(default=None),
        actor=Depends(actor_dependency),
        cases=Depends(service),
    ):
        claimed = bool(upload_id)
        uploads = request.app.state.uploads if claimed else None
        stored = None
        try:
            cases.require_upload(actor, case_id, expected_version, idempotency_key)
            if claimed:
                stored = uploads.take(actor, upload_id, "case_attachment")
                result = cases.add_attachment(
                    actor, case_id, expected_version, idempotency_key, stored
                )
                uploads.confirm(actor, upload_id)
                return result["case"]
            if file is None:
                raise AccountingError("请选择文件，或先完成大文件上传校验", 422)
            data = file.file.read(MAX_FILE_BYTES + 1)
            stored = request.app.state.attachment_store.put(file.filename, file.content_type, data)
            result = cases.add_attachment(actor, case_id, expected_version, idempotency_key, stored)
            if result["duplicate"]:
                request.app.state.attachment_store.discard(stored)
            return result["case"]
        except AccountingError:
            if claimed:
                if stored:
                    uploads.release(actor, upload_id)
                raise
            if stored:
                request.app.state.attachment_store.discard(stored)
            raise
        finally:
            if file is not None:
                file.file.close()

    return router
