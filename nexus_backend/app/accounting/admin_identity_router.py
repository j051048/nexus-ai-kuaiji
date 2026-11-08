from fastapi import APIRouter, Depends, Query, Request, Response

from .admin_identity_models import (
    AccountBatchPreview,
    AccountRequestAccept,
    AccountRequestCreate,
    AccountRequestRetry,
    InvitationSend,
)


def build_admin_identity_router(actor_dependency, identity_dependency):
    router = APIRouter(prefix="/api/admin/account-requests", tags=["账号开通与激活"])

    def service(request: Request):
        return request.app.state.admin_identity

    @router.get("")
    def listing(
        company_id: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        current=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.list_requests(current, company_id, limit, offset)

    @router.post("", status_code=201)
    def create(
        payload: AccountRequestCreate,
        response: Response,
        current=Depends(actor_dependency),
        svc=Depends(service),
    ):
        response.headers["Cache-Control"] = "no-store"
        return svc.create(current, payload)

    @router.post("/preview")
    def preview(
        payload: AccountBatchPreview,
        response: Response,
        current=Depends(actor_dependency),
        svc=Depends(service),
    ):
        response.headers["Cache-Control"] = "no-store"
        return svc.preview_accounts(current, payload.items)

    @router.post("/{request_id}/retry")
    def retry(
        request_id: str,
        response: Response,
        payload: AccountRequestRetry | None = None,
        current=Depends(actor_dependency),
        svc=Depends(service),
    ):
        response.headers["Cache-Control"] = "no-store"
        return svc.retry(current, request_id, payload.initial_password if payload else None)

    @router.post("/{request_id}/cancel")
    def cancel(request_id: str, current=Depends(actor_dependency), svc=Depends(service)):
        return svc.cancel(current, request_id)

    @router.post("/{request_id}/send-invitation")
    def send_invitation(
        request_id: str,
        payload: InvitationSend,
        response: Response,
        current=Depends(actor_dependency),
        svc=Depends(service),
    ):
        response.headers["Cache-Control"] = "no-store"
        return svc.send_invitation(
            current, request_id, payload.activation_url.get_secret_value(), payload.idempotency_key
        )

    @router.get("/{request_id}/invitation-delivery")
    def invitation_delivery(
        request_id: str, response: Response, current=Depends(actor_dependency), svc=Depends(service)
    ):
        response.headers["Cache-Control"] = "no-store"
        return svc.invitation_status(current, request_id)

    @router.post("/{request_id}/accept")
    def accept(
        request_id: str,
        payload: AccountRequestAccept,
        current=Depends(identity_dependency),
        svc=Depends(service),
    ):
        return svc.accept(current, request_id, payload.token)

    return router
