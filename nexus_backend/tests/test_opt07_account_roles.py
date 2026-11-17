from uuid import uuid4

from app.accounting.account_roles import AccountRoleService
from app.ledgers.models import EntityCreate
from app.ledgers.service import LedgersService
from app.settlements.models import OpenItemCreate
from app.settlements.service import SettlementService
from test_business import approved, business, handoff  # noqa: F401


def test_account_roles_resolve_non_default_codes(business):
    case = business
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "INSERT INTO accounts(book_id,code,name,category,normal_balance) VALUES(?,?,?,?,?)",
            (case.book["id"], "3202", "应付账款", "liability", "credit"),
        )
        AccountRoleService.set_role(conn, case.book["id"], "3202", "payable")
        assert AccountRoleService.codes(conn, case.book["id"], "payable") == [
            "2202",
            "2241",
            "3202",
        ]
        assert AccountRoleService.codes(conn, case.book["id"], "bank") == ["1002"]


def test_custom_payable_role_is_used_by_business_and_settlement(business):
    case = business
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "INSERT INTO accounts(book_id,code,name,category,normal_balance) VALUES(?,?,?,?,?)",
            (case.book["id"], "3202", "应付账款", "liability", "credit"),
        )
        AccountRoleService.set_role(conn, case.book["id"], "3202", "payable")
        AccountRoleService.set_role(conn, case.book["id"], "2202", "payable", enabled=False)
        AccountRoleService.set_role(conn, case.book["id"], "2241", "payable", enabled=False)
    event = handoff(case, approved(case, template="procurement_expense"))
    draft = case.ledger.get_draft(case.manager, case.book["id"], event["draft_id"])
    assert [line["account_code"] for line in draft["lines"]] == ["6602.02", "3202"]
    party = LedgersService(case.db).create_entity(
        case.manager,
        case.book["id"],
        EntityCreate(kind="counterparty", code="SUPPLIER-ROLE", name="耗材商店"),
    )
    item = SettlementService(case.db).open_item(
        case.actors["accountant"],
        case.book["id"],
        OpenItemCreate(
            source_id=event["source_id"],
            counterparty_id=party["id"],
            expected_draft_version=1,
            idempotency_key=uuid4().hex,
            reason="核对自定义应付科目",
        ),
    )
    assert item["clearing_account"] == "3202"
