from fastapi import APIRouter, Depends, Request

from app.accounting.domain import Actor
from app.cases.oa_snapshots import OAMaterialRequestWrite, OASnapshotWrite


def build_oa_router(actor_dependency):
    router = APIRouter(prefix="/api/books/{book_id}/oa-snapshots", tags=["OA 审批快照"])

    @router.get("")
    def list_snapshots(book_id: str, request: Request, current: Actor = Depends(actor_dependency)):
        return request.app.state.oa_snapshots.list(current, book_id)

    @router.get("/material-requests")
    def list_material_requests(
        book_id: str, request: Request, current: Actor = Depends(actor_dependency)
    ):
        return request.app.state.oa_snapshots.list_material_requests(current, book_id)

    @router.post("/material-requests", status_code=201)
    def create_material_request(
        book_id: str,
        body: OAMaterialRequestWrite,
        request: Request,
        current: Actor = Depends(actor_dependency),
    ):
        return request.app.state.oa_snapshots.create_material_request(current, book_id, body)

    @router.post("", status_code=201)
    def register_snapshot(
        book_id: str,
        body: OASnapshotWrite,
        request: Request,
        current: Actor = Depends(actor_dependency),
    ):
        return request.app.state.oa_snapshots.register(current, book_id, body)

    accessible = APIRouter(prefix="/api/oa/material-requests", tags=["OA 审批快照"])

    @accessible.get("")
    def list_accessible_material_requests(
        request: Request, current: Actor = Depends(actor_dependency)
    ):
        return request.app.state.oa_snapshots.list_accessible_requests(current)

    return router, accessible
