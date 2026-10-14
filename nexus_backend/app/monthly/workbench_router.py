from fastapi import APIRouter, Depends, Query, Request


def build_monthly_workbench_router(actor_dependency):
    router = APIRouter(prefix="/api/monthly", tags=["客户月度进度"])

    def service(request: Request):
        return request.app.state.monthly_workbench

    @router.get("/workbench")
    def workbench(
        period: str = Query(pattern=r"^20[0-9]{2}-(?:0[1-9]|1[0-2])$"),
        search: str = Query(default="", max_length=120),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1_000_000),
        current=Depends(actor_dependency),
        monthly=Depends(service),
    ):
        return monthly.list(current, period, search, limit, offset)

    return router
