from fastapi import APIRouter, Depends, Request
from .bank_imports import DuplicateDistinction


def build_bank_import_router(actor_dependency):
    router = APIRouter(prefix='/api/books/{book_id}/sources/{source_id}', tags=['bank-identity'])

    @router.get('/duplicate-check')
    def overview(book_id: str, source_id: str, request: Request, actor=Depends(actor_dependency)):
        return request.app.state.bank_import.overview(actor, book_id, source_id)

    @router.post('/duplicates/{other_id}/distinguish')
    def distinguish(book_id: str, source_id: str, other_id: str, body: DuplicateDistinction, request: Request, actor=Depends(actor_dependency)):
        return request.app.state.bank_import.distinguish(actor, book_id, source_id, other_id, body)

    return router
