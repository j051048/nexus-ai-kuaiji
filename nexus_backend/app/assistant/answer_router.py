"""PLT08 调查回答与受控记忆的 HTTP 入口。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from . import memory

PERIOD_PATTERN = r"^20[0-9]{2}-(0[1-9]|1[0-2])$"


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["why_not_delivered", "revenue_mismatch", "number_origin"]
    book_id: str = Field(min_length=1, max_length=100)
    period: str = Field(pattern=PERIOD_PATTERN)
    settlement_source_id: str | None = Field(default=None, max_length=100)
    bank_source_id: str | None = Field(default=None, max_length=100)
    fee_source_id: str | None = Field(default=None, max_length=100)
    account_code: str | None = Field(default=None, max_length=20)
    narrative: str | None = Field(default=None, max_length=2000)


class RuleProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_id: str = Field(min_length=1, max_length=100)
    rule_key: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=2, max_length=60)
    payload: dict = Field(default_factory=dict)
    effective_from: str | None = Field(default=None, max_length=40)
    expires_at: str | None = Field(default=None, max_length=40)


class RuleAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=500)


class PreferenceWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_id: str = Field(min_length=1, max_length=100)
    key: str = Field(min_length=1, max_length=60)
    value: str = Field(min_length=1, max_length=60)


class CorrectionSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_id: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=2, max_length=500)


def build_plt08_router(actor_dependency):
    router = APIRouter(prefix="/api/assistant", tags=["调查回答与受控记忆"])

    def store(request: Request):
        return request.app.state.assistant_answers

    def database(request: Request):
        return request.app.state.service.db

    @router.post("/answers/preview")
    def preview_answer(payload: AnswerRequest, request: Request, current=Depends(actor_dependency)):
        """只跑调查路径、不落库：用于确认结论与依据，零持久化副作用。"""
        return store(request).preview(current, payload.model_dump())

    @router.post("/answers")
    def create_answer(payload: AnswerRequest, request: Request, current=Depends(actor_dependency)):
        return store(request).investigate(current, payload.model_dump())

    @router.get("/answers")
    def list_answers(
        request: Request,
        book_id: str = Query(min_length=1, max_length=100),
        period: str = Query(default="", max_length=7),
        current=Depends(actor_dependency),
    ):
        return {
            "items": store(request).list_answers(current, book_id, period),
        }

    @router.get("/answers/{answer_id}")
    def get_answer(answer_id: str, request: Request, current=Depends(actor_dependency)):
        return store(request).read(current, answer_id)

    @router.post("/answers/{answer_id}/reinvestigate")
    def reinvestigate(answer_id: str, request: Request, current=Depends(actor_dependency)):
        return store(request).reinvestigate(current, answer_id)

    @router.get("/preferences")
    def list_preferences(
        request: Request,
        company_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
    ):
        return {"items": memory.list_preferences(current, company_id, db=database(request))}

    @router.put("/preferences")
    def set_preference(
        payload: PreferenceWrite, request: Request, current=Depends(actor_dependency)
    ):
        return memory.set_preference(
            current,
            payload.company_id,
            payload.key,
            payload.value,
            db=database(request),
        )

    @router.delete("/preferences")
    def delete_preference(
        request: Request,
        company_id: str = Query(min_length=1, max_length=100),
        key: str = Query(min_length=1, max_length=60),
        current=Depends(actor_dependency),
    ):
        return memory.delete_preference(current, company_id, key, db=database(request))

    @router.get("/rules")
    def list_rules(
        request: Request,
        company_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
    ):
        return {"items": memory.list_rules(current, company_id, db=database(request))}

    @router.post("/rules")
    def propose_rule(payload: RuleProposal, request: Request, current=Depends(actor_dependency)):
        return memory.propose_rule(
            current,
            payload.company_id,
            payload.rule_key,
            payload.title,
            payload.payload,
            db=database(request),
            effective_from=payload.effective_from,
            expires_at=payload.expires_at,
        )

    @router.post("/rules/suggest")
    def suggest_rule(
        payload: CorrectionSuggestion, request: Request, current=Depends(actor_dependency)
    ):
        del request, current
        return memory.suggest_rule_from_correction(
            {"note": payload.note}, company_id=payload.company_id
        )

    @router.post("/rules/{proposal_id}/approve")
    def approve_rule(proposal_id: str, request: Request, current=Depends(actor_dependency)):
        return memory.approve_rule(current, proposal_id, db=database(request))

    @router.post("/rules/{proposal_id}/retire")
    def retire_rule(
        proposal_id: str,
        payload: RuleAction,
        request: Request,
        current=Depends(actor_dependency),
    ):
        return memory.retire_rule(current, proposal_id, payload.reason, db=database(request))

    return router
