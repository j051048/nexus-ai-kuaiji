from fastapi import APIRouter, Depends, Request

from .access_policy import AccessPolicyService, StaffAssignment, AccessReason, ContactAccess


def build_access_policy_router(actor_dependency):
    router = APIRouter(prefix="/api", tags=["岗位范围与期限"])

    def service(request: Request):
        return AccessPolicyService(request.app.state.service.db)

    @router.get("/firm/access-assignments")
    def overview(actor=Depends(actor_dependency), policy=Depends(service)):
        return policy.overview(actor)

    @router.post("/firm/access-assignments", status_code=201)
    def assign(payload: StaffAssignment, actor=Depends(actor_dependency), policy=Depends(service)):
        return policy.assign(actor, payload)

    @router.post("/firm/access-assignments/{identifier}/revoke")
    def revoke(
        identifier: str,
        payload: AccessReason,
        actor=Depends(actor_dependency),
        policy=Depends(service),
    ):
        return policy.revoke(actor, identifier, payload.reason)

    @router.post("/books/{book_id}/contact-access")
    def contact(
        book_id: str,
        payload: ContactAccess,
        actor=Depends(actor_dependency),
        policy=Depends(service),
    ):
        return policy.contact_access(actor, book_id, payload)

    @router.post("/books/{book_id}/contact-access/revoke")
    def contact_revoke(
        book_id: str,
        payload: ContactAccess,
        actor=Depends(actor_dependency),
        policy=Depends(service),
    ):
        return policy.contact_access(actor, book_id, payload, revoke=True)

    @router.get("/my-delivery-packages")
    def packages(actor=Depends(actor_dependency), policy=Depends(service)):
        return policy.recipient_packages(actor)

    @router.get("/companies/{company_id}/departments/{department_id}/expenses")
    def expenses(
        company_id: str,
        department_id: str,
        period: str,
        actor=Depends(actor_dependency),
        policy=Depends(service),
    ):
        return policy.department_expenses(actor, company_id, period, department_id)

    return router
