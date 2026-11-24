"""TAX03 transport master data on real restricted PostgreSQL."""

import pytest

from app.accounting.domain import AccountingError
from app.ledgers.models import (
    EntityCreate,
    TransportAliasCreate,
    TransportAssignmentCreate,
    TransportRelationCreate,
    TransportSubjectCreate,
)
from app.ledgers.service import LedgersService
from test_tax_workpaper_postgres import case, pg  # noqa: F401
import os

pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="需要显式 AK05 四角色隔离 PostgreSQL 运行器",
)


def seed(case):
    service = LedgersService(case.db)
    project = service.create_entity(case.maker, case.book["id"], EntityCreate(kind="project", code="PG-TAX03-P", name="PG 机场项目"))
    vehicle = service.create_transport_subject(case.maker, case.book["id"], TransportSubjectCreate(kind="vehicle", external_id="PG-VIN-1", display_name="京A·P0001"))
    driver = service.create_transport_subject(case.maker, case.book["id"], TransportSubjectCreate(kind="driver", external_id="PG-DRV-1", display_name="PG 张师傅"))
    service.create_transport_relation(case.maker, case.book["id"], TransportRelationCreate(subject_id=vehicle["id"], target_id=project["id"], contract_external_id="PG-HT-1", operating_right="机场专线", valid_from="2026-10-01", valid_to=None))
    service.create_transport_assignment(case.maker, case.book["id"], TransportAssignmentCreate(vehicle_id=vehicle["id"], driver_id=driver["id"], shift="day", contract_external_id="PG-HT-1", valid_from="2026-10-01", valid_to=None))
    service.add_transport_alias(case.maker, case.book["id"], TransportAliasCreate(subject_id=vehicle["id"], alias="PG-OLD-1", source="历史台账"))
    return service, project, vehicle, driver


def test_transport_relations_assignments_and_alias_on_postgres(case):
    service, project, vehicle, driver = seed(case)
    resolved = service.resolve_transport(case.maker, case.book["id"], "2026-10-03", vehicle_external_id="PG-VIN-1", driver_external_id="PG-DRV-1")
    assert resolved["project"]["id"] == project["id"]
    assert resolved["assignment"]["driver_id"] == driver["id"]
    alias = service.resolve_transport(case.maker, case.book["id"], "2026-10-03", vehicle_name="PG-OLD-1")
    assert alias["vehicle"]["id"] == vehicle["id"]


def test_non_manager_cannot_change_transport_master_data(case):
    service = LedgersService(case.db)
    with pytest.raises(AccountingError) as error:
        service.create_transport_subject(case.viewer, case.book["id"], TransportSubjectCreate(kind="vehicle", external_id="PG-DENIED", display_name="无权车辆"))
    assert error.value.status in {403, 404}
