import re
from urllib.parse import quote
from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from .models import ConfirmRequest, PasteRequest, PreviewRequest
from .parser import MAX_BYTES


def build_import_wizard_router(actor_dependency):
    router = APIRouter(prefix="/api/books/{book_id}/import-wizard", tags=["中文流水导入"])

    def service(request: Request):
        return request.app.state.import_wizard

    @router.post("/files", status_code=201)
    async def upload(
        book_id: str,
        file: UploadFile = File(...),
        actor=Depends(actor_dependency),
        wizard=Depends(service),
    ):
        with wizard.db.transaction(actor=actor) as conn:
            wizard._book(conn, actor, book_id, True)
        try:
            data = await file.read(MAX_BYTES + 1)
            return wizard.upload(actor, book_id, file.filename, data)
        finally:
            await file.close()

    @router.post("/paste", status_code=201)
    def paste(
        book_id: str,
        payload: PasteRequest,
        actor=Depends(actor_dependency),
        wizard=Depends(service),
    ):
        return wizard.upload(actor, book_id, payload.filename, payload.content.encode("utf-8"))

    @router.get("/profiles")
    def profiles(book_id: str, actor=Depends(actor_dependency), wizard=Depends(service)):
        return wizard.profiles(actor, book_id)

    @router.post("/previews", status_code=201)
    def preview(
        book_id: str,
        payload: PreviewRequest,
        actor=Depends(actor_dependency),
        wizard=Depends(service),
    ):
        return wizard.preview(actor, book_id, payload)

    @router.post("/previews/{preview_id}/confirm")
    def confirm(
        book_id: str,
        preview_id: str,
        payload: ConfirmRequest,
        actor=Depends(actor_dependency),
        wizard=Depends(service),
    ):
        return wizard.confirm(actor, book_id, preview_id, payload.remember_mapping)

    @router.get("/files/{file_id}")
    def original(
        book_id: str, file_id: str, actor=Depends(actor_dependency), wizard=Depends(service)
    ):
        value = wizard.original(actor, book_id, file_id)
        return Response(
            value["data"],
            media_type=value["media_type"],
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(value["filename"]),
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    @router.get("/sources/{source_id}/evidence")
    def evidence(
        book_id: str, source_id: str, actor=Depends(actor_dependency), wizard=Depends(service)
    ):
        return wizard.evidence(actor, book_id, source_id)

    return router


class ImportWizardBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not re.fullmatch(
                r"/api/books/[^/]+/import-wizard/(files|paste|previews)", scope.get("path", "")
            )
        ):
            return await self.app(scope, receive, send)
        buffer = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(buffer) + len(chunk) > MAX_BYTES + 128 * 1024:
                return await JSONResponse({"detail": "导入请求过大，文件最多2MB"}, status_code=413)(
                    scope, receive, send
                )
            buffer.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(buffer), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)
