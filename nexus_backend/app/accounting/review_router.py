from fastapi import APIRouter, Depends, Query, Request

from .review_models import ReviewConfirmRequest, ReviewPreviewRequest
from .package_models import PackagePrepare, PackageSelect


def build_review_router(actor_dependency):
    router = APIRouter(prefix="/api/review", tags=["跨企业人工复核"])

    def review(request: Request):
        return request.app.state.review

    def packages(request: Request):
        return request.app.state.review_packages

    @router.post("/packages", status_code=201)
    def prepare_packages(
        payload: PackagePrepare, current=Depends(actor_dependency), service=Depends(packages)
    ):
        return service.prepare(current, payload)

    @router.get("/packages/{preview_id}")
    def read_packages(
        preview_id: str, current=Depends(actor_dependency), service=Depends(packages)
    ):
        return service.get(current, preview_id)

    @router.post("/packages/{preview_id}/selection", status_code=201)
    def select_packages(
        preview_id: str,
        payload: PackageSelect,
        current=Depends(actor_dependency),
        service=Depends(packages),
    ):
        return service.select(current, preview_id, payload)

    @router.post("/packages/{preview_id}/confirm")
    def confirm_packages(
        preview_id: str,
        payload: ReviewConfirmRequest,
        current=Depends(actor_dependency),
        service=Depends(packages),
    ):
        return service.confirm(current, preview_id, payload)

    @router.get("/drafts")
    def drafts(
        company_id: str | None = Query(default=None, min_length=1, max_length=100),
        period: str | None = Query(default=None, pattern=r"^20[0-9]{2}-(?:0[1-9]|1[0-2])$"),
        template: str = Query(default="all", max_length=40),
        risk: str = Query(default="all", max_length=40),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1_000_000),
        book_id: str | None = Query(default=None, min_length=1, max_length=100),
        current=Depends(actor_dependency),
        service=Depends(review),
    ):
        return service.list_drafts(
            current, company_id, period, template, risk, limit, offset, book_id=book_id
        )

    @router.post("/previews", status_code=201)
    def preview(
        payload: ReviewPreviewRequest, current=Depends(actor_dependency), service=Depends(review)
    ):
        return service.preview(current, payload)

    @router.get("/previews/{preview_id}")
    def get_preview(preview_id: str, current=Depends(actor_dependency), service=Depends(review)):
        return service.get_preview(current, preview_id)

    @router.post("/previews/{preview_id}/confirm")
    def confirm(
        preview_id: str,
        payload: ReviewConfirmRequest,
        current=Depends(actor_dependency),
        service=Depends(review),
    ):
        return service.confirm(current, preview_id, payload)

    return router
