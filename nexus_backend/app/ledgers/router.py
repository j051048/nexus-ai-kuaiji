from fastapi import APIRouter, Depends, Request

from .models import AliasCreate, AliasDecision, EntityCreate, EntityUpdate, InheritanceActivate, ManagerGrant, PeriodCreate, PeriodLink


def build_ledgers_router(actor_dependency):
    router = APIRouter(prefix='/api/books/{book_id}/ledger', tags=['长期账套与企业主数据'])

    def service(request: Request):
        return request.app.state.ledgers

    @router.get('')
    def overview(book_id: str, actor=Depends(actor_dependency), ledgers=Depends(service)):
        return ledgers.overview(actor, book_id)

    @router.post('/managers', status_code=201)
    def manager(book_id: str, payload: ManagerGrant, actor=Depends(actor_dependency), ledgers=Depends(service)):
        return ledgers.grant_manager(actor, book_id, payload)

    @router.post('/periods', status_code=201)
    def period(book_id: str, payload: PeriodCreate, actor=Depends(actor_dependency), ledgers=Depends(service)):
        return ledgers.create_period(actor, book_id, payload)

    @router.post('/periods/link')
    def link(book_id: str, payload: PeriodLink, actor=Depends(actor_dependency), ledgers=Depends(service)):
        return ledgers.link_period(actor, book_id, payload)

    @router.post('/candidates/{candidate_id}/evaluate')
    def candidate(book_id: str, candidate_id: str, payload: InheritanceActivate, actor=Depends(actor_dependency), ledgers=Depends(service)):
        return ledgers.activate_candidate(actor, book_id, candidate_id, payload)

    @router.post('/entities', status_code=201)
    def entity(book_id: str, payload: EntityCreate, actor=Depends(actor_dependency), ledgers=Depends(service)):
        return ledgers.create_entity(actor, book_id, payload)

    @router.put('/entities/{entity_id}')
    def update_entity(book_id: str, entity_id: str, payload: EntityUpdate, actor=Depends(actor_dependency), ledgers=Depends(service)):
        return ledgers.update_entity(actor, book_id, entity_id, payload)

    @router.post('/aliases', status_code=201)
    def alias(book_id: str, payload: AliasCreate, actor=Depends(actor_dependency), ledgers=Depends(service)):
        return ledgers.create_alias(actor, book_id, payload)

    @router.post('/aliases/{alias_id}/review')
    def decision(book_id: str, alias_id: str, payload: AliasDecision, actor=Depends(actor_dependency), ledgers=Depends(service)):
        return ledgers.decide_alias(actor, book_id, alias_id, payload)

    return router
