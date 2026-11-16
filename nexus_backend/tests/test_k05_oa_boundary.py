"""K05 boundary: no OA event model exists, so an unbound supplement must fail."""
import pytest

from app.accounting.domain import AccountingError
from app.cases.models import CaseCreate
from app.cases.service import CaseService
from test_intelligence_workflow import ai_case  # noqa: F401


def test_supplement_without_an_approved_business_or_oa_source_is_rejected(ai_case):
    cases = CaseService(ai_case.db, ai_case.business)
    with pytest.raises(AccountingError) as caught:
        cases.create(ai_case.manager, CaseCreate(
            book_id=ai_case.book["id"], draft_id=None, kind="supplement",
            title="OA 只补材料", reason="仅登记 OA 核定后的补件，不重放审批",
            idempotency_key="k05-unbound-supplement-0001",
        ))
    assert caught.value.status == 422
    assert cases.list(ai_case.manager)["total"] == 0


def test_oa_snapshot_is_immutable_and_versioned(ai_case):
    from app.cases.oa_snapshots import OASnapshotService, OASnapshotWrite, OALine

    service = OASnapshotService(ai_case.db)
    body = OASnapshotWrite(
        oa_event_id="OA-K05-001", oa_version=1, period=ai_case.book["period"],
        title="OA 已审只补材料", approved_by="OA 审批人", approved_at="2026-10-12T09:00:00+08:00",
        lines=[OALine(account_code="6602.03", amount_cents=12500, description="办公费")],
    )
    first = service.register(ai_case.manager, ai_case.book["id"], body)
    again = service.register(ai_case.manager, ai_case.book["id"], body)
    assert again["id"] == first["id"] and again["fingerprint"] == first["fingerprint"]
    changed = body.model_copy(update={"lines": [OALine(account_code="6602.03", amount_cents=12600)]})
    with pytest.raises(AccountingError) as caught:
        service.register(ai_case.manager, ai_case.book["id"], changed)
    assert caught.value.status == 409
    assert service.list(ai_case.manager, ai_case.book["id"])["items"][0]["oa_version"] == 1



def test_oa_snapshot_creates_one_material_request_without_replaying_approval(ai_case):
    from app.cases.oa_snapshots import OASnapshotService, OASnapshotWrite, OALine, OAMaterialRequestWrite

    service = OASnapshotService(ai_case.db)
    snap = service.register(ai_case.manager, ai_case.book["id"], OASnapshotWrite(
        oa_event_id="OA-K05-003", oa_version=1, period=ai_case.book["period"],
        title="OA 已审只补材料", approved_by="OA 审批人", approved_at="2026-10-12T09:00:00+08:00",
        lines=[OALine(account_code="6602.03", amount_cents=12500)],
    ))
    body = OAMaterialRequestWrite(snapshot_id=snap["id"], title="只补 OA 材料", reason="OA 已审批，仅补原件材料")
    first = service.create_material_request(ai_case.manager, ai_case.book["id"], body)
    again = service.create_material_request(ai_case.manager, ai_case.book["id"], body)
    assert again["id"] == first["id"]
    items = service.list_material_requests(ai_case.manager, ai_case.book["id"])["items"]
    assert len(items) == 1 and items[0]["snapshot_id"] == snap["id"] and items[0]["status"] == "open"


def test_material_requests_are_visible_only_within_actor_companies(ai_case):
    from app.cases.oa_snapshots import OASnapshotService, OASnapshotWrite, OALine, OAMaterialRequestWrite

    service = OASnapshotService(ai_case.db)
    snap = service.register(ai_case.manager, ai_case.book["id"], OASnapshotWrite(
        oa_event_id="OA-K05-EMP", oa_version=1, period=ai_case.book["period"],
        title="员工差旅补件", approved_by="OA 审批人", approved_at="2026-10-12T09:00:00+08:00",
        lines=[OALine(account_code="6602.03", amount_cents=12500)],
    ))
    service.create_material_request(ai_case.manager, ai_case.book["id"], OAMaterialRequestWrite(
        snapshot_id=snap["id"], title="员工差旅补件", reason="补交审批依据与原始附件",
    ))
    own = service.list_accessible_requests(ai_case.actors["employee"])["items"]
    assert len(own) == 1 and own[0]["title"] == "员工差旅补件"
    assert own[0]["company_name"] and own[0]["book_name"]
