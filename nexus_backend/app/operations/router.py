"""Operating metrics never grant access to the company's private business originals."""

from fastapi import APIRouter, Depends, Query, Request

from .requests import AcknowledgeRequest, PolicyRequest, ReconcileRequest


def build_operations_router(actor_dependency):
    router = APIRouter(prefix="/api", tags=["企业运行与模型费用"])

    def operations(request: Request):
        return request.app.state.operations

    @router.get("/operations/companies")
    def companies(current=Depends(actor_dependency), service=Depends(operations)):
        return service.companies(current)

    @router.get("/operations/companies/{company_id}/overview")
    def overview(company_id: str, period: str | None = Query(default=None, min_length=7, max_length=7, pattern=r"^20[0-9]{2}-(?:0[1-9]|1[0-2])$"),
                 current=Depends(actor_dependency), service=Depends(operations)):
        return service.overview(current, company_id, period=period)

    @router.put("/operations/companies/{company_id}/policy")
    def policy(company_id: str, payload: PolicyRequest, current=Depends(actor_dependency), service=Depends(operations)):
        return service.policy(current, company_id, payload)

    @router.post("/operations/companies/{company_id}/refresh")
    def refresh(company_id: str, current=Depends(actor_dependency), service=Depends(operations)):
        return service.refresh(current, company_id)

    @router.post("/operations/alerts/{alert_id}/acknowledge")
    def acknowledge(alert_id: str, payload: AcknowledgeRequest, current=Depends(actor_dependency), service=Depends(operations)):
        return service.acknowledge(current, alert_id, payload)

    @router.post("/operations/costs/{cost_id}/reconcile")
    def reconcile(cost_id: str, payload: ReconcileRequest, current=Depends(actor_dependency), service=Depends(operations)):
        return service.reconcile(current, cost_id, payload)

    return router
