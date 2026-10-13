from fastapi import APIRouter, Depends, Request
from .models import SameTransaction


def build_dispositions_router(actor_dependency):
    router = APIRouter(prefix="/api/books/{book_id}/sources/{source_id}", tags=["重复来源隔离"])

    @router.get("/disposition")
    def overview(book_id: str, source_id: str, request: Request, actor=Depends(actor_dependency)):
        return request.app.state.dispositions.overview(actor, book_id, source_id)

    @router.post("/duplicates/{canonical_id}/same-transaction")
    def confirm(book_id: str, source_id: str, canonical_id: str, body: SameTransaction,
                request: Request, actor=Depends(actor_dependency)):
        return request.app.state.dispositions.confirm(actor, book_id, source_id, canonical_id, body)

    return router
