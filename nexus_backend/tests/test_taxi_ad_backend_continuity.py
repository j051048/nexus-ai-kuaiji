"""A-D integration acceptance: transport identity must survive accounting output."""

from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ExportRequest
from app.ledgers.models import EntityCreate, EntityUpdate, TransportRelationCreate
from app.ledgers.service import LedgersService
from app.settlements.models import OpenItemCreate
from app.settlements.service import SettlementService
from app.tax.fees import TaxFeeService
from test_business import business  # noqa: F401
from test_tax06_fees import POLICY, ROWS, _masters, _source


def mapped_fee(case):
    ledgers = LedgersService(case.db)
    driver, vehicle, _, _ = _masters(case, with_mapping=False)
    party = ledgers.create_entity(
        case.manager, case.book["id"],
        EntityCreate(kind="counterparty", code="TAXI-DRIVER-1", name="驾驶员结算主体"),
    )
    project = ledgers.create_entity(
        case.manager, case.book["id"],
        EntityCreate(kind="project", code="TAXI-CAR-1", name="单车收入主体"),
    )
    for subject, target in ((driver, party), (vehicle, project)):
        ledgers.create_transport_relation(
            case.manager, case.book["id"], TransportRelationCreate(
                subject_id=subject["id"], target_id=target["id"],
                contract_external_id="TAXI-CONTRACT-1", operating_right="合成试点核定",
                valid_from="2026-10-01", valid_to=None,
            ),
        )
    source = _source(case, rows=ROWS[:2])
    batch = TaxFeeService(case.db).generate(
        case.manager, case.book["id"], period="2026-10",
        source_file_ids=[source["id"]], policy=POLICY,
    )
    return batch, party, project


def test_fee_draft_carries_real_driver_and_vehicle_auxiliaries(business):
    batch, party, project = mapped_fee(business)
    draft = business.ledger.get_draft(business.manager, business.book["id"], batch["draft_id"])
    assert draft["lines"][0]["auxiliary_values"] == {"counterparty": party["name"]}
    assert draft["lines"][1]["auxiliary_values"] == {"project": project["name"]}


def test_fee_receivable_cannot_masquerade_as_business_settlement(business):
    batch, party, _ = mapped_fee(business)
    draft = business.ledger.get_draft(business.manager, business.book["id"], batch["draft_id"])
    # D integration is still absent. Preserve the safe rejection, not a claimed success.
    with pytest.raises(AccountingError, match="当前移交的业务来源"):
        SettlementService(business.db).open_item(
            business.manager, business.book["id"], OpenItemCreate(
                source_id=draft["source_id"], counterparty_id=party["id"],
                expected_draft_version=draft["version"], idempotency_key=uuid4().hex,
                reason="缺少已核定逐驾驶员债权，禁止伪装业务事项",
            ),
        )


def test_fee_requires_effective_master_mapping(business):
    _masters(business, with_mapping=False)
    source = _source(business, rows=ROWS[:2])
    with pytest.raises(AccountingError):
        TaxFeeService(business.db).generate(
            business.manager, business.book["id"], period="2026-10",
            source_file_ids=[source["id"]], policy=POLICY,
        )
    with business.db.transaction(actor=business.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tax_fee_batches WHERE book_id=?", (business.book["id"],)).fetchone()[0] == 0


@pytest.mark.parametrize("stage", ["review", "export"])
def test_fee_master_version_change_invalidates_old_acceptance(business, stage):
    batch, party, _ = mapped_fee(business)
    fees = TaxFeeService(business.db)
    if stage == "export":
        fees.review(business.actors["accountant"], business.book["id"], batch["id"],
                    expected_version=1, reason="独立核对运输映射")
    LedgersService(business.db).update_entity(business.manager, business.book["id"], party["id"],
        EntityUpdate(expected_version=party["version"], name="修正后的核算主体", active=True, reason="来源身份发生变化"))
    assert fees.get(business.manager, business.book["id"], batch["id"])["stale"] is True
    with pytest.raises(AccountingError):
        if stage == "review":
            fees.review(business.actors["accountant"], business.book["id"], batch["id"],
                        expected_version=1, reason="不能接受旧身份版本")
        else:
            business.ledger.export(business.manager, business.book["id"],
                ExportRequest(draft_ids=[batch["draft_id"]], idempotency_key=uuid4().hex))
