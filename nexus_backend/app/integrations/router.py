from fastapi import APIRouter, Depends, Query, Request
from .models import BridgeConfiguration, BridgeExport, BridgeImport, BridgeImportConfirm


def build_integrations_router(actor_dependency):
    router = APIRouter(prefix="/api/books/{book_id}/integrations/tplus", tags=["T+ 文件接入底座"])

    def service(request: Request):
        return request.app.state.integrations

    @router.get("")
    def overview(
        book_id: str,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1000000),
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.overview(actor, book_id, limit, offset)

    @router.post("/configuration")
    def configure(
        book_id: str,
        payload: BridgeConfiguration,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.configure(actor, book_id, payload)

    @router.get("/template")
    def template(book_id: str, actor=Depends(actor_dependency), svc=Depends(service)):
        return svc.template(actor, book_id)

    @router.post("/imports", status_code=201)
    def preview(
        book_id: str, payload: BridgeImport, actor=Depends(actor_dependency), svc=Depends(service)
    ):
        return svc.preview_import(actor, book_id, payload)

    @router.get("/imports/{preview_id}")
    def detail(
        book_id: str, preview_id: str, actor=Depends(actor_dependency), svc=Depends(service)
    ):
        return svc.get_preview(actor, book_id, preview_id)

    @router.post("/imports/{preview_id}/confirm")
    def confirm(
        book_id: str,
        preview_id: str,
        payload: BridgeImportConfirm,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.confirm_import(actor, book_id, preview_id, payload)

    @router.post("/outbox", status_code=201)
    def prepare(
        book_id: str, payload: BridgeExport, actor=Depends(actor_dependency), svc=Depends(service)
    ):
        return svc.prepare_export(actor, book_id, payload)

    @router.get("/outbox/{intent_id}")
    def intent(book_id: str, intent_id: str, actor=Depends(actor_dependency), svc=Depends(service)):
        return svc.get_outbox(actor, book_id, intent_id)

    @router.post("/outbox/{intent_id}/dispatch")
    def dispatch(
        book_id: str, intent_id: str, actor=Depends(actor_dependency), svc=Depends(service)
    ):
        return svc.dispatch(actor, book_id, intent_id)

    return router
