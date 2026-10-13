from fastapi import APIRouter, Depends, Query, Request


def build_workspace_router(actor_dependency):
    router = APIRouter(prefix="/api/workspace", tags=["我的工作空间"])

    def service(request: Request):
        return request.app.state.workspace

    @router.get("/profile")
    def profile(current=Depends(actor_dependency), workspace=Depends(service)):
        return workspace.profile(current)

    @router.get("/tasks")
    def tasks(bucket: str = Query("action", max_length=20), kind: str | None = Query(None, max_length=30),
              company_id: str | None = Query(None, min_length=1, max_length=100),
              limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=1_000_000),
              current=Depends(actor_dependency), workspace=Depends(service)):
        return workspace.list(current, bucket, kind, company_id, limit, offset)

    @router.get("/tasks/{kind}/{ref}")
    def task(kind: str, ref: str, current=Depends(actor_dependency), workspace=Depends(service)):
        return workspace.get(current, kind, ref)

    return router
