"""TAX03 transport master data: stable IDs, effective relations and shift assignments."""

import pytest

from app.accounting.domain import AccountingError
from app.ledgers.models import (
    AliasCreate,
    EntityCreate,
    EntityUpdate,
    TransportAliasCreate,
    TransportAssignmentCreate,
    TransportRelationCreate,
    TransportSubjectCreate,
    TransportSubjectUpdate,
)
from app.ledgers.service import LedgersService
from test_business import business  # noqa: F401


def setup(case):
    service = LedgersService(case.db)
    project = service.create_entity(case.manager, case.book["id"], EntityCreate(kind="project", code="TAX03-P1", name="机场接送项目"))
    party = service.create_entity(case.manager, case.book["id"], EntityCreate(kind="counterparty", code="TAX03-C1", name="驾驶员结算主体"))
    vehicle = service.create_transport_subject(case.manager, case.book["id"], TransportSubjectCreate(kind="vehicle", external_id="VIN-001", display_name="京A·12345"))
    driver = service.create_transport_subject(case.manager, case.book["id"], TransportSubjectCreate(kind="driver", external_id="DRV-001", display_name="张师傅"))
    relation = service.create_transport_relation(case.manager, case.book["id"], TransportRelationCreate(
        subject_id=vehicle["id"], target_id=project["id"], contract_external_id="HT-001",
        operating_right="机场专线经营权", valid_from="2026-10-01", valid_to=None,
    ))
    driver_relation = service.create_transport_relation(case.manager, case.book["id"], TransportRelationCreate(
        subject_id=driver["id"], target_id=party["id"], contract_external_id="HT-001",
        operating_right="驾驶服务", valid_from="2026-10-01", valid_to=None,
    ))
    assignment = service.create_transport_assignment(case.manager, case.book["id"], TransportAssignmentCreate(
        vehicle_id=vehicle["id"], driver_id=driver["id"], shift="day",
        contract_external_id="HT-001", valid_from="2026-10-01", valid_to=None,
    ))
    return service, project, party, vehicle, driver, relation, driver_relation, assignment


def test_same_display_name_is_not_a_key_and_alias_resolves_history(business):
    case = business
    service, project, party, vehicle, driver, *_ = setup(case)
    second = service.create_transport_subject(case.manager, case.book["id"], TransportSubjectCreate(kind="vehicle", external_id="VIN-002", display_name="京A·12345"))
    assert second["id"] != vehicle["id"]
    # A display name shared by two vehicles is ambiguous until the stable ID is supplied.
    with pytest.raises(AccountingError) as error:
        service.resolve_transport(case.manager, case.book["id"], "2026-10-03", vehicle_name="京A·12345")
    assert error.value.status == 409
    service.add_transport_alias(case.manager, case.book["id"], TransportAliasCreate(subject_id=vehicle["id"], alias="旧车牌京A12345", source="历史台账"))
    resolved = service.resolve_transport(case.manager, case.book["id"], "2026-10-03", vehicle_name="旧车牌京A12345")
    assert resolved["vehicle"]["id"] == vehicle["id"]
    assert resolved["project"]["id"] == project["id"]


def test_double_shift_allowed_for_vehicle_but_driver_cannot_change_vehicle_overlap(business):
    case = business
    service, project, party, vehicle, driver, *_ = setup(case)
    driver2 = service.create_transport_subject(case.manager, case.book["id"], TransportSubjectCreate(kind="driver", external_id="DRV-002", display_name="李师傅"))
    service.create_transport_assignment(case.manager, case.book["id"], TransportAssignmentCreate(vehicle_id=vehicle["id"], driver_id=driver2["id"], shift="night", contract_external_id="HT-001", valid_from="2026-10-01", valid_to=None))
    second_vehicle = service.create_transport_subject(case.manager, case.book["id"], TransportSubjectCreate(kind="vehicle", external_id="VIN-003", display_name="京A·99999"))
    with pytest.raises(AccountingError) as error:
        service.create_transport_assignment(case.manager, case.book["id"], TransportAssignmentCreate(vehicle_id=second_vehicle["id"], driver_id=driver["id"], shift="day", contract_external_id="HT-002", valid_from="2026-10-02", valid_to=None))
    assert error.value.status == 409
    assert "驾驶员" in str(error.value)


def test_relation_period_is_effective_and_old_history_is_kept(business):
    case = business
    service, project, party, vehicle, driver, relation, driver_relation, assignment = setup(case)
    early = service.resolve_transport(case.manager, case.book["id"], "2026-09-30", vehicle_external_id="VIN-001")
    assert early["project"] is None and early["coverage"]["vehicle_relation"] is False
    current = service.resolve_transport(case.manager, case.book["id"], "2026-10-03", vehicle_external_id="VIN-001", driver_external_id="DRV-001")
    assert current["project"]["id"] == project["id"]
    assert current["counterparty"]["id"] == party["id"]
    assert current["assignment"]["shift"] == "day"
    # A new plate display name does not rewrite the old relation or its source.
    service.update_transport_subject(case.manager, case.book["id"], vehicle["id"], TransportSubjectUpdate(expected_version=1, display_name="京A·54321", active=True, reason="车辆换牌"))
    old = service.resolve_transport(case.manager, case.book["id"], "2026-10-03", vehicle_external_id="VIN-001")
    assert old["vehicle"]["display_name"] == "京A·54321"
    assert old["project"]["id"] == project["id"]
    assert old["vehicle"]["external_id"] == "VIN-001"


def test_cross_company_target_and_subject_cannot_be_linked(business):
    case = business
    service = LedgersService(case.db)
    project = service.create_entity(case.manager, case.book["id"], EntityCreate(kind="project", code="P-LOCAL", name="本地项目"))
    other_vehicle = service.create_transport_subject(case.manager, case.other_book["id"], TransportSubjectCreate(kind="vehicle", external_id="VIN-OTHER", display_name="外部车辆"))
    with pytest.raises(AccountingError) as error:
        service.create_transport_relation(case.manager, case.book["id"], TransportRelationCreate(subject_id=other_vehicle["id"], target_id=project["id"], contract_external_id="X", operating_right="X", valid_from="2026-10-01", valid_to=None))
    assert error.value.status == 404


def test_inactive_project_or_vehicle_cannot_gain_new_effective_relation(business):
    case = business
    service, project, party, vehicle, driver, *_ = setup(case)
    service.update_entity(case.manager, case.book["id"], project["id"], EntityUpdate(expected_version=1, name=project["name"], active=False, reason="项目结束"))
    with pytest.raises(AccountingError) as error:
        service.create_transport_relation(case.manager, case.book["id"], TransportRelationCreate(subject_id=vehicle["id"], target_id=project["id"], contract_external_id="HT-X", operating_right="X", valid_from="2026-11-01", valid_to=None))
    assert error.value.status == 409
    service.update_transport_subject(case.manager, case.book["id"], driver["id"], TransportSubjectUpdate(expected_version=1, display_name=driver["display_name"], active=False, reason="离职停用"))
    with pytest.raises(AccountingError) as error:
        service.create_transport_assignment(case.manager, case.book["id"], TransportAssignmentCreate(vehicle_id=vehicle["id"], driver_id=driver["id"], shift="day", contract_external_id="HT-X", valid_from="2026-11-01", valid_to=None))
    assert error.value.status == 409


def test_alias_is_company_unique_across_subjects(business):
    case = business
    service, project, party, vehicle, driver, *_ = setup(case)
    other = service.create_transport_subject(case.manager, case.book["id"], TransportSubjectCreate(kind="vehicle", external_id="VIN-OTHER", display_name="京B·00001"))
    service.add_transport_alias(case.manager, case.book["id"], TransportAliasCreate(subject_id=vehicle["id"], alias="旧牌-001", source="历史台账"))
    with pytest.raises(AccountingError) as error:
        service.add_transport_alias(case.manager, case.book["id"], TransportAliasCreate(subject_id=other["id"], alias="旧牌-001", source="历史台账"))
    assert error.value.status == 409


def test_http_transport_overview_and_subject_creation(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app

    app = create_app(tmp_path / "tax03-http.db", seed=True)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 45993)) as client:
        token = client.get("/api/session").json()["token"]
        headers = {"Authorization": "Bearer " + token}
        with app.state.service.db.transaction() as conn:
            book_id = conn.execute("SELECT id FROM books ORDER BY name LIMIT 1").fetchone()["id"]
        overview = client.get(f"/api/books/{book_id}/ledger/transport", headers=headers)
        assert overview.status_code == 200, overview.text
        assert overview.json()["subjects"] == []
        created = client.post(
            f"/api/books/{book_id}/ledger/transport/subjects",
            headers=headers,
            json={"kind": "vehicle", "external_id": "HTTP-VIN-1", "display_name": "京A·HTTP01"},
        )
        assert created.status_code == 201, created.text
        after = client.get(f"/api/books/{book_id}/ledger/transport", headers=headers).json()
        assert after["subjects"][0]["external_id"] == "HTTP-VIN-1"
