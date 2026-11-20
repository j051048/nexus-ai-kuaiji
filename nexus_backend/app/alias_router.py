"""OPT07-F alias endpoints (firm-scoped, company-explicit)."""

from fastapi import APIRouter, Depends, Request, Query

from .aliases import AliasCreate, AliasService


def build_alias_router(actor):
    router = APIRouter()

    def service(request: Request) -> AliasService:
        return request.app.state.aliases

    @router.get("/api/companies/{company_id}/aliases")
    def list_aliases(
        company_id: str,
        subject_type: str | None = None,
        current=Depends(actor),
        svc=Depends(service),
    ):
        return svc.list(current, company_id, subject_type)

    @router.get("/api/companies/{company_id}/aliases/resolve")
    def resolve_alias(
        company_id: str,
        alias: str = Query(min_length=1),
        subject_type: str = "transport_subject",
        on_date: str = Query(alias="on_date"),
        current=Depends(actor),
        svc=Depends(service),
    ):
        return svc.resolve(current, company_id, subject_type, alias, on_date)

    @router.post("/api/companies/{company_id}/aliases", status_code=201)
    def create_alias(
        company_id: str, payload: AliasCreate, current=Depends(actor), svc=Depends(service)
    ):
        return svc.add(current, company_id, payload)

    @router.post("/api/companies/{company_id}/aliases/{alias_id}/retire")
    def retire_alias(
        company_id: str, alias_id: str, payload: dict, current=Depends(actor), svc=Depends(service)
    ):
        return svc.retire(current, company_id, alias_id, str(payload.get("reason", "")))

    return router
