"""Management routes share verified identity and current firm selection."""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from .admin_models import (
    AdminAIControlUpdate,
    AdminAIPolicyUpdate,
    AdminCompanyStatus,
    AdminCompanyUpdate,
)


def build_admin_router(actor_dependency):
    router = APIRouter(prefix="/api/admin", tags=["组织管理后台"])

    def admin(request: Request):
        return request.app.state.admin

    def intelligence(request: Request):
        return request.app.state.intelligence

    def operations(request: Request):
        return request.app.state.operations

    def assistant_provider(request: Request):
        return getattr(request.app.state, "assistant_provider", None)

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

    @router.get("/companies/{company_id}/onboarding")
    def onboarding(
        company_id: str,
        period: str = Query(pattern=r"^20[0-9]{2}-(?:0[1-9]|1[0-2])$"),
        current=Depends(actor_dependency),
        service=Depends(admin),
    ):
        return service.onboarding(current, company_id, period)

    @router.get("/companies/{company_id}/ai-control")
    def ai_control(
        company_id: str,
        period: str | None = Query(
            default=None, min_length=7, max_length=7, pattern=r"^20[0-9]{2}-(?:0[1-9]|1[0-2])$"
        ),
        current=Depends(actor_dependency),
        service=Depends(admin),
        ai=Depends(intelligence),
        ops=Depends(operations),
        chat=Depends(assistant_provider),
    ):
        return service.ai_control(current, company_id, ai, ops, chat, period=period)

    @router.patch("/companies/{company_id}/ai-control")
    def update_ai_control(
        company_id: str,
        payload: AdminAIControlUpdate,
        current=Depends(actor_dependency),
        service=Depends(admin),
        ai=Depends(intelligence),
        chat=Depends(assistant_provider),
    ):
        return service.update_ai_control(current, company_id, payload, ai, chat)

    @router.put("/companies/{company_id}/ai-control/policy")
    def update_ai_policy(
        company_id: str,
        payload: AdminAIPolicyUpdate,
        current=Depends(actor_dependency),
        service=Depends(admin),
        ops=Depends(operations),
    ):
        return service.update_ai_policy(current, company_id, payload, ops)

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
