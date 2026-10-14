"""AI evidence endpoints reuse the verified actor dependency and current database grants."""

from fastapi import APIRouter, Depends, Query, Request, Response

from .requests import (
    ApplyRequest,
    BankLinkRequest,
    CompanyAISettings,
    OCRRequest,
    ProposalRequest,
    ResolutionRequest,
)


def build_intelligence_router(actor_dependency):
    router = APIRouter(prefix="/api", tags=["票据核对与模型建议"])

    def intelligence(request: Request):
        return request.app.state.intelligence

    @router.get("/intelligence/config")
    def config(current=Depends(actor_dependency), service=Depends(intelligence)):
        return service.config(current)

    @router.get("/companies/{company_id}/intelligence/settings")
    def company_settings(
        company_id: str, current=Depends(actor_dependency), service=Depends(intelligence)
    ):
        return service.company_settings(current, company_id)

    @router.put("/companies/{company_id}/intelligence/settings")
    def update_company_settings(
        company_id: str,
        payload: CompanyAISettings,
        current=Depends(actor_dependency),
        service=Depends(intelligence),
    ):
        return service.company_settings(current, company_id, payload.enabled)

    @router.get("/business/events/{event_id}/intelligence")
    def detail(event_id: str, current=Depends(actor_dependency), service=Depends(intelligence)):
        return service.detail(current, event_id)

    @router.post("/business/events/{event_id}/ocr")
    def extract(
        event_id: str,
        payload: OCRRequest,
        current=Depends(actor_dependency),
        service=Depends(intelligence),
    ):
        return service.extract(current, event_id, payload)

    @router.get("/business/events/{event_id}/intelligence/runs/{run_id}/pages/{page}")
    def evidence_page(
        event_id: str,
        run_id: str,
        page: int,
        current=Depends(actor_dependency),
        service=Depends(intelligence),
    ):
        return Response(
            service.evidence_page(current, event_id, run_id, page),
            media_type="image/jpeg",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @router.post("/business/events/{event_id}/reconcile")
    def reconcile(event_id: str, current=Depends(actor_dependency), service=Depends(intelligence)):
        return service.reconcile(current, event_id)

    @router.get("/intelligence/exceptions")
    def exceptions(
        company_id: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100_000),
        current=Depends(actor_dependency),
        service=Depends(intelligence),
    ):
        return service.exceptions(current, company_id=company_id, limit=limit, offset=offset)

    @router.post("/intelligence/findings/{finding_id}/resolve")
    def resolve(
        finding_id: str,
        payload: ResolutionRequest,
        current=Depends(actor_dependency),
        service=Depends(intelligence),
    ):
        return service.resolve(current, finding_id, payload.reason)

    @router.post("/business/events/{event_id}/bank-links")
    def link_bank(
        event_id: str,
        payload: BankLinkRequest,
        current=Depends(actor_dependency),
        service=Depends(intelligence),
    ):
        return service.link_bank(current, event_id, payload)

    @router.delete("/business/events/{event_id}/bank-links/{source_id}")
    def unlink_bank(
        event_id: str,
        source_id: str,
        payload: ResolutionRequest,
        current=Depends(actor_dependency),
        service=Depends(intelligence),
    ):
        return service.unlink_bank(current, event_id, source_id, payload.reason)

    @router.post("/books/{book_id}/drafts/{draft_id}/ai-proposals")
    def propose(
        book_id: str,
        draft_id: str,
        payload: ProposalRequest,
        current=Depends(actor_dependency),
        service=Depends(intelligence),
    ):
        return service.propose(current, book_id, draft_id, payload)

    @router.post("/intelligence/proposals/{run_id}/apply")
    def apply(
        run_id: str,
        payload: ApplyRequest,
        current=Depends(actor_dependency),
        service=Depends(intelligence),
    ):
        return service.apply(current, run_id, payload.expected_version)

    return router
