from fastapi import APIRouter, Depends, Request

from ..accounting.dependencies import actor_dependency
from .profiles import FscProfileService, ProfileConfirm, ProfileWrite


def build_fsc_router(service: FscProfileService):
    router = APIRouter(prefix="/api/fsc", tags=["fsc"])

    @router.get("/profiles/matrix")
    def matrix(actor=Depends(actor_dependency)):
        return service.matrix(actor)

    @router.get("/profiles/active")
    def active(company_id: str, actor=Depends(actor_dependency)):
        return {"profile": service.active(actor, company_id)}

    @router.get("/profiles")
    def list_profiles(company_id: str, actor=Depends(actor_dependency)):
        return service.list_profiles(actor, company_id)

    @router.post("/profiles")
    def create(company_id: str, payload: ProfileWrite, actor=Depends(actor_dependency)):
        return service.create_draft(actor, company_id, payload)

    @router.post("/profiles/{profile_id}/confirm")
    def confirm(profile_id: str, payload: ProfileConfirm, actor=Depends(actor_dependency)):
        return service.confirm(actor, profile_id, payload)

    return router
