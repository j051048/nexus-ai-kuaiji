from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response

from ..accounting.domain import AccountingError
from .models import (
    BusinessEventCreate,
    BusinessEventUpdate,
    VersionRequest,
    ReturnRequest,
    HandoffRequest,
    DepartmentApproverGrant,
    SubmissionPolicyUpdate,
)
from .storage import MAX_FILE_BYTES


def build_business_router(actor_dependency):
    router = APIRouter(prefix="/api", tags=["企业业务事项"])

    def business(request: Request):
        return request.app.state.business

    @router.get("/companies/{company_id}/submission-policy")
    def submission_policy(
        company_id: str, current=Depends(actor_dependency), service=Depends(business)
    ):
        return service.submission.get(current, company_id)

    @router.put("/companies/{company_id}/submission-policy")
    def update_submission_policy(
        company_id: str,
        payload: SubmissionPolicyUpdate,
        current=Depends(actor_dependency),
        service=Depends(business),
    ):
        return service.submission.update(current, company_id, payload)

    @router.get("/business/context")
    def context(current=Depends(actor_dependency), service=Depends(business)):
        return service.context(current)

    @router.get("/business/preparation")
    def preparation(
        company_id: str,
        occurred_on: str,
        current=Depends(actor_dependency),
        service=Depends(business),
    ):
        return service.preparation(current, company_id, occurred_on)

    @router.get("/business/events")
    def list_events(
        scope: Literal["own", "department", "company", "accounting"] = "own",
        company_id: str | None = None,
        status: Literal[
            "draft",
            "submitted",
            "department_approved",
            "finance_approved",
            "handed_off",
            "returned",
        ]
        | None = None,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        period: str | None = Query(default=None, pattern=r"^20[0-9]{2}-(?:0[1-9]|1[0-2])$"),
        current=Depends(actor_dependency),
        service=Depends(business),
    ):
        return service.list_events(current, scope, company_id, status, limit, offset, period=period)

    @router.post("/business/events", status_code=201)
    def create_event(
        payload: BusinessEventCreate, current=Depends(actor_dependency), service=Depends(business)
    ):
        return service.create(current, payload)

    @router.get("/business/events/{event_id}")
    def get_event(event_id: str, current=Depends(actor_dependency), service=Depends(business)):
        return service.get_event(current, event_id)

    @router.patch("/business/events/{event_id}")
    def update_event(
        event_id: str,
        payload: BusinessEventUpdate,
        current=Depends(actor_dependency),
        service=Depends(business),
    ):
        return service.update(current, event_id, payload)

    @router.post("/business/events/{event_id}/submit")
    def submit(
        event_id: str,
        payload: VersionRequest,
        current=Depends(actor_dependency),
        service=Depends(business),
    ):
        return service.submit(current, event_id, payload.expected_version)

    @router.post("/business/events/{event_id}/department-approve")
    def department_approve(
        event_id: str,
        payload: VersionRequest,
        current=Depends(actor_dependency),
        service=Depends(business),
    ):
        return service.approve_department(current, event_id, payload.expected_version)

    @router.post("/business/events/{event_id}/finance-approve")
    def finance_approve(
        event_id: str,
        payload: VersionRequest,
        current=Depends(actor_dependency),
        service=Depends(business),
    ):
        return service.approve_finance(current, event_id, payload.expected_version)

    @router.post("/business/events/{event_id}/return")
    def return_event(
        event_id: str,
        payload: ReturnRequest,
        current=Depends(actor_dependency),
        service=Depends(business),
    ):
        return service.return_event(current, event_id, payload)

    @router.post("/business/events/{event_id}/handoff")
    def handoff(
        event_id: str,
        payload: HandoffRequest,
        current=Depends(actor_dependency),
        service=Depends(business),
    ):
        return service.handoff(current, event_id, payload)

    @router.post("/business/events/{event_id}/attachments")
    def upload_attachment(
        event_id: str,
        request: Request,
        expected_version: int = Query(..., ge=1),
        file: UploadFile | None = File(default=None),
        upload_id: str | None = Form(default=None),
        current=Depends(actor_dependency),
        service=Depends(business),
    ):
        store = request.app.state.attachment_store
        claimed = bool(upload_id)
        uploads = request.app.state.uploads if claimed else None
        stored = None
        try:
            if claimed:
                return uploads.consume(
                    current,
                    upload_id,
                    "event_attachment",
                    event_id,
                    lambda conn, metadata: service.add_attachment(
                        current, event_id, expected_version, metadata, conn=conn
                    ),
                )
            service.require_attachment_upload(current, event_id, expected_version)
            if file is None:
                raise AccountingError("请选择文件，或先完成大文件上传校验", 422)
            data = file.file.read(MAX_FILE_BYTES + 1)
            stored = store.put(file.filename, file.content_type, data)
            return service.add_attachment(current, event_id, expected_version, stored)
        except AccountingError:
            if claimed:
                raise
            if stored:
                store.discard(stored)
            raise
        except BaseException:
            # A connection/COMMIT error can occur after the database committed.
            # Preserve the blob on an uncertain outcome: orphaned files are
            # recoverable, deleting a committed original is not.
            raise
        finally:
            if file is not None:
                file.file.close()

    @router.get("/business/attachments/{attachment_id}/content")
    def download_attachment(
        attachment_id: str,
        request: Request,
        current=Depends(actor_dependency),
        service=Depends(business),
    ):
        metadata = service.download_metadata(current, attachment_id)
        data = request.app.state.attachment_store.read(metadata)
        return Response(
            content=data,
            media_type=metadata["media_type"],
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(metadata['filename'], safe='')}",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Cache-Control": "private, no-store",
            },
        )

    @router.get("/companies/{company_id}/departments/{department_id}/approvers")
    def approvers(
        company_id: str,
        department_id: str,
        current=Depends(actor_dependency),
        service=Depends(business),
    ):
        return service.list_department_approvers(current, company_id, department_id)

    @router.put("/companies/{company_id}/departments/{department_id}/approvers")
    def grant_approver(
        company_id: str,
        department_id: str,
        payload: DepartmentApproverGrant,
        current=Depends(actor_dependency),
        service=Depends(business),
    ):
        return service.grant_department_approver(
            current, company_id, department_id, payload.user_id
        )

    @router.delete("/companies/{company_id}/departments/{department_id}/approvers/{user_id}")
    def revoke_approver(
        company_id: str,
        department_id: str,
        user_id: str,
        current=Depends(actor_dependency),
        service=Depends(business),
    ):
        return service.revoke_department_approver(current, company_id, department_id, user_id)

    return router
