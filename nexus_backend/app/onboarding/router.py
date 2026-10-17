from fastapi import APIRouter, Depends, Query, Request

from .models import CopyCatalogTemplates, StartOnboarding


def build_onboarding_router(actor_dependency):
    router = APIRouter(prefix="/api/firm/onboarding", tags=["客户标准化开通"])

    def service(request: Request):
        return request.app.state.onboarding

    @router.get("/preview")
    def preview(
        company_id: str = Query(min_length=1, max_length=100),
        period: str = Query(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$"),
        source_company_id: str = Query("", max_length=100),
        current=Depends(actor_dependency),
        onboarding=Depends(service),
    ):
        return onboarding.preview(current, company_id, period, source_company_id)

    @router.post("/runs", status_code=201)
    def start(
        payload: StartOnboarding,
        current=Depends(actor_dependency),
        onboarding=Depends(service),
    ):
        return onboarding.start(current, payload)

    @router.get("/runs")
    def runs(
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100_000),
        current=Depends(actor_dependency),
        onboarding=Depends(service),
    ):
        return onboarding.list(current, limit, offset)

    @router.get("/runs/{run_id}")
    def run(
        run_id: str,
        current=Depends(actor_dependency),
        onboarding=Depends(service),
    ):
        return onboarding.get(current, run_id)

    @router.post("/runs/{run_id}/refresh")
    def refresh(
        run_id: str,
        current=Depends(actor_dependency),
        onboarding=Depends(service),
    ):
        return onboarding.refresh(current, run_id)

    @router.post("/copy-templates")
    def copy_templates(
        payload: CopyCatalogTemplates,
        current=Depends(actor_dependency),
        onboarding=Depends(service),
    ):
        return onboarding.copy_catalog_templates(current, payload)

    return router
