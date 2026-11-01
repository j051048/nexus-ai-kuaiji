from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, StrictStr

Period = Annotated[str, Field(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")]


class ReviewPackRequest(BaseModel):
    company_ids: list[StrictStr] = Field(min_length=1, max_length=200)
    period: Period


def build_review_packs_router(actor_dependency):
    router = APIRouter(prefix="/api/firm/review-packs", tags=["批量复核资料"])

    def service(request: Request):
        return request.app.state.review_packs

    @router.post("")
    def build(
        payload: ReviewPackRequest,
        current=Depends(actor_dependency),
        packs=Depends(service),
    ):
        return packs.build(current, payload.company_ids, payload.period)

    return router
