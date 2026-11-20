from fastapi import APIRouter, Depends, Request

from .links import AssetVehicleLinkRevoke, AssetVehicleLinkWrite
from .models import (
    AccrualRule,
    AssetReconcile,
    ClosingEvidenceWrite,
    ClosingGenerate,
    DepreciableAsset,
    DraftCommand,
    DraftPost,
)


def build_periodic_router(actor_dependency):
    router = APIRouter(prefix="/api/periodic", tags=["B04期末草稿"])

    def service(request: Request):
        return request.app.state.periodic

    def closing_service(request: Request):
        return request.app.state.closing_authority

    def link_service(request: Request):
        return request.app.state.asset_vehicle_links

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

    @router.get("/closing/authority")
    def closing_authority(
        company_id: str,
        current=Depends(actor_dependency),
        authority=Depends(closing_service),
    ):
        return authority.get(current, company_id)

    @router.get("/closing/summary")
    def closing_summary(
        book_id: str,
        current=Depends(actor_dependency),
        authority=Depends(closing_service),
    ):
        return authority.summary(current, book_id)

    @router.get("/closing/evidence")
    def closing_evidence(
        book_id: str,
        current=Depends(actor_dependency),
        authority=Depends(closing_service),
    ):
        return authority.evidence(current, book_id)

    @router.post("/closing/evidence", status_code=201)
    def bind_closing_evidence(
        payload: ClosingEvidenceWrite,
        current=Depends(actor_dependency),
        authority=Depends(closing_service),
    ):
        return authority.bind(current, payload)

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

    @router.get("/assets/{asset_id}/vehicle-link")
    def vehicle_link(asset_id: str, current=Depends(actor_dependency), links=Depends(link_service)):
        return {"link": links.summary(current, asset_id)}

    @router.post("/assets/{asset_id}/vehicle-link", status_code=201)
    def link_vehicle(
        asset_id: str,
        payload: AssetVehicleLinkWrite,
        current=Depends(actor_dependency),
        links=Depends(link_service),
    ):
        return links.link(current, asset_id, payload)

    @router.post("/assets/{asset_id}/vehicle-link/revoke", status_code=201)
    def revoke_vehicle_link(
        asset_id: str,
        payload: AssetVehicleLinkRevoke,
        current=Depends(actor_dependency),
        links=Depends(link_service),
    ):
        return links.revoke(current, asset_id, payload)

    @router.get("/vehicle-links")
    def vehicle_links(
        book_id: str,
        vehicle_external_id: str,
        current=Depends(actor_dependency),
        links=Depends(link_service),
    ):
        return links.for_vehicle(current, book_id, vehicle_external_id)

    @router.get("/drafts")
    def drafts(
        book_id: str,
        current=Depends(actor_dependency),
        periodic=Depends(service),
    ):
        return {"drafts": periodic.list_drafts(current, book_id)}

    return router
