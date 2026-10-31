from fastapi import APIRouter, Depends, Request

from .legacy_review import Kind, LegacyReviewService, RecheckConfirm, RecheckPrepare


def build_legacy_review_router(actor_dependency):
    router = APIRouter(prefix="/api", tags=["旧稿当前版本人工补核"])

    def service(request: Request):
        return LegacyReviewService(request.app.state.service.db)

    @router.get("/books/{book_id}/legacy-reviews")
    def listing(book_id: str, actor=Depends(actor_dependency), svc=Depends(service)):
        return svc.list(actor, book_id)

    @router.get("/books/{book_id}/legacy-reviews/{kind}/{identifier}")
    def inspect(
        book_id: str,
        kind: Kind,
        identifier: str,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.inspect(actor, book_id, kind, identifier)

    @router.post("/books/{book_id}/legacy-reviews/{kind}/{identifier}/prepare", status_code=201)
    def prepare(
        book_id: str,
        kind: Kind,
        identifier: str,
        payload: RecheckPrepare,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.prepare(actor, book_id, kind, identifier, payload)

    @router.post("/legacy-reviews/{identifier}/confirm")
    def confirm(
        identifier: str,
        payload: RecheckConfirm,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.confirm(actor, identifier, payload)

    return router
