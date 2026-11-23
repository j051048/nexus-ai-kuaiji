from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from .models import (
    BridgeConfiguration,
    BridgeExport,
    BridgeImport,
    BridgeImportConfirm,
    BridgeManualReturn,
    BridgeReceiptImport,
    BridgeReissueCreate,
    BridgeReturnEvidence,
    BridgeReturnImport,
    BridgeReturnRowHandle,
)


def _attachment_disposition(filename: str) -> str:
    """RFC 5987 double header: ASCII fallback plus the UTF-8 spelling."""
    cleaned = str(filename)
    for char in ("\\", '"', chr(13), chr(10)):
        cleaned = cleaned.replace(char, "_")
    fallback = cleaned.encode("ascii", "replace").decode("ascii")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(cleaned, safe='')}"


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
    def template(
        book_id: str,
        vendor: str | None = Query(default=None, max_length=40),
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.template(actor, book_id, vendor)

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

    @router.post("/outbox/{intent_id}/artifacts", status_code=201)
    def artifact(
        book_id: str,
        intent_id: str,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.create_artifact(actor, book_id, intent_id)

    @router.get("/outbox/{intent_id}/artifacts")
    def artifacts(
        book_id: str,
        intent_id: str,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.list_artifacts(actor, book_id, intent_id)

    @router.get("/artifacts/{artifact_id}/download")
    def artifact_download(
        book_id: str,
        artifact_id: str,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        filename, content = svc.download_artifact(actor, book_id, artifact_id)
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": _attachment_disposition(filename)},
        )

    @router.post("/artifacts/{artifact_id}/receipts", status_code=201)
    def artifact_receipt(
        book_id: str,
        artifact_id: str,
        payload: BridgeReceiptImport,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.record_receipt(actor, book_id, artifact_id, payload)

    @router.post("/artifacts/{artifact_id}/returns", status_code=201)
    def artifact_return(
        book_id: str,
        artifact_id: str,
        payload: BridgeReturnImport,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.record_return(actor, book_id, artifact_id, payload)

    @router.post("/artifacts/{artifact_id}/returns/manual", status_code=201)
    def artifact_manual_return(
        book_id: str,
        artifact_id: str,
        payload: BridgeManualReturn,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.record_manual_return(actor, book_id, artifact_id, payload)

    @router.post("/artifacts/{artifact_id}/returns/{import_id}/rows/{voucher_code}/handle")
    def artifact_return_row_handle(
        book_id: str,
        artifact_id: str,
        import_id: str,
        voucher_code: str,
        payload: BridgeReturnRowHandle,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.handle_return_row(actor, book_id, artifact_id, import_id, voucher_code, payload)

    @router.post("/artifacts/{artifact_id}/returns/{import_id}/evidence", status_code=201)
    def artifact_return_evidence(
        book_id: str,
        artifact_id: str,
        import_id: str,
        payload: BridgeReturnEvidence,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.attach_evidence(actor, book_id, artifact_id, import_id, payload)

    @router.get("/artifacts/{artifact_id}/returns/{import_id}/evidence")
    def artifact_return_evidence_download(
        book_id: str,
        artifact_id: str,
        import_id: str,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        row = svc.evidence_for(actor, book_id, import_id, artifact_id)
        disposition = _attachment_disposition(str(row["filename"]))
        return Response(
            content=bytes(row["content"]),
            media_type=row["media_type"],
            headers={
                "Content-Disposition": disposition,
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/returns/tasks")
    def return_tasks(
        book_id: str,
        limit: int = 50,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return {"tasks": svc.list_return_tasks(actor, book_id, limit)}

    @router.get("/artifacts/{artifact_id}/returns")
    def artifact_returns(
        book_id: str,
        artifact_id: str,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.list_returns(actor, book_id, artifact_id)

    @router.get("/artifacts/{artifact_id}/receipts")
    def artifact_receipts(
        book_id: str,
        artifact_id: str,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.list_receipts(actor, book_id, artifact_id)

    @router.post("/outbox/{intent_id}/dispatch")
    def dispatch(
        book_id: str, intent_id: str, actor=Depends(actor_dependency), svc=Depends(service)
    ):
        return svc.dispatch(actor, book_id, intent_id)

    @router.post("/artifacts/{artifact_id}/reissues", status_code=201)
    def artifact_reissue(
        book_id: str,
        artifact_id: str,
        payload: BridgeReissueCreate,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.create_reissue(actor, book_id, artifact_id, payload)

    @router.get("/artifacts/{artifact_id}/reissues")
    def artifact_reissues(
        book_id: str,
        artifact_id: str,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.list_reissues(actor, book_id, artifact_id)

    @router.get("/reissues")
    def book_reissues(
        book_id: str,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.list_reissues(actor, book_id)

    @router.get("/reissues/{reissue_id}/download")
    def reissue_download(
        book_id: str,
        reissue_id: str,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        row = svc.download_reissue(actor, book_id, reissue_id)
        return Response(
            content=row["content"],
            media_type=row["media_type"],
            headers={
                "Content-Disposition": _attachment_disposition(row["filename"]),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Content-Sha256": row["content_sha256"],
            },
        )

    return router
