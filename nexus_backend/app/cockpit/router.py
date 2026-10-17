from fastapi import APIRouter, Depends, Query, Request


def build_cockpit_router(actor_dependency):
    router = APIRouter(prefix="/api/firm/cockpit", tags=["事务所驾驶舱"])

    def service(request: Request):
        return request.app.state.firm_cockpit

    @router.get("")
    def snapshot(
        period: str = Query("", max_length=7),
        current=Depends(actor_dependency),
        cockpit=Depends(service),
    ):
        return cockpit.snapshot(current, period)

    return router
