from urllib.parse import quote
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response


def build_source_history_router(actor_dependency):
    router = APIRouter(prefix='/api/books/{book_id}/sources/{source_id}/history', tags=['历史事实与原件'])

    @router.get('')
    def history(book_id: str, source_id: str, request: Request, actor=Depends(actor_dependency)):
        return request.app.state.source_history.get(actor, book_id, source_id)

    @router.get('/attachments/{attachment_id}/content')
    def content(book_id: str, source_id: str, attachment_id: str, request: Request, actor=Depends(actor_dependency)):
        data, filename, media_type = request.app.state.source_history.download(actor, book_id, source_id, attachment_id)
        return Response(content=data, media_type=media_type, headers={
            'Content-Disposition': "attachment; filename*=UTF-8''" + quote(filename, safe=''),
            'Content-Security-Policy': "sandbox; default-src 'none'", 'X-Content-Type-Options': 'nosniff',
            'Cache-Control': 'private, no-store',
        })

    return router
