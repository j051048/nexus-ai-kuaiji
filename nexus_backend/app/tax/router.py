import hashlib
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .workpaper import TaxWorkpaperService
from .context import tax_context


class TaxProfileWrite(BaseModel):
    book_id: str = Field(min_length=1, max_length=100)
    taxpayer_type: str = Field(min_length=1, max_length=32)
    vat_rate_bp: StrictInt = Field(ge=0, le=100_000)
    surtax_rate_bp: StrictInt = Field(default=0, ge=0, le=100_000)
    filing_cycle: str = Field(default="monthly", max_length=16)
    effective_from: str = Field(pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$")
    reason: str = Field(min_length=3, max_length=1000)


class TaxAccountRole(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1, max_length=100)
    account_version: StrictInt = Field(ge=1)
    role: Literal["vat_payable", "excluded"]


class TaxWorkpaperCreate(BaseModel):
    book_id: str = Field(min_length=1, max_length=100)
    period: str | None = Field(default=None, pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$")
    reason: str = Field(default="", max_length=1000)
    vat_accounts: list[TaxAccountRole] = Field(default_factory=list, max_length=1000)


class TaxWorkpaperReview(BaseModel):
    book_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=3, max_length=1000)
    expected_version: StrictInt = Field(ge=1)


def build_tax_router(actor_dependency):
    router = APIRouter(prefix="/api/tax", tags=["B05税务底稿"])

    def service(request: Request) -> TaxWorkpaperService:
        return request.app.state.tax

    @router.get("/context")
    def preparation_context(
        book_id: str = Query(min_length=1, max_length=100),
        period: str | None = Query(default=None, max_length=7),
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        return tax_context(tax, current, book_id, period)

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
        return tax.generate(
            current,
            payload.book_id,
            payload.period,
            payload.reason,
            [item.model_dump() for item in payload.vat_accounts],
        )

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
        expected_version: int = Query(ge=1),
        source_digest: str = Query(pattern=r"^[0-9a-f]{64}$"),
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        body = tax.csv(current, book_id, workpaper_id, expected_version, source_digest)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return Response(
            content=body,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="tax-{workpaper_id}.csv"',
                "X-Content-Sha256": digest,
                "X-Workpaper-Version": str(expected_version),
                "X-Source-Sha256": source_digest,
                "Cache-Control": "no-store",
            },
        )

    return router
