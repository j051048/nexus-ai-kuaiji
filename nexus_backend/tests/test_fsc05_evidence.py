"""FSC05: evidence requirements are company-approved policy, not UI assumptions."""

from datetime import datetime, timedelta, timezone

import pytest

from app.accounting.domain import Actor, AccountingError
from app.fsc.evidence import (
    EvidenceEvaluate, EvidenceFact, EvidencePolicyWrite, EvidenceRequest, ExceptionGrant,
    FscEvidenceService, PolicyConfirm, Requirement,
)
from test_business import business  # noqa: F401
from test_fsc04_contracts import confirmed_profile, second_manager


def policy_payload(**overrides):
    data = {
        "industry_code": "出租车",
        "requirements": [
            Requirement(action="payment", business_type="taxi_fee", evidence_kind="bank_receipt", max_age_days=30),
            Requirement(action="review", business_type="taxi_fee", evidence_kind="independent_review"),
        ],
        "exceptions": [],
    }
    data.update(overrides)
    return EvidencePolicyWrite(**data)


def active_policy(case, **overrides):
    confirmed_profile(case, case.company["id"])
    service = FscEvidenceService(case.db)
    draft = service.create_draft(case.manager, case.company["id"], policy_payload(**overrides))
    service.confirm(second_manager(case), draft["id"], 1)
    service.activate(case.manager, draft["id"], 1)
    return service, draft


def test_evidence_policy_requires_independent_confirmation_and_verified_fact(business):
    case = business
    service = FscEvidenceService(case.db)
    no_policy = service.evaluate(
        case.manager, case.book["id"],
        EvidenceEvaluate(action="payment", business_type="taxi_fee", amount_cents=1000),
    )
    assert no_policy["allowed"] is False
    assert no_policy["blockers"][0]["code"] == "policy_missing"

    confirmed_profile(case, case.company["id"])
    draft = service.create_draft(case.manager, case.company["id"], policy_payload())
    with pytest.raises(AccountingError) as self_confirm:
        service.confirm(case.manager, draft["id"], 1)
    assert self_confirm.value.status == 409
    service.confirm(second_manager(case), draft["id"], 1)
    service.activate(case.manager, draft["id"], 1)

    missing = service.evaluate(
        case.manager, case.book["id"],
        EvidenceEvaluate(action="payment", business_type="taxi_fee", amount_cents=1000),
    )
    assert missing["allowed"] is False
    assert missing["blockers"] == [{"code": "missing", "evidence_kind": "bank_receipt", "message": "缺少必需证据"}]

    pending = service.evaluate(
        case.manager, case.book["id"],
        EvidenceEvaluate(action="payment", business_type="taxi_fee", amount_cents=1000,
                         evidence=[EvidenceFact(evidence_kind="bank_receipt", status="pending")]),
    )
    assert pending["blockers"][0]["code"] == "pending"

    verified = service.evaluate(
        case.manager, case.book["id"],
        EvidenceEvaluate(action="payment", business_type="taxi_fee", amount_cents=1000,
                         evidence=[EvidenceFact(evidence_kind="bank_receipt", status="verified")]),
    )
    assert verified["allowed"] is True
    assert verified["policy_version"] == 1

    with pytest.raises(AccountingError) as unknown:
        service.evaluate(case.manager, case.book["id"],
                         EvidenceEvaluate(action="launch", business_type="taxi_fee"))
    assert unknown.value.status == 422

    with pytest.raises(AccountingError) as denied:
        service.list(case.actors["employee"], case.company["id"])
    assert denied.value.status in {403, 404}
    stranger = Actor(case.manager.user_id, "other-firm")
    with pytest.raises(AccountingError) as cross_firm:
        service.list(stranger, case.company["id"])
    assert cross_firm.value.status == 403


def test_evidence_exception_is_bounded_and_requires_approval(business):
    case = business
    confirmed_profile(case, case.company["id"])
    service = FscEvidenceService(case.db)
    expires = (datetime.now(timezone.utc).date() + timedelta(days=10)).isoformat()
    with pytest.raises(AccountingError) as unapproved:
        service.create_draft(case.manager, case.company["id"], policy_payload(exceptions=[
            ExceptionGrant(action="payment", business_type="taxi_fee", evidence_kind="bank_receipt",
                           reason="客户确认后补", approved_by=" "),
        ]))
    assert unapproved.value.status == 422

    draft = service.create_draft(case.manager, case.company["id"], policy_payload(exceptions=[
        ExceptionGrant(action="payment", business_type="taxi_fee", evidence_kind="bank_receipt",
                       reason="客户确认后补银行回单", approved_by="财务负责人", expires_on=expires,
                       max_amount_cents=100000),
    ]))
    service.confirm(second_manager(case), draft["id"], 1)
    service.activate(case.manager, draft["id"], 1)

    allowed = service.evaluate(
        case.manager, case.book["id"],
        EvidenceEvaluate(action="payment", business_type="taxi_fee", amount_cents=50000),
    )
    assert allowed["allowed"] is True
    assert allowed["exceptions_applied"][0]["approved_by"] == "财务负责人"

    too_large = service.evaluate(
        case.manager, case.book["id"],
        EvidenceEvaluate(action="payment", business_type="taxi_fee", amount_cents=200000),
    )
    assert too_large["allowed"] is False
    assert too_large["blockers"][0]["code"] == "missing"


def test_evidence_expiry_and_cross_scope(business):
    case = business
    service, _ = active_policy(case, requirements=[
        Requirement(action="payment", business_type="taxi_fee", evidence_kind="bank_receipt", max_age_days=30),
    ])
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    expired = service.evaluate(
        case.manager, case.book["id"],
        EvidenceEvaluate(action="payment", business_type="taxi_fee", amount_cents=1000,
                         evidence=[EvidenceFact(evidence_kind="bank_receipt", status="verified", captured_at=old)]),
    )
    assert expired["allowed"] is False
    assert expired["blockers"][0]["code"] == "expired"

    fresh = service.evaluate(
        case.manager, case.book["id"],
        EvidenceEvaluate(action="payment", business_type="taxi_fee", amount_cents=1000,
                         evidence=[EvidenceFact(evidence_kind="bank_receipt", status="verified",
                                                captured_at=datetime.now(timezone.utc))]),
    )
    assert fresh["allowed"] is True

    with pytest.raises(AccountingError) as duplicate:
        service.activate(case.manager, service.list(case.manager, case.company["id"])["items"][0]["id"], 1)
    assert duplicate.value.status == 409

    with pytest.raises(AccountingError) as cross:
        service.evaluate(Actor(case.manager.user_id, "other-firm"), case.book["id"],
                         EvidenceEvaluate(action="payment", business_type="taxi_fee", amount_cents=1))
    assert cross.value.status in {403, 404}


def _contact_plan(case):
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "INSERT INTO service_plan_versions(book_id,firm_id,company_id,version,lead_user_id,"
            "reviewer_user_id,backup_user_id,contact_user_id,due_on,notes,reason,updated_by,updated_at) "
            "VALUES(?,?,?,1,?,NULL,NULL,?,?,?,?,?,?)",
            (case.book["id"], case.manager.firm_id, case.company["id"], case.manager.user_id,
             case.actors["finance"].user_id, "2026-11-20", "FSC05 补件测试",
             "测试补件联系人", case.manager.user_id,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )


def test_missing_evidence_opens_one_material_request_and_reuses_it(business):
    case = business
    service, draft = active_policy(case)
    request = EvidenceRequest(action="payment", business_type="taxi_fee", amount_cents=1000)
    with pytest.raises(AccountingError) as no_contact:
        service.request_missing(case.manager, case.book["id"], request)
    assert no_contact.value.status == 409
    assert "资料联系人" in str(no_contact.value)

    _contact_plan(case)
    opened = service.request_missing(case.manager, case.book["id"], request)
    assert opened["created"] == 1 and opened["reused"] == 0
    assert opened["policy_version"] == 1
    assert opened["requests"][0]["assignee_user_id"] == case.actors["finance"].user_id
    with case.db.transaction(actor=case.manager) as conn:
        rows = conn.execute(
            "SELECT r.title,r.period,l.policy_id,l.evidence_kind,r.assignee_user_id "
            "FROM material_requests r JOIN fsc_evidence_request_links l ON l.request_id=r.id "
            "WHERE r.book_id=?", (case.book["id"],)
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["policy_id"] == draft["id"]
    assert rows[0]["evidence_kind"] == "bank_receipt"
    assert rows[0]["period"] == case.book["period"]
    assert rows[0]["assignee_user_id"] == case.actors["finance"].user_id

    again = service.request_missing(case.manager, case.book["id"], request)
    assert again["created"] == 0 and again["reused"] == 1
    with case.db.transaction(actor=case.manager) as conn:
        count = conn.execute("SELECT count(*) FROM material_requests WHERE book_id=?", (case.book["id"],)).fetchone()[0]
    assert count == 1

    with pytest.raises(AccountingError) as denied:
        service.request_missing(case.actors["employee"], case.book["id"], request)
    assert denied.value.status in {403, 404}

    satisfied = service.evaluate(
        case.manager, case.book["id"],
        EvidenceEvaluate(action="payment", business_type="taxi_fee", amount_cents=1000,
                         evidence=[EvidenceFact(evidence_kind="bank_receipt", status="verified")]),
    )
    assert satisfied["allowed"] is True
