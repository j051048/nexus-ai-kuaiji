from fastapi import APIRouter, Depends, Request


def build_telemetry_router(actor_dependency):
    router = APIRouter(prefix="/api", tags=["运行性能观测"])

    def service(request: Request):
        return request.app.state.telemetry

    @router.get("/operations/companies/{company_id}/telemetry")
    def overview(company_id: str, current=Depends(actor_dependency), telemetry=Depends(service)):
        return telemetry.overview(current, company_id)

    return router
