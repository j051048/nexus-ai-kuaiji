import pytest

from app.accounting.domain import AccountingError
from app.aliases import AliasCreate, AliasService
from app.ledgers.models import EntityCreate, EntityUpdate, TransportSubjectCreate
from app.ledgers.service import LedgersService
from test_business import business  # noqa: F401


def _vehicle(case, external_id, name):
    return LedgersService(case.db).create_transport_subject(
        case.manager,
        case.book["id"],
        TransportSubjectCreate(kind="vehicle", external_id=external_id, display_name=name),
    )


def test_alias_rejects_conflict_and_respects_window(business):
    case = business
    svc = AliasService(case.db)
    first = _vehicle(case, "ALIAS-1", "京A·A1")
    second = _vehicle(case, "ALIAS-2", "京A·A2")
    company_id = case.book["company_id"]
    svc.add(case.manager, company_id, AliasCreate(subject_type="transport_subject", subject_id=first["id"], alias="旧牌-X", source="测试", effective_from="2026-10-01", effective_to="2026-12-31"))
    with pytest.raises(AccountingError) as error:
        svc.add(case.manager, company_id, AliasCreate(subject_type="transport_subject", subject_id=second["id"], alias="旧牌-X", source="测试", effective_from="2026-11-01"))
    assert error.value.status == 409
    resolved = svc.resolve(case.manager, company_id, "transport_subject", "旧牌-X", "2026-10-15")
    assert resolved["status"] == "resolved"
    assert resolved["subject_id"] == first["id"]
    assert svc.resolve(case.manager, company_id, "transport_subject", "旧牌-X", "2027-01-01")["status"] == "not_found"
    with case.db.transaction(actor=case.manager) as conn:
        assert conn.execute("SELECT COUNT(*) c FROM audit_events WHERE action='alias.create'").fetchone()["c"] == 1


def test_resolver_reports_ambiguous_alias(business):
    from uuid import uuid4
    case = business
    svc = AliasService(case.db)
    first = _vehicle(case, "ALIAS-A1", "京A·B1")
    second = _vehicle(case, "ALIAS-A2", "京A·B2")
    company_id = case.book["company_id"]
    svc.add(case.manager, company_id, AliasCreate(subject_type="transport_subject", subject_id=first["id"], alias="旧牌-AMB", source="测试", effective_from="2026-10-01", effective_to="2026-12-31"))
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "INSERT INTO subject_aliases(id,firm_id,company_id,subject_type,subject_id,alias,source,status,effective_from,effective_to,created_by,created_at) VALUES(?,?,?,?,?,?,?,'approved',?,?,?,?)",
            (uuid4().hex, case.manager.firm_id, company_id, "transport_subject", second["id"], "旧牌-AMB", "历史脏数据", "2026-11-01", "2026-12-31", case.manager.user_id, "2026-11-01T00:00:00+00:00"),
        )
    result = svc.resolve(case.manager, company_id, "transport_subject", "旧牌-AMB", "2026-11-15")
    assert result["status"] == "ambiguous"
    assert set(result["candidates"]) == {first["id"], second["id"]}


def test_counterparty_alias_resolves_only_while_entity_is_active(business):
    case = business
    svc = AliasService(case.db)
    ledger = LedgersService(case.db)
    company_id = case.book["company_id"]
    entity = ledger.create_entity(
        case.manager,
        case.book["id"],
        EntityCreate(kind="counterparty", code="ALIAS-C1", name="虚构客商甲"),
    )
    svc.add(case.manager, company_id, AliasCreate(subject_type="counterparty", subject_id=entity["id"], alias="客商旧称", source="测试", effective_from="2026-10-01"))
    resolved = svc.resolve(case.manager, company_id, "counterparty", "客商旧称", "2026-10-15")
    assert resolved == {"status": "resolved", "subject_id": entity["id"]}
    other = ledger.create_entity(
        case.manager,
        case.book["id"],
        EntityCreate(kind="counterparty", code="ALIAS-C2", name="虚构客商乙"),
    )
    with pytest.raises(AccountingError) as error:
        svc.add(case.manager, company_id, AliasCreate(subject_type="counterparty", subject_id=other["id"], alias="客商旧称", source="测试", effective_from="2026-11-01"))
    assert error.value.status == 409
    ledger.update_entity(
        case.manager,
        case.book["id"],
        entity["id"],
        EntityUpdate(expected_version=1, name="虚构客商甲", active=False, reason="测试停用"),
    )
    assert svc.resolve(case.manager, company_id, "counterparty", "客商旧称", "2026-10-15")["status"] == "not_found"


def test_taxi_alias_write_is_mirrored_to_generic_table(business):
    from app.ledgers.models import TransportAliasCreate
    case = business
    vehicle = _vehicle(case, "ALIAS-W1", "京A·W1")
    LedgersService(case.db).add_transport_alias(
        case.manager,
        case.book["id"],
        TransportAliasCreate(subject_id=vehicle["id"], alias="旧牌-W", source="测试"),
    )
    with case.db.transaction(actor=case.manager) as conn:
        row = conn.execute("SELECT subject_type,status FROM subject_aliases WHERE alias='旧牌-W'").fetchone()
    assert row["subject_type"] == "transport_subject"
    assert row["status"] == "approved"
