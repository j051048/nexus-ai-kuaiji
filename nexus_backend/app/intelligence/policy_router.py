from fastapi import APIRouter, Depends, Request

from .endpoints import CompanyPolicyUpdate, EndpointCreate


def build_ai_policy_router(actor_dependency):
    router = APIRouter(prefix="/api", tags=["B05 AI端点与政策"])

    def service(request: Request):
        return request.app.state.ai_policy

    @router.get("/operations/ai/endpoints")
    def list_endpoints(current=Depends(actor_dependency), policy=Depends(service)):
        return {"endpoints": policy.list_endpoints(current)}

    @router.post("/operations/ai/endpoints", status_code=201)
    def create_endpoint(
        payload: EndpointCreate, current=Depends(actor_dependency), policy=Depends(service)
    ):
        return policy.create_endpoint(current, payload)

    @router.get("/companies/{company_id}/ai-policy")
    def get_policy(company_id: str, current=Depends(actor_dependency), policy=Depends(service)):
        return policy.policy(current, company_id)

    @router.put("/companies/{company_id}/ai-policy")
    def update_policy(
        company_id: str,
        payload: CompanyPolicyUpdate,
        current=Depends(actor_dependency),
        policy=Depends(service),
    ):
        return policy.update_policy(current, company_id, payload)

    return router
