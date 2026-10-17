from fastapi import APIRouter, Depends, Query, Request


def build_workspace_router(actor_dependency):
    router = APIRouter(prefix="/api/workspace", tags=["我的工作空间"])

    def service(request: Request):
        return request.app.state.workspace

    @router.get("/profile")
    def profile(current=Depends(actor_dependency), workspace=Depends(service)):
        return workspace.profile(current)

    @router.get("/tasks")
    def tasks(
        bucket: str = Query("action", max_length=20),
        kind: str | None = Query(None, max_length=30),
        company_id: str | None = Query(None, min_length=1, max_length=100),
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1_000_000),
        sort: str = Query("priority", max_length=20),
        q: str = Query("", max_length=200),
        current=Depends(actor_dependency),
        workspace=Depends(service),
    ):
        return workspace.list(current, bucket, kind, company_id, limit, offset, sort=sort, q=q)

    @router.get("/companies")
    def companies(
        q: str = Query("", max_length=200),
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1_000_000),
        current=Depends(actor_dependency),
        workspace=Depends(service),
    ):
        return workspace.companies(current, q, limit, offset)

    @router.get("/tasks/{kind}/{ref}")
    def task(kind: str, ref: str, current=Depends(actor_dependency), workspace=Depends(service)):
        return workspace.get(current, kind, ref)

    @router.get("/projections")
    def projections(current=Depends(actor_dependency), workspace=Depends(service)):
        return workspace.projection_status(current)

    @router.post("/projections/rebuild")
    def rebuild_projections(
        limit: int = Query(20, ge=1, le=50),
        current=Depends(actor_dependency),
        workspace=Depends(service),
    ):
        return workspace.rebuild_projections(current, limit)

    return router
