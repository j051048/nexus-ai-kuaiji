from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from fastapi.responses import JSONResponse
from .models import ConversationCreate, TurnCreate, TurnCancel
from .actions import ActionPrepare, ActionConfirm
from . import analysis, investigation
from ..accounting.domain import AccountingError


class InvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    book_id: str = Field(min_length=1, max_length=100)
    period: str = Field(pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$")


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    book_id: str = Field(min_length=1, max_length=100)
    period: str = Field(pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$")
    kind: Literal["expense_bridge", "invoice_voucher_diff"]
    account_code: str | None = Field(default=None, max_length=20)
    compare_period: str | None = Field(default=None, pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$")
    target_voucher_id: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=50, strict=True, ge=1, le=100)
    offset: int = Field(default=0, strict=True, ge=0, le=1_000_000)
    expected_source_version: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def build_assistant_router(actor_dependency):
    router = APIRouter(prefix="/api/assistant", tags=["会计助手"])

    def service(request: Request):
        return request.app.state.assistant

    def actions(request: Request):
        return request.app.state.assistant_actions

    @router.get("/investigations/capability")
    def investigation_capability(
        request: Request,
        book_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
    ):
        service = request.app.state.service
        with service.db.transaction(actor=current) as conn:
            service.access.book(conn, current, book_id, "read")
        return {"book_id": book_id, "enabled": investigation.enabled()}

    @router.post("/investigations")
    def investigate(
        body: InvestigationRequest,
        request: Request,
        current=Depends(actor_dependency),
    ):
        return investigation.investigate(
            request.app.state.service, current, body.book_id, body.period
        )

    @router.get("/analyses/capability")
    def analysis_capability(
        request: Request,
        book_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
    ):
        service = request.app.state.service
        with service.db.transaction(actor=current) as conn:
            service.access.book(conn, current, book_id, "read")
        return {
            "book_id": book_id,
            "enabled": investigation.enabled(),
            "kinds": list(analysis.KINDS),
            "real_model_evaluated": False,
        }

    @router.post("/analyses")
    def analyses(
        body: AnalysisRequest,
        request: Request,
        current=Depends(actor_dependency),
    ):
        if not investigation.enabled():
            raise AccountingError("只读分析未启用，请联系管理员", 403)
        params = body.model_dump(exclude={"book_id", "period", "kind"}, exclude_none=True)
        return analysis.analyze(
            request.app.state.service, current, body.book_id, body.period, body.kind, **params
        )

    @router.get("/scope")
    def choices(
        company_id: str = Query(..., min_length=1, max_length=100),
        event_q: str = Query("", max_length=200),
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1_000_000),
        current=Depends(actor_dependency),
        assistant=Depends(service),
    ):
        return assistant.choices(current, company_id, event_q, limit, offset)

    @router.get("/conversations")
    def conversations(
        company_id: str = Query(..., min_length=1, max_length=100),
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1_000_000),
        current=Depends(actor_dependency),
        assistant=Depends(service),
    ):
        return assistant.list(current, company_id, limit, offset)

    @router.post("/conversations", status_code=201)
    def create(
        body: ConversationCreate, current=Depends(actor_dependency), assistant=Depends(service)
    ):
        return assistant.create(current, body)

    @router.get("/conversations/{conversation_id}")
    def detail(conversation_id: str, current=Depends(actor_dependency), assistant=Depends(service)):
        return assistant.detail(current, conversation_id)

    @router.get("/conversations/{conversation_id}/workspace")
    def workspace(
        conversation_id: str,
        message_limit: int = Query(100, ge=1, le=100),
        message_offset: int = Query(0, ge=0, le=1_000_000),
        action_limit: int = Query(20, ge=1, le=20),
        action_offset: int = Query(0, ge=0, le=100_000),
        current=Depends(actor_dependency),
        assistant=Depends(service),
        action_service=Depends(actions),
    ):
        return assistant.workspace(
            current,
            conversation_id,
            action_service,
            message_limit=message_limit,
            message_offset=message_offset,
            action_limit=action_limit,
            action_offset=action_offset,
        )

    @router.get("/conversations/{conversation_id}/messages")
    def messages(
        conversation_id: str,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1_000_000),
        current=Depends(actor_dependency),
        assistant=Depends(service),
    ):
        return assistant.messages(current, conversation_id, limit, offset)

    @router.get("/conversations/{conversation_id}/progress")
    def progress(
        conversation_id: str,
        action_offset: int = Query(0, ge=0, le=100_000),
        current=Depends(actor_dependency),
        assistant=Depends(service),
    ):
        return assistant.progress(current, conversation_id, action_offset=action_offset)

    @router.post("/conversations/{conversation_id}/turns", status_code=202)
    def enqueue(
        conversation_id: str,
        body: TurnCreate,
        current=Depends(actor_dependency),
        assistant=Depends(service),
    ):
        return assistant.enqueue(current, conversation_id, body)

    @router.get("/conversations/{conversation_id}/turns/{turn_id}")
    def turn(
        conversation_id: str,
        turn_id: str,
        current=Depends(actor_dependency),
        assistant=Depends(service),
    ):
        return assistant.turn(current, conversation_id, turn_id)

    @router.get("/conversations/{conversation_id}/turns/{turn_id}/execution")
    def execution(
        conversation_id: str,
        turn_id: str,
        current=Depends(actor_dependency),
        assistant=Depends(service),
    ):
        return assistant.execution(current, conversation_id, turn_id)

    @router.get("/conversations/{conversation_id}/turns/{turn_id}/delivery-details")
    def delivery_details(
        conversation_id: str,
        turn_id: str,
        kind: Literal["control", "bank"] = Query("bank"),
        after: str | None = Query(None, min_length=1, max_length=2048),
        limit: int = Query(10, ge=1, le=20),
        current=Depends(actor_dependency),
        assistant=Depends(service),
    ):
        return JSONResponse(
            assistant.delivery_details(
                current, conversation_id, turn_id, kind=kind, after=after, limit=limit
            ),
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/conversations/{conversation_id}/turns/{turn_id}/events")
    def events(
        conversation_id: str,
        turn_id: str,
        after: str | None = Query(None, min_length=1, max_length=2048),
        limit: int = Query(3, ge=1, le=3),
        current=Depends(actor_dependency),
        assistant=Depends(service),
    ):
        return JSONResponse(
            assistant.events(current, conversation_id, turn_id, after=after, limit=limit),
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/conversations/{conversation_id}/turns/{turn_id}/cancel")
    def cancel(
        conversation_id: str,
        turn_id: str,
        body: TurnCancel,
        current=Depends(actor_dependency),
        assistant=Depends(service),
    ):
        return assistant.cancel(current, conversation_id, turn_id, body)

    @router.post("/conversations/{conversation_id}/actions", status_code=201)
    def prepare(
        conversation_id: str,
        body: ActionPrepare,
        current=Depends(actor_dependency),
        action_service=Depends(actions),
    ):
        return action_service.prepare(current, conversation_id, body)

    @router.get("/conversations/{conversation_id}/actions")
    def plans(
        conversation_id: str,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1_000_000),
        current=Depends(actor_dependency),
        action_service=Depends(actions),
    ):
        return action_service.list(current, conversation_id, limit, offset)

    @router.get("/conversations/{conversation_id}/actions/{plan_id}")
    def plan(
        conversation_id: str,
        plan_id: str,
        current=Depends(actor_dependency),
        action_service=Depends(actions),
    ):
        return action_service.get(current, conversation_id, plan_id)

    @router.post("/conversations/{conversation_id}/actions/{plan_id}/confirm")
    def confirm(
        conversation_id: str,
        plan_id: str,
        body: ActionConfirm,
        current=Depends(actor_dependency),
        action_service=Depends(actions),
    ):
        return action_service.confirm(current, conversation_id, plan_id, body)

    @router.get("/conversations/{conversation_id}/actions/{plan_id}/receipt")
    def receipt(
        conversation_id: str,
        plan_id: str,
        current=Depends(actor_dependency),
        action_service=Depends(actions),
    ):
        return action_service.receipt(current, conversation_id, plan_id)

    return router
