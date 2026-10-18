from app.ingestion.models import ConnectionCreate
from app.ingestion.service import IngestionService
from test_business import business


def test_ingestion_connection_token_rotation_and_webhook_run(business):
    c = business
    service = IngestionService(c.db, c.service)
    created = service.create(
        c.manager,
        ConnectionCreate(
            company_id=c.company["id"],
            kind="webhook",
            name="客户系统回调",
            config={"schema": "v1"},
        ),
    )
    assert created["token"]
    received = service.receive_webhook(created["token"], {"records": [{"amount": "10.00"}]})
    assert received["status"] == "received"
    overview = service.list(c.manager)
    assert overview["connections"][0]["name"] == "客户系统回调"
    assert overview["runs"][0]["status"] == "received"
    rotated = service.rotate(c.manager, created["id"])
    assert rotated["token"] != created["token"]
