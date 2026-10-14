"""Management routes share verified identity and current firm selection."""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from .admin_models import AdminCompanyStatus, AdminCompanyUpdate


def build_admin_router(actor_dependency):
    router = APIRouter(prefix="/api/admin", tags=["组织管理后台"])

    def admin(request: Request):
        return request.app.state.admin

    @router.get("/companies")
    def companies(
        search: str = Query(default="", max_length=120),
        status: Literal["active", "suspended", "archived"] | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        current=Depends(actor_dependency),
        service=Depends(admin),
    ):
        return service.list_companies(
            current, search=search, status=status, limit=limit, offset=offset
        )

    @router.get("/companies/{company_id}")
    def company(company_id: str, current=Depends(actor_dependency), service=Depends(admin)):
        return service.get_company(current, company_id)

    @router.patch("/companies/{company_id}")
    def update_company(
        company_id: str,
        payload: AdminCompanyUpdate,
        current=Depends(actor_dependency),
        service=Depends(admin),
    ):
        return service.update_company(current, company_id, payload)

    @router.post("/companies/{company_id}/status")
    def change_status(
        company_id: str,
        payload: AdminCompanyStatus,
        current=Depends(actor_dependency),
        service=Depends(admin),
    ):
        return service.change_status(current, company_id, payload)

    @router.get("/audit")
    def audit(
        company_id: str | None = Query(default=None, min_length=1, max_length=100),
        action: str | None = Query(default=None, min_length=1, max_length=100),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        current=Depends(actor_dependency),
        service=Depends(admin),
    ):
        return service.audit(
            current, company_id=company_id, action=action, limit=limit, offset=offset
        )

    return router
