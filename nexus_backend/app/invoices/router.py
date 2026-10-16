"""发票导出表导入接口：上传→解析批次→证据行与银行流水候选线索。"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field


class InvoiceImportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_id: str = Field(pattern=r"^[0-9a-f]{32}$")


def build_invoice_imports_router(actor_dependency):
    router = APIRouter(prefix="/api/books/{book_id}/invoice-imports", tags=["发票导出表导入"])

    def service(request: Request):
        return request.app.state.invoice_imports

    @router.post("", status_code=201)
    def create(
        book_id: str,
        payload: InvoiceImportCreate,
        actor=Depends(actor_dependency),
        invoices=Depends(service),
    ):
        return invoices.create(actor, book_id, payload.upload_id)

    @router.get("")
    def listing(
        book_id: str,
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1000000),
        actor=Depends(actor_dependency),
        invoices=Depends(service),
    ):
        return invoices.list_batches(actor, book_id, limit, offset)

    @router.get("/{batch_id}")
    def detail(
        book_id: str,
        batch_id: str,
        actor=Depends(actor_dependency),
        invoices=Depends(service),
    ):
        return invoices.get_batch(actor, book_id, batch_id)

    @router.get("/{batch_id}/original")
    def original(
        book_id: str,
        batch_id: str,
        actor=Depends(actor_dependency),
        invoices=Depends(service),
    ):
        filename, data = invoices.original(actor, book_id, batch_id)
        return Response(
            data,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Cache-Control": "private, no-store",
            },
        )

    return router
