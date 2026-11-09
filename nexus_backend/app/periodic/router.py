from fastapi import APIRouter, Depends, Request

from .models import (
    AccrualRule,
    AssetReconcile,
    ClosingGenerate,
    DepreciableAsset,
    DraftCommand,
    DraftPost,
)


def build_periodic_router(actor_dependency):
    router = APIRouter(prefix="/api/periodic", tags=["B04期末草稿"])

    def service(request: Request):
        return request.app.state.periodic

    @router.get("/workspace")
    def workspace(book_id: str, current=Depends(actor_dependency), periodic=Depends(service)):
        return periodic.workspace(current, book_id)

    @router.post("/accrual-rules", status_code=201)
    def accrual_rule(
        payload: AccrualRule, current=Depends(actor_dependency), periodic=Depends(service)
    ):
        return periodic.create_accrual_rule(current, payload)

    @router.post("/assets", status_code=201)
    def asset(
        payload: DepreciableAsset, current=Depends(actor_dependency), periodic=Depends(service)
    ):
        return periodic.create_asset(current, payload)

    @router.post("/accrual-rules/{rule_id}/generate", status_code=201)
    def generate_accrual(
        rule_id: str, current=Depends(actor_dependency), periodic=Depends(service)
    ):
        return periodic.generate_accrual(current, rule_id)

    @router.post("/assets/{asset_id}/generate", status_code=201)
    def generate_asset(
        asset_id: str,
        book_id: str | None = None,
        current=Depends(actor_dependency),
        periodic=Depends(service),
    ):
        return periodic.generate_asset(current, asset_id, book_id=book_id)

    @router.get("/assets/{asset_id}/reconciliation")
    def reconciliation(
        asset_id: str, book_id: str, current=Depends(actor_dependency), periodic=Depends(service)
    ):
        return periodic.asset_reconciliation(current, asset_id, book_id)

    @router.post("/assets/{asset_id}/reconciliation", status_code=201)
    def reconcile(
        asset_id: str,
        payload: AssetReconcile,
        current=Depends(actor_dependency),
        periodic=Depends(service),
    ):
        return periodic.reconcile_asset(current, asset_id, payload)

    @router.post("/closing/generate", status_code=201)
    def generate_closing(
        payload: ClosingGenerate,
        current=Depends(actor_dependency),
        periodic=Depends(service),
    ):
        return periodic.generate_closing(current, payload)

    @router.post("/drafts/{draft_id}/approve")
    def approve(
        draft_id: str,
        payload: DraftCommand,
        current=Depends(actor_dependency),
        periodic=Depends(service),
    ):
        return periodic.approve(current, draft_id, payload)

    @router.post("/drafts/{draft_id}/post")
    def post(
        draft_id: str,
        payload: DraftPost,
        current=Depends(actor_dependency),
        periodic=Depends(service),
    ):
        return periodic.post(current, draft_id, payload)

    @router.get("/drafts")
    def drafts(
        book_id: str,
        current=Depends(actor_dependency),
        periodic=Depends(service),
    ):
        return {"drafts": periodic.list_drafts(current, book_id)}

    return router
