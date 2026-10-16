from fastapi import APIRouter, Depends, Request

from .models import Generate, MaterialReview, MaterialSubmit, PlanSave, TemplateCreate


def build_service_plans_router(actor_dependency):
    router = APIRouter(prefix="/api", tags=["月度服务与资料清单"])

    def service(request: Request):
        return request.app.state.service_plans

    @router.get("/books/{book_id}/service-plan")
    def overview(book_id: str, actor=Depends(actor_dependency), plans=Depends(service)):
        return plans.overview(actor, book_id)

    @router.put("/books/{book_id}/service-plan")
    def save(
        book_id: str, payload: PlanSave, actor=Depends(actor_dependency), plans=Depends(service)
    ):
        return plans.save_plan(actor, book_id, payload)

    @router.post("/books/{book_id}/material-templates", status_code=201)
    def template(
        book_id: str,
        payload: TemplateCreate,
        actor=Depends(actor_dependency),
        plans=Depends(service),
    ):
        return plans.create_template(actor, book_id, payload)

    @router.post("/books/{book_id}/material-templates/{template_id}/generate")
    def generate(
        book_id: str,
        template_id: str,
        payload: Generate,
        actor=Depends(actor_dependency),
        plans=Depends(service),
    ):
        return plans.generate(actor, book_id, template_id, payload)

    @router.get("/books/{book_id}/material-requests")
    def book_requests(
        book_id: str,
        limit: int = 20,
        offset: int = 0,
        request_id: str | None = None,
        actor=Depends(actor_dependency),
        plans=Depends(service),
    ):
        return plans.book_requests(actor, book_id, limit, offset, request_id)

    @router.get("/material-requests")
    def mine(
        company_id: str | None = None,
        period: str | None = None,
        limit: int = 20,
        offset: int = 0,
        request_id: str | None = None,
        actor=Depends(actor_dependency),
        plans=Depends(service),
    ):
        return plans.my_requests(actor, company_id, period, limit, offset, request_id)

    @router.post("/material-requests/{request_id}/submit")
    def submit(
        request_id: str,
        payload: MaterialSubmit,
        actor=Depends(actor_dependency),
        plans=Depends(service),
    ):
        return plans.submit(actor, request_id, payload)

    @router.post("/material-requests/{request_id}/review")
    def review(
        request_id: str,
        payload: MaterialReview,
        actor=Depends(actor_dependency),
        plans=Depends(service),
    ):
        return plans.review(actor, request_id, payload)

    return router
