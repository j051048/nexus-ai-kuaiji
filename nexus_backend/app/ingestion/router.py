from fastapi import APIRouter, Depends, Request

from .models import ConnectionCreate


def build_ingestion_router(actor_dependency):
    router = APIRouter(prefix="/api", tags=["数据接入中心"])

    def service(request: Request):
        return request.app.state.ingestion

    @router.get("/firm/ingestion")
    def overview(
        current=Depends(actor_dependency),
        ingestion=Depends(service),
    ):
        return ingestion.list(current)

    @router.post("/firm/ingestion/connections", status_code=201)
    def create_connection(
        payload: ConnectionCreate,
        current=Depends(actor_dependency),
        ingestion=Depends(service),
    ):
        return ingestion.create(current, payload)

    @router.post("/firm/ingestion/connections/{connection_id}/rotate")
    def rotate(
        connection_id: str,
        current=Depends(actor_dependency),
        ingestion=Depends(service),
    ):
        return ingestion.rotate(current, connection_id)

    @router.post("/integrations/webhook/{token}", status_code=202)
    def webhook(token: str, payload: dict, request: Request):
        return request.app.state.ingestion.receive_webhook(token, payload)

    return router
