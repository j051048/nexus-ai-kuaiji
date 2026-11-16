from fastapi import APIRouter, Depends, Query, Request


def build_observability_router(actor_dependency):
    router = APIRouter(prefix="/api/firm/observability", tags=["事务所可观测性"])

    def service(request: Request):
        return request.app.state.firm_observability

    @router.get("/closing-radar")
    def closing_radar(
        request: Request,
        period: str = Query(min_length=7, max_length=7),
        current=Depends(actor_dependency),
    ):
        return request.app.state.closing_radar.radar(current, period)

    @router.get("/insights")
    def insights(
        request: Request,
        period: str = Query(min_length=7, max_length=7),
        current=Depends(actor_dependency),
    ):
        return request.app.state.firm_insights.overview(current, period)

    @router.get("/insights/company")
    def insight_company(
        request: Request,
        book_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
    ):
        return request.app.state.firm_insights_extra.company(current, book_id)

    @router.get("/insights/companies")
    def insight_companies(
        request: Request,
        period: str = Query(min_length=7, max_length=7),
        current=Depends(actor_dependency),
    ):
        return request.app.state.firm_insights_extra.comparison(current, period)

    @router.get("/insights/timeline")
    def insight_timeline(
        request: Request,
        period: str = Query(min_length=7, max_length=7),
        current=Depends(actor_dependency),
    ):
        return request.app.state.firm_insights_extra.timeline(current, period)

    @router.get("/value")
    def value(
        request: Request,
        company_id: str = Query(min_length=1, max_length=100),
        period: str = Query(min_length=7, max_length=7),
        current=Depends(actor_dependency),
    ):
        return request.app.state.value_baseline.overview(current, company_id, period)

    @router.get("")
    def snapshot(
        period: str = Query("", max_length=7),
        current=Depends(actor_dependency),
        observability=Depends(service),
    ):
        return observability.snapshot(current, period)

    return router
