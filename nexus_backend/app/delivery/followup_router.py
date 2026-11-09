"""Firm-level feed of monthly delivery items that still need a human step."""

from fastapi import APIRouter, Depends, Query, Request


def build_delivery_followup_router(actor_dependency):
    router = APIRouter(prefix="/api/delivery", tags=["月度交付跟进"])

    @router.get("/followups")
    def feed(
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=1_000_000),
        actor=Depends(actor_dependency),
    ):
        return request.app.state.delivery_followups.followups(actor, limit, offset)

    return router
