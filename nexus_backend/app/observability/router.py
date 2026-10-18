from fastapi import APIRouter, Depends, Query, Request


def build_observability_router(actor_dependency):
    router = APIRouter(prefix="/api/firm/observability", tags=["事务所可观测性"])

    def service(request: Request):
        return request.app.state.firm_observability

    @router.get("")
    def snapshot(
        period: str = Query("", max_length=7),
        current=Depends(actor_dependency),
        observability=Depends(service),
    ):
        return observability.snapshot(current, period)

    return router
