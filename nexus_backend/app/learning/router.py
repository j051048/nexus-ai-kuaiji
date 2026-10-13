from fastapi import APIRouter, Depends, Query, Request

from .models import RuleCreate, RuleEvaluate, RulePublish, RuleRetire, RuleUpdate


def build_learning_router(actor_dependency):
    router = APIRouter(prefix="/api/books/{book_id}/learning", tags=["审核样例与企业规则"])

    def learning(request: Request):
        return request.app.state.learning

    @router.get("")
    def overview(book_id: str, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=1000000),
                 current=Depends(actor_dependency), service=Depends(learning)):
        return service.overview(current, book_id, limit, offset)

    @router.get("/samples/{sample_id}")
    def sample(book_id: str, sample_id: str, current=Depends(actor_dependency), service=Depends(learning)):
        return service.get_sample(current, book_id, sample_id)

    @router.get('/governance')
    def governance(book_id: str, current=Depends(actor_dependency), service=Depends(learning)):
        return service.governance(current, book_id)

    @router.post("/rules", status_code=201)
    def create(book_id: str, payload: RuleCreate, current=Depends(actor_dependency), service=Depends(learning)):
        return service.save_rule(current, book_id, payload)

    @router.put("/rules/{rule_id}")
    def update(book_id: str, rule_id: str, payload: RuleUpdate, current=Depends(actor_dependency), service=Depends(learning)):
        return service.save_rule(current, book_id, payload, rule_id)

    @router.get("/rules/{rule_id}")
    def rule(book_id: str, rule_id: str, current=Depends(actor_dependency), service=Depends(learning)):
        return service.get_rule(current, book_id, rule_id)

    @router.post("/rules/{rule_id}/evaluate")
    def evaluate(book_id: str, rule_id: str, payload: RuleEvaluate, current=Depends(actor_dependency), service=Depends(learning)):
        return service.evaluate(current, book_id, rule_id, payload)

    @router.post("/rules/{rule_id}/publish")
    def publish(book_id: str, rule_id: str, payload: RulePublish, current=Depends(actor_dependency), service=Depends(learning)):
        return service.publish(current, book_id, rule_id, payload)

    @router.post("/rules/{rule_id}/retire")
    def retire(book_id: str, rule_id: str, payload: RuleRetire, current=Depends(actor_dependency), service=Depends(learning)):
        return service.retire(current, book_id, rule_id, payload)

    return router
