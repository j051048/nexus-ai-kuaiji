from period_fixtures import restore_period_state
"""业务权限、审批版本与账务移交的真实事务验证。"""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.accounting.database import Database
from app.accounting.directory import DirectoryService
from app.accounting.domain import Actor, AccountingError
from app.accounting.models import BookCreate, BookMemberGrant, CompanyCreate, CompanyMemberGrant, DepartmentCreate, FirmCreate, FirmMemberGrant
from app.accounting.service import AccountingService
from app.business.models import BusinessEventCreate, BusinessEventUpdate, BusinessPayload, HandoffRequest, ReturnRequest, VersionRequest
from app.business.service import BusinessService


@pytest.fixture
def business(tmp_path):
    db = Database(tmp_path / "business.db")
    directory, ledger, service = DirectoryService(db), AccountingService(db), BusinessService(db)
    def user(name):
        identity = SimpleNamespace(user_id=name, display_name=name, email=f"{name}@example.test")
        directory.register_identity(identity)
        return identity
    owner = user("owner")
    firm = directory.create_firm(owner.user_id, FirmCreate(name="第三阶段试点"))
    manager = Actor(owner.user_id, firm["id"])
    company = directory.create_company(manager, CompanyCreate(name="第一客户企业"))
    other = directory.create_company(manager, CompanyCreate(name="第二客户企业"))
    department = directory.create_department(manager, company["id"], DepartmentCreate(name="业务部"))
    other_department = directory.create_department(manager, company["id"], DepartmentCreate(name="运营部"))
    book = ledger.create_book(manager, BookCreate(name="第一客户账套", period="2026-10", company_id=company["id"]))
    other_book = ledger.create_book(manager, BookCreate(name="第二客户账套", period="2026-10", company_id=other["id"]))
    actors = {}
    for name, role, dept in [("employee", "employee", department["id"]), ("peer", "employee", department["id"]), ("other_employee", "employee", other_department["id"]), ("approver", "employee", department["id"]), ("finance", "finance", other_department["id"]), ("company_admin", "admin", department["id"])]:
        identity = user(name)
        directory.grant_company_member(manager, company["id"], CompanyMemberGrant(email=identity.email, role=role, department_id=dept))
        actors[name] = Actor(name, firm["id"])
    accountant = user("accountant")
    directory.grant_firm_member(manager, FirmMemberGrant(email=accountant.email, role="reviewer"))
    directory.grant_book_member(manager, book["id"], accountant.user_id, BookMemberGrant(role="reviewer"))
    actors["accountant"] = Actor(accountant.user_id, firm["id"])
    directory.grant_book_member(manager, book["id"], "finance", BookMemberGrant(role="editor"))
    service.grant_department_approver(manager, company["id"], department["id"], "approver")
    service.grant_department_approver(manager, company["id"], other_department["id"], "approver")
    return SimpleNamespace(db=db, directory=directory, ledger=ledger, service=service, manager=manager, company=company, other=other, department=department, other_department=other_department, book=book, other_book=other_book, actors=actors)


def payload(**changes):
    return {"occurred_on": "2026-10-03", "description": "垫付办公耗材", "counterparty": "耗材商店", "amount": "0.30", "direction": "expense", "payment_status": "unpaid", "category": "office", **changes}


def create(case, actor="employee", template="reimbursement", **changes):
    return case.service.create(case.actors[actor], BusinessEventCreate(company_id=case.company["id"], template_id=template, payload=payload(**changes)))


def attach(case, event, actor="employee", tag="1"):
    return case.service.add_attachment(case.actors[actor], event["id"], event["version"], {
        "id": "attachment-" + event["id"] + tag, "storage_key": event["id"] + tag,
        "filename": "办公发票.pdf", "media_type": "application/pdf", "sha256": (tag * 64)[:64], "size_bytes": 400,
    })


def submitted(case, actor="employee", template="reimbursement", **changes):
    event = attach(case, create(case, actor, template, **changes), actor)
    return case.service.submit(case.actors[actor], event["id"], event["version"])


def approved(case, template="reimbursement", **changes):
    event = submitted(case, template=template, **changes)
    event = case.service.approve_department(case.actors["approver"], event["id"], event["version"])
    return case.service.approve_finance(case.actors["finance"], event["id"], event["version"])


def handoff(case, event, **changes):
    request = {"expected_version": event["version"], "book_id": case.book["id"], "idempotency_key": "handoff-" + event["id"], **changes}
    return case.service.handoff(case.actors["finance"], event["id"], HandoffRequest(**request))


def test_scope_own_department_company_accounting_never_inherits_admin_approval(business):
    case = business
    first, peer = create(case), create(case, "peer")
    assert first["company_name"] == "第一客户企业"
    assert first["department_name"] == "业务部" and first["applicant_name"] == "employee"
    assert case.service.list_events(case.actors["employee"])["total"] == 1
    assert case.service.list_events(case.actors["approver"], scope="department")["total"] == 2
    assert case.service.list_events(case.actors["finance"], scope="company")["total"] == 2
    assert case.service.list_events(case.actors["accountant"], scope="accounting")["total"] == 0
    for actor in (case.actors["peer"], case.actors["other_employee"], case.actors["company_admin"], case.manager, case.actors["accountant"]):
        with pytest.raises(AccountingError) as error:
            case.service.get_event(actor, first["id"])
        assert error.value.status == 404
    for actor in (case.actors["employee"], case.actors["company_admin"], case.manager):
        with pytest.raises(AccountingError) as error:
            case.service.list_events(actor, scope="company")
        assert error.value.status == 403
    assert case.service.get_event(case.actors["peer"], peer["id"])["permissions"]["edit"]


def test_employee_cannot_choose_department_company_or_applicant(business):
    case = business
    with pytest.raises(AccountingError, match="所属部门"):
        case.service.create(case.actors["employee"], BusinessEventCreate(company_id=case.company["id"], department_id=case.other_department["id"], template_id="reimbursement", payload=payload()))
    with pytest.raises(AccountingError):
        case.service.create(case.actors["employee"], BusinessEventCreate(company_id=case.other["id"], template_id="reimbursement", payload=payload()))
    with pytest.raises(ValidationError):
        BusinessEventCreate(company_id=case.company["id"], template_id="reimbursement", payload=payload(), applicant_id="finance")
    with pytest.raises(AccountingError):
        case.service.create(case.manager, BusinessEventCreate(company_id=case.company["id"], template_id="reimbursement", payload=payload()))
    finance = case.service.create(case.actors["finance"], BusinessEventCreate(company_id=case.company["id"], department_id=case.department["id"], template_id="reimbursement", payload=payload()))
    assert finance["department_id"] == case.department["id"]
    assert finance["applicant_id"] == "finance"


@pytest.mark.parametrize("bad", [0.1, "-1", "1e2", "1.001", "00.30", "0", "9999999999999.99"])
def test_payload_rejects_nonexact_or_nonpositive_money(bad):
    with pytest.raises(ValidationError):
        BusinessPayload(**payload(amount=bad))


@pytest.mark.parametrize("bad", [True, 1.0, "1", 0, -1])
def test_versions_are_strict_integer(bad):
    with pytest.raises(ValidationError):
        VersionRequest(expected_version=bad)


@pytest.mark.parametrize("template,changes", [("reimbursement", {"payment_status": "paid"}), ("reimbursement", {"direction": "income"}), ("procurement_expense", {"direction": "income"}), ("daily_cashflow", {"payment_status": "unpaid"})])
def test_template_constraints_cannot_be_overridden(business, template, changes):
    with pytest.raises(AccountingError, match="不符合模板"):
        create(business, template=template, **changes)


def test_submit_requires_actual_attachment_department_and_eligible_approvers(business):
    case = business
    event = create(case)
    with pytest.raises(AccountingError, match="真实附件"):
        case.service.submit(case.actors["employee"], event["id"], event["version"])
    event = attach(case, event)
    case.service.revoke_department_approver(case.manager, case.company["id"], case.department["id"], "approver")
    with pytest.raises(AccountingError, match="部门审批人"):
        case.service.submit(case.actors["employee"], event["id"], event["version"])
    case.service.grant_department_approver(case.manager, case.company["id"], case.department["id"], "approver")
    case.directory.revoke_company_member(case.manager, case.company["id"], "finance")
    with pytest.raises(AccountingError, match="财务审批人"):
        case.service.submit(case.actors["employee"], event["id"], event["version"])
    case.directory.grant_company_member(case.manager, case.company["id"], CompanyMemberGrant(email="peer@example.test", role="employee"))
    no_department = attach(case, create(case, "peer"), "peer")
    with pytest.raises(AccountingError, match="所属部门"):
        case.service.submit(case.actors["peer"], no_department["id"], no_department["version"])


def test_self_approval_wrong_stage_and_stale_version(business):
    case = business
    case.service.grant_department_approver(case.manager, case.company["id"], case.department["id"], "employee")
    event = submitted(case)
    with pytest.raises(AccountingError, match="本人"):
        case.service.approve_department(case.actors["employee"], event["id"], event["version"])
    with pytest.raises(AccountingError):
        case.service.approve_finance(case.actors["finance"], event["id"], event["version"])
    with pytest.raises(AccountingError, match="版本已更新"):
        case.service.approve_department(case.actors["approver"], event["id"], event["version"] - 1)
    with pytest.raises(AccountingError):
        case.service.update(case.actors["employee"], event["id"], BusinessEventUpdate(expected_version=event["version"], payload=payload(amount="100.00")))
    with pytest.raises(AccountingError):
        attach(case, event, tag="2")
    approved_event = case.service.approve_department(case.actors["approver"], event["id"], event["version"])
    with pytest.raises(AccountingError):
        case.service.approve_department(case.actors["approver"], event["id"], approved_event["version"])


def test_return_keeps_old_payload_attachments_and_action_then_resubmits(business):
    case = business
    first = submitted(case)
    returned = case.service.return_event(case.actors["approver"], first["id"], ReturnRequest(expected_version=first["version"], reason="请补充用途"))
    changed = case.service.update(case.actors["employee"], returned["id"], BusinessEventUpdate(expected_version=returned["version"], payload=payload(amount="1.20", purpose="客户演示耗材")))
    changed = attach(case, changed, tag="2")
    second = case.service.submit(case.actors["employee"], changed["id"], changed["version"])
    assert second["status"] == "submitted"
    assert [v["payload"]["amount"] for v in second["versions"]] == ["0.30", "1.20"]
    assert [len(v["attachment_snapshot"]) for v in second["versions"]] == [1, 2]
    assert second["actions"][0]["reason"] == "请补充用途"
    assert second["actions"][0]["submitted_version"] == first["submitted_version"]
    assert second["submitted_version"] > first["submitted_version"]


def test_attachments_are_private_duplicate_safe_and_revocation_is_immediate(business):
    case = business
    event = attach(case, create(case))
    attachment = event["attachments"][0]
    assert "storage_key" not in str(event)
    assert case.service.download_metadata(case.actors["employee"], attachment["id"])["storage_key"]
    with pytest.raises(AccountingError):
        case.service.download_metadata(case.actors["peer"], attachment["id"])
    with pytest.raises(AccountingError, match="相同内容"):
        attach(case, event)
    assert case.service.get_event(case.actors["employee"], event["id"])["version"] == event["version"]
    case.directory.revoke_company_member(case.manager, case.company["id"], "employee")
    with pytest.raises(AccountingError):
        case.service.download_metadata(case.actors["employee"], attachment["id"])


@pytest.mark.parametrize("template,changes,credit", [("reimbursement", {}, "2241"), ("procurement_expense", {}, "2202"), ("procurement_expense", {"payment_status": "paid"}, "1002"), ("daily_cashflow", {"payment_status": "paid"}, "1002")])
def test_three_templates_handoff_correct_candidate_and_keep_accounting_review(business, template, changes, credit):
    case = business
    event = approved(case, template=template, **changes)
    result = handoff(case, event)
    assert result["event"]["status"] == "handed_off"
    assert result["event"]["version"] == event["version"] + 1
    draft = case.ledger.get_book(case.actors["accountant"], case.book["id"])["drafts"][0]
    assert draft["status"] == "draft"
    assert [(line["account_code"], line["debit"], line["credit"]) for line in draft["lines"]] == [("6602.02", "0.30", "0.00"), (credit, "0.00", "0.30")]
    assert "未判定税额" in draft["explanation"]
    assert case.service.list_events(case.actors["accountant"], scope="accounting")["total"] == 1
    with pytest.raises(AccountingError):
        case.ledger.get_book(case.actors["employee"], case.book["id"])


def test_income_candidate_and_unknown_expense_remain_explainable(business):
    case = business
    income = handoff(case, approved(case, template="daily_cashflow", direction="income", payment_status="paid"))
    unknown = handoff(case, approved(case, category="other"))
    view = case.ledger.get_book(case.actors["accountant"], case.book["id"])
    drafts = {draft["id"]: draft for draft in view["drafts"]}
    assert drafts[income["draft_id"]]["lines"][0]["account_code"] == "1002"
    assert len(drafts[unknown["draft_id"]]["lines"]) == 1
    assert "未形成完整分录，请会计补充对应科目" in drafts[unknown["draft_id"]]["issues"]


def test_handoff_retry_and_concurrency_create_only_one_source_draft_and_mapping(business):
    case = business
    event = approved(case)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: handoff(case, event), range(2)))
    assert results[0]["source_id"] == results[1]["source_id"]
    assert results[0]["draft_id"] == results[1]["draft_id"]
    with case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources WHERE source_kind='business'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM business_mappings").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM business_actions WHERE stage='handoff'").fetchone()[0] == 1
    with pytest.raises(AccountingError, match="不一致"):
        handoff(case, event, idempotency_key="different-key")
    case.directory.revoke_book_member(case.manager, case.book["id"], "finance")
    with pytest.raises(AccountingError):
        handoff(case, event)


def test_handoff_closed_period_cross_company_and_outside_period_roll_back(business):
    case = business
    event = approved(case)
    # Extra book access still cannot transfer business data to another customer.
    case.directory.grant_company_member(case.manager, case.other["id"], CompanyMemberGrant(email="finance@example.test", role="finance"))
    case.directory.grant_book_member(case.manager, case.other_book["id"], "finance", BookMemberGrant(role="editor"))
    with pytest.raises(AccountingError, match="不属于事项"):
        handoff(case, event, book_id=case.other_book["id"])
    with case.db.transaction(write=True) as conn:
        restore_period_state(case.ledger.db, case.book["id"], conn=conn)
    with pytest.raises(AccountingError, match="已关闭"):
        handoff(case, event)
    with case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
    assert case.service.get_event(case.actors["employee"], event["id"])["status"] == "finance_approved"
    with case.db.transaction(write=True) as conn:
        restore_period_state(case.ledger.db, case.book["id"], status="open", conn=conn)
    outside = approved(case, occurred_on="2026-11-01")
    with pytest.raises(AccountingError, match="会计期间"):
        handoff(case, outside)


def test_audit_failure_rolls_back_entire_handoff(business, monkeypatch):
    case = business
    event = approved(case)
    def fail(*args, **kwargs):
        raise RuntimeError("audit write failed")
    monkeypatch.setattr(case.service, "_audit", fail)
    with pytest.raises(RuntimeError, match="audit write failed"):
        handoff(case, event)
    with case.db.transaction() as conn:
        for table in ("sources", "drafts", "draft_lines", "business_mappings"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    current = case.service.get_event(case.actors["finance"], event["id"])
    assert current["version"] == event["version"] and current["status"] == "finance_approved"
    assert [action["stage"] for action in current["actions"]] == ["department", "finance"]


def test_approver_grant_company_ids_and_revocation(business):
    case = business
    with pytest.raises(AccountingError):
        case.service.list_department_approvers(case.manager, case.other["id"], case.department["id"])
    with pytest.raises(AccountingError):
        case.service.grant_department_approver(case.actors["employee"], case.company["id"], case.department["id"], "peer")
    event = submitted(case)
    case.service.revoke_department_approver(case.manager, case.company["id"], case.department["id"], "approver")
    with pytest.raises(AccountingError):
        case.service.approve_department(case.actors["approver"], event["id"], event["version"])
    assert case.service.list_department_approvers(case.manager, case.company["id"], case.department["id"])["approvers"] == []


def test_create_retries_use_initial_normalized_request_after_edits(business):
    case = business
    request = BusinessEventCreate(company_id=case.company["id"], template_id="reimbursement", idempotency_key="employee-create-001", payload=payload(amount="0.3"))
    event = case.service.create(case.actors["employee"], request)
    normalized_retry = BusinessEventCreate(**{**request.model_dump(), "payload": payload(amount="0.30")})
    assert case.service.create(case.actors["employee"], normalized_retry)["id"] == event["id"]
    changed = case.service.update(case.actors["employee"], event["id"], BusinessEventUpdate(expected_version=event["version"], payload=payload(amount="12.34")))
    retry = case.service.create(case.actors["employee"], normalized_retry)
    assert retry["id"] == changed["id"] and retry["version"] == changed["version"]
    assert retry["payload"]["amount"] == "12.34"
    assert "creation_fingerprint" not in retry and "create_key" not in retry
    with pytest.raises(AccountingError, match="原始事项内容不同"):
        case.service.create(case.actors["employee"], request.model_copy(update={"payload": BusinessPayload(**payload(amount="0.40"))}))
    peer = case.service.create(case.actors["peer"], request)
    assert peer["id"] != event["id"] and peer["applicant_id"] == "peer"
    case.directory.revoke_company_member(case.manager, case.company["id"], "employee")
    with pytest.raises(AccountingError):
        case.service.create(case.actors["employee"], request)


def test_concurrent_create_retry_creates_exactly_one_event(business):
    case = business
    request = BusinessEventCreate(company_id=case.company["id"], template_id="reimbursement", idempotency_key="concurrent-create-001", payload=payload())
    with ThreadPoolExecutor(max_workers=4) as executor:
        events = list(executor.map(lambda _: case.service.create(case.actors["employee"], request), range(4)))
    assert len({event["id"] for event in events}) == 1
    with case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM business_events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM audit_events WHERE action='business.create'").fetchone()[0] == 1
