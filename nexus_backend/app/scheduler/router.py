from fastapi import APIRouter, Depends, Request

from .service import PlanWrite


def build_scheduler_router(actor_dependency):
    router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])

    def service(request: Request):
        return request.app.state.scheduler

    @router.get("/plans")
    def plans(request: Request, actor=Depends(actor_dependency)):
        return service(request).list_plans(actor)

    @router.post("/plans", status_code=201)
    def create_plan(request: Request, payload: PlanWrite, actor=Depends(actor_dependency)):
        return service(request).create_plan(actor, payload)

    @router.post("/plans/{plan_id}/enabled")
    def set_enabled(
        request: Request, plan_id: str, enabled: bool = True, actor=Depends(actor_dependency)
    ):
        return service(request).set_enabled(actor, plan_id, enabled)

    @router.get("/plans/{plan_id}/triggers")
    def triggers(request: Request, plan_id: str, actor=Depends(actor_dependency)):
        return service(request).triggers(actor, plan_id)

    @router.post("/triggers/{trigger_id}/confirm")
    def confirm(request: Request, trigger_id: str, actor=Depends(actor_dependency)):
        return service(request).confirm(actor, trigger_id)

    return router
