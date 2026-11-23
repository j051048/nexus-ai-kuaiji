"""HTTP surface for the group management consolidation worksheet (Batch 10-1.2)."""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, StrictInt

from ..accounting.models import InputModel


class PartyWrite(InputModel):
    company_id: str = Field(min_length=1, max_length=100)
    party_key: str = Field(min_length=2, max_length=120)
    party_name: str = Field(min_length=1, max_length=120)
    counterparty_company_id: str = Field(min_length=1, max_length=100)
    status: Literal["active", "inactive"] = "active"


class EliminationWrite(InputModel):
    company_id: str = Field(min_length=1, max_length=100)
    counterparty_company_id: str = Field(min_length=1, max_length=100)
    period: str = Field(pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$")
    kind: Literal[
        "internal_receivable",
        "internal_payable",
        "internal_revenue",
        "internal_cost",
        "internal_funding",
    ]
    amount_cents: StrictInt = Field(gt=0, le=10**15)
    note: str = Field(default="", max_length=500)


class EliminationReview(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=500)


def _company_ids(value: str):
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def build_consolidation_router(actor_dependency):
    router = APIRouter(prefix="/api/consolidation", tags=["集团管理合并试算"])

    def service(request: Request):
        return request.app.state.consolidation

    @router.get("/parties")
    def parties(
        company_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
        consolidation=Depends(service),
    ):
        return consolidation.parties(current, company_id)

    @router.put("/parties")
    def save_party(
        payload: PartyWrite,
        current=Depends(actor_dependency),
        consolidation=Depends(service),
    ):
        return consolidation.save_party(
            current,
            payload.company_id,
            payload.party_key,
            payload.party_name,
            payload.counterparty_company_id,
            payload.status,
        )

    @router.get("/worksheet")
    def worksheet(
        period: str = Query(pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$"),
        company_ids: str = Query(min_length=1, max_length=4000),
        current=Depends(actor_dependency),
        consolidation=Depends(service),
    ):
        return consolidation.worksheet(current, period, _company_ids(company_ids))

    @router.get("/eliminations")
    def eliminations(
        period: str = Query(pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$"),
        company_ids: str = Query(min_length=1, max_length=4000),
        current=Depends(actor_dependency),
        consolidation=Depends(service),
    ):
        return consolidation.eliminations(current, period, _company_ids(company_ids))

    @router.post("/eliminations")
    def create_elimination(
        payload: EliminationWrite,
        current=Depends(actor_dependency),
        consolidation=Depends(service),
    ):
        return consolidation.create_elimination(
            current,
            payload.company_id,
            payload.counterparty_company_id,
            payload.period,
            payload.kind,
            payload.amount_cents,
            payload.note,
        )

    @router.post("/eliminations/{elimination_id}/review")
    def review_elimination(
        elimination_id: str,
        payload: EliminationReview,
        current=Depends(actor_dependency),
        consolidation=Depends(service),
    ):
        return consolidation.review_elimination(
            current, elimination_id, payload.approve, payload.note
        )

    return router
