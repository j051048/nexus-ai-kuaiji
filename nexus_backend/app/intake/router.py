from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response

from ..accounting.domain import AccountingError
from ..business.storage import MAX_FILE_BYTES
from .models import IntakeComplete, IntakeCreate, IntakeRecognize, IntakeUpdate


def build_intake_router(actor_dependency):
    router = APIRouter(prefix="/api/intake", tags=["员工一体化录入"])

    def service(request: Request):
        return request.app.state.intake

    @router.get("/drafts")
    def list_drafts(
        company_id: str | None = None, current=Depends(actor_dependency), intake=Depends(service)
    ):
        return intake.list(current, company_id)

    @router.post("/drafts", status_code=201)
    def create_draft(
        payload: IntakeCreate, current=Depends(actor_dependency), intake=Depends(service)
    ):
        return intake.create(current, payload)

    @router.get("/drafts/{draft_id}")
    def get_draft(draft_id: str, current=Depends(actor_dependency), intake=Depends(service)):
        return intake.get(current, draft_id)

    @router.get("/drafts/{draft_id}/runs/{run_id}/execution")
    def run_execution(
        draft_id: str,
        run_id: str,
        current=Depends(actor_dependency),
        intake=Depends(service),
    ):
        return intake.run_execution(current, draft_id, run_id)

    @router.patch("/drafts/{draft_id}")
    def update_draft(
        draft_id: str,
        payload: IntakeUpdate,
        current=Depends(actor_dependency),
        intake=Depends(service),
    ):
        return intake.update(current, draft_id, payload)

    @router.post("/drafts/{draft_id}/attachments")
    def upload(
        draft_id: str,
        request: Request,
        expected_version: int = Query(..., ge=1),
        file: UploadFile | None = File(default=None),
        upload_id: str | None = Form(default=None),
        current=Depends(actor_dependency),
        intake=Depends(service),
    ):
        claimed = bool(upload_id)
        uploads = request.app.state.uploads if claimed else None
        stored = None
        try:
            if claimed:
                return uploads.consume(
                    current,
                    upload_id,
                    "intake_attachment",
                    draft_id,
                    lambda conn, metadata: intake.add_attachment(
                        current, draft_id, expected_version, metadata, conn=conn
                    ),
                )
            intake.require_upload(current, draft_id, expected_version)
            if file is None:
                raise AccountingError("请选择文件，或先完成大文件上传校验", 422)
            stored = intake.storage.put(
                file.filename, file.content_type, file.file.read(MAX_FILE_BYTES + 1)
            )
            result = intake.add_attachment(current, draft_id, expected_version, stored)
            if not any(item["id"] == stored["id"] for item in result["attachments"]):
                intake.storage.discard(stored)
            return result
        except AccountingError:
            if claimed:
                raise
            if stored:
                intake.storage.discard(stored)
            raise
        finally:
            if file is not None:
                file.file.close()

    @router.get("/attachments/{attachment_id}/content")
    def download(attachment_id: str, current=Depends(actor_dependency), intake=Depends(service)):
        data, metadata = intake.download(current, attachment_id)
        return Response(
            data,
            media_type=metadata["media_type"],
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(metadata['filename'], safe='')}",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Cache-Control": "private, no-store",
            },
        )

    @router.post("/drafts/{draft_id}/attachments/{attachment_id}/recognize")
    def recognize(
        draft_id: str,
        attachment_id: str,
        payload: IntakeRecognize,
        current=Depends(actor_dependency),
        intake=Depends(service),
    ):
        return intake.recognize(current, draft_id, attachment_id, payload)

    @router.post("/drafts/{draft_id}/complete")
    def complete(
        draft_id: str,
        payload: IntakeComplete,
        current=Depends(actor_dependency),
        intake=Depends(service),
    ):
        return intake.complete(current, draft_id, payload)

    return router
