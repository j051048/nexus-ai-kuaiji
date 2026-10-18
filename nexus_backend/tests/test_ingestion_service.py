import pytest

from app.accounting.admin_models import AdminCompanyStatus
from app.accounting.admin_service import AdminService
from app.accounting.domain import AccountingError
from app.ingestion.models import ConnectionCreate
from app.ingestion.service import IngestionService
from test_business import business


def test_ingestion_connection_token_rotation_and_webhook_run(business):
    c = business
    service = IngestionService(c.db, c.service, enabled=True)
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
    received = service.receive_webhook(
        created["token"], {"records": [{"amount": "10.00"}]}, event_key="evt-1"
    )
    assert received["status"] == "received" and received["duplicate"] is False
    overview = service.list(c.manager)
    assert overview["connections"][0]["name"] == "客户系统回调"
    assert overview["runs"][0]["status"] == "received"
    with c.db.transaction(actor=c.manager) as conn:
        raw = conn.execute(
            "SELECT raw_event_json,request_hash,event_key FROM ingestion_runs WHERE id=?",
            (received["run_id"],),
        ).fetchone()
    assert "10.00" in raw["raw_event_json"]
    assert raw["event_key"] == "evt-1" and len(raw["request_hash"]) == 64
    rotated = service.rotate(c.manager, created["id"])
    assert rotated["token"] != created["token"]
    with pytest.raises(AccountingError) as stale:
        service.receive_webhook(created["token"], {"records": []}, event_key="evt-2")
    assert stale.value.status == 404


def test_ingestion_is_disabled_by_default_and_rejects_suspended_company(business):
    c = business
    disabled = IngestionService(c.db, c.service)
    with pytest.raises(AccountingError) as off:
        disabled.receive_webhook("token", {"records": []}, event_key="evt-off")
    assert off.value.status == 404

    service = IngestionService(c.db, c.service, enabled=True)
    created = service.create(
        c.manager,
        ConnectionCreate(company_id=c.company["id"], kind="webhook", name="暂停企业回调", config={}),
    )
    with c.db.transaction(actor=c.manager) as conn:
        version = conn.execute(
            "SELECT version FROM companies WHERE id=?", (c.company["id"],)
        ).fetchone()["version"]
    AdminService(c.db).change_status(
        c.manager,
        c.company["id"],
        AdminCompanyStatus(
            status="suspended",
            reason="外部接入测试：企业停用后不应继续接收数据",
            expected_version=version,
        ),
    )
    with pytest.raises(AccountingError) as suspended:
        service.receive_webhook(created["token"], {"records": []}, event_key="evt-suspended")
    assert suspended.value.status == 409


def test_ingestion_duplicate_event_is_receipted_once(business):
    c = business
    service = IngestionService(c.db, c.service, enabled=True)
    created = service.create(
        c.manager,
        ConnectionCreate(company_id=c.company["id"], kind="webhook", name="幂等回调", config={}),
    )
    first = service.receive_webhook(created["token"], {"records": [{"id": 1}]}, event_key="evt-dup")
    second = service.receive_webhook(created["token"], {"records": [{"id": 1}]}, event_key="evt-dup")
    assert second["run_id"] == first["run_id"] and second["duplicate"] is True
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) AS count FROM ingestion_runs WHERE connection_id=?",
                (created["id"],),
            ).fetchone()["count"]
            == 1
        )


def test_ingestion_rejects_unknown_token_and_oversized_event(business):
    c = business
    service = IngestionService(c.db, c.service, enabled=True)
    with pytest.raises(AccountingError) as unknown:
        service.receive_webhook("not-a-token", {"records": []}, event_key="evt-bad")
    assert unknown.value.status == 404
    created = service.create(
        c.manager,
        ConnectionCreate(company_id=c.company["id"], kind="webhook", name="超限回调", config={}),
    )
    with pytest.raises(AccountingError) as oversized:
        service.receive_webhook(
            created["token"],
            {"records": [{"blob": "x" * (256 * 1024)}]},
            event_key="evt-large",
        )
    assert oversized.value.status == 413


def test_ingestion_without_event_key_uses_content_hash_and_is_idempotent(business):
    c = business
    service = IngestionService(c.db, c.service, enabled=True)
    created = service.create(
        c.manager,
        ConnectionCreate(company_id=c.company["id"], kind="webhook", name="内容幂等", config={}),
    )
    payload = {"records": [{"id": 2}]}
    first = service.receive_webhook(created["token"], payload)
    second = service.receive_webhook(created["token"], payload)
    assert second["run_id"] == first["run_id"] and second["duplicate"] is True
