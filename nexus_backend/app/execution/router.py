"""Execution read/handoff endpoints use the same verified actor as source APIs."""

from typing import Literal

from fastapi import APIRouter, Depends, Request

from .handoff import HandoffRequest, HandoffService


def build_execution_router(actor_dependency):
    router = APIRouter(prefix="/api/executions", tags=["执行记录与人工接手"])

    def service(request: Request):
        state = request.app.state
        return HandoffService(
            state.cases,
            assistant=state.assistant,
            intelligence=state.intelligence,
            intake=state.intake,
        )

    @router.get("/{kind}/{run_id}")
    def detail(
        kind: Literal["chat", "ocr", "journal", "intake"],
        run_id: str,
        actor=Depends(actor_dependency),
        executions=Depends(service),
    ):
        return executions.preview(actor, kind, run_id)

    @router.post("/{kind}/{run_id}/handoff", status_code=201)
    def handoff(
        kind: Literal["chat", "ocr", "journal", "intake"],
        run_id: str,
        body: HandoffRequest,
        actor=Depends(actor_dependency),
        executions=Depends(service),
    ):
        return executions.create(actor, kind, run_id, body)

    return router
