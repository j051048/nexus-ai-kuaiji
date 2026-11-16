from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ..accounting.domain import Actor


def build_fde_router(actor_dependency) -> APIRouter:
    router = APIRouter(prefix="/api/firm/fde", tags=["fde"])

    @router.get("/adapters")
    def adapters(
        request: Request,
        company_id: str = Query(...),
        current: Actor = Depends(actor_dependency),
    ):
        return request.app.state.fde_adapters.catalog(current, company_id)

    return router
