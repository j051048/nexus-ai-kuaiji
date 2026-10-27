import hashlib

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, StrictInt

from .workpaper import TaxWorkpaperService


class TaxProfileWrite(BaseModel):
    book_id: str = Field(min_length=1, max_length=100)
    taxpayer_type: str = Field(min_length=1, max_length=32)
    vat_rate_bp: StrictInt = Field(ge=0, le=100_000)
    surtax_rate_bp: StrictInt = Field(default=0, ge=0, le=100_000)
    filing_cycle: str = Field(default="monthly", max_length=16)
    effective_from: str = Field(min_length=7, max_length=7)
    reason: str = Field(min_length=3, max_length=1000)


class TaxWorkpaperCreate(BaseModel):
    book_id: str = Field(min_length=1, max_length=100)
    period: str | None = Field(default=None, max_length=7)
    reason: str = Field(default="", max_length=1000)


class TaxWorkpaperReview(BaseModel):
    book_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=3, max_length=1000)
    expected_version: StrictInt = Field(ge=1)


def build_tax_router(actor_dependency):
    router = APIRouter(prefix="/api/tax", tags=["B05税务底稿"])

    def service(request: Request) -> TaxWorkpaperService:
        return request.app.state.tax

    @router.put("/profile")
    def write_profile(
        payload: TaxProfileWrite,
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        return tax.set_profile(current, payload.book_id, payload.model_dump())

    @router.get("/profile")
    def read_profile(
        book_id: str = Query(min_length=1, max_length=100),
        period: str | None = Query(default=None, max_length=7),
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        return {"profile": tax.profile(current, book_id, period)}

    @router.post("/workpapers", status_code=201)
    def create_workpaper(
        payload: TaxWorkpaperCreate,
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        return tax.generate(current, payload.book_id, payload.period, payload.reason)

    @router.get("/workpapers")
    def list_workpapers(
        book_id: str = Query(min_length=1, max_length=100),
        period: str | None = Query(default=None, max_length=7),
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        return {"workpapers": tax.list(current, book_id, period)["items"]}

    @router.get("/workpapers/{workpaper_id}")
    def read_workpaper(
        workpaper_id: str,
        book_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        return tax.get(current, book_id, workpaper_id)

    @router.post("/workpapers/{workpaper_id}/review")
    def review_workpaper(
        workpaper_id: str,
        payload: TaxWorkpaperReview,
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        return tax.review(
            current, payload.book_id, workpaper_id, payload.reason, payload.expected_version
        )

    @router.get("/workpapers/{workpaper_id}/csv")
    def export_workpaper(
        workpaper_id: str,
        book_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        body = tax.csv(current, book_id, workpaper_id)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return Response(
            content=body,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="tax-{workpaper_id}.csv"',
                "X-Content-Sha256": digest,
            },
        )

    return router
