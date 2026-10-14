from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from urllib.parse import quote

from .models import (
    BaselineCreate,
    BaselineImport,
    CheckCreate,
    MonthlyReason,
    PlanApproval,
    PlanCreate,
    PlanGenerate,
)


def build_monthly_router(actor_dependency):
    router = APIRouter(prefix="/api/books/{book_id}/monthly", tags=["月度参考余额与固定事项"])

    def service(request: Request):
        return request.app.state.monthly

    @router.get("")
    def overview(book_id: str, actor=Depends(actor_dependency), monthly=Depends(service)):
        return monthly.overview(actor, book_id)

    @router.post("/baselines", status_code=201)
    def baseline(
        book_id: str,
        payload: BaselineCreate,
        actor=Depends(actor_dependency),
        monthly=Depends(service),
    ):
        return monthly.create_baseline(actor, book_id, payload)

    @router.post("/baselines/{baseline_id}/confirm")
    def confirm(
        book_id: str,
        baseline_id: str,
        payload: MonthlyReason,
        actor=Depends(actor_dependency),
        monthly=Depends(service),
    ):
        return monthly.confirm_baseline(actor, book_id, baseline_id, payload)

    @router.post("/baselines/import", status_code=201)
    def import_balance(
        book_id: str,
        payload: BaselineImport,
        actor=Depends(actor_dependency),
        monthly=Depends(service),
    ):
        return monthly.import_baseline(actor, book_id, payload)

    @router.get("/baselines/{baseline_id}/original")
    def original(
        book_id: str, baseline_id: str, actor=Depends(actor_dependency), monthly=Depends(service)
    ):
        value = monthly.original(actor, book_id, baseline_id)
        return Response(
            content=value["content"].encode("utf-8"),
            media_type="text/csv" if value["format"] == "csv" else "application/json",
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(value["filename"], safe=""),
                "Cache-Control": "private, no-store",
                "X-Content-SHA256": value["sha256"],
            },
        )

    @router.post("/checks", status_code=201)
    def check(
        book_id: str,
        payload: CheckCreate,
        actor=Depends(actor_dependency),
        monthly=Depends(service),
    ):
        return monthly.create_check(actor, book_id, payload)

    @router.post("/checks/{check_id}/close")
    def close(
        book_id: str,
        check_id: str,
        payload: MonthlyReason,
        actor=Depends(actor_dependency),
        monthly=Depends(service),
    ):
        return monthly.close_check(actor, book_id, check_id, payload)

    @router.post("/plans", status_code=201)
    def plan(
        book_id: str, payload: PlanCreate, actor=Depends(actor_dependency), monthly=Depends(service)
    ):
        return monthly.create_plan(actor, book_id, payload)

    @router.post("/plans/{plan_id}/approve")
    def approve(
        book_id: str,
        plan_id: str,
        payload: PlanApproval,
        actor=Depends(actor_dependency),
        monthly=Depends(service),
    ):
        return monthly.approve_plan(actor, book_id, plan_id, payload)

    @router.post("/plans/{plan_id}/generate", status_code=201)
    def generate(
        book_id: str,
        plan_id: str,
        payload: PlanGenerate,
        actor=Depends(actor_dependency),
        monthly=Depends(service),
    ):
        return monthly.generate(actor, book_id, plan_id, payload)

    return router
