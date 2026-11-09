"""Populated assets and settlements resumed through real HTTP after recovery."""

from uuid import uuid4
from app.accounting.directory import DirectoryService
from app.accounting.models import BookCreate, BookMemberGrant
from app.accounting.service import AccountingService
from app.ledgers.models import EntityCreate
from app.ledgers.service import LedgersService
from app.opening.models import AccountCreate
from app.opening.service import OpeningService
from app.periodic.models import DepreciableAsset, AssetReconcile
from app.periodic.service import PeriodicService
from app.posting.models import ManualLine, ManualVoucher
from app.posting.service import PostingService
from app.subledger.models import (
    ControlAccountCreate,
    OpenItemCreate,
    AllocationCreate,
    AllocationPart,
    AllocationReverse,
)
from app.subledger.service import SubledgerService
from scripts.recovery_finance_contract import TABLE_FIELDS
from review_policy_fixtures import post_reviewed_manual


def seed_finance_recovery(db, owner, reviewer, company_id):
    from p01_recovery_business import _zero_formal_opening

    book = AccountingService(db).create_book(
        owner, BookCreate(name="虚构资产核销恢复办理", period="2026-10", company_id=company_id)
    )
    DirectoryService(db).grant_book_member(
        owner, book["id"], reviewer.user_id, BookMemberGrant(role="reviewer")
    )
    _zero_formal_opening(db, owner, reviewer, book)
    with db.transaction(actor=owner) as conn:
        ledger = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book["id"],)
        ).fetchone()[0]
        accounts = {
            r["code"]: r["account_id"]
            for r in conn.execute(
                "SELECT v.code,v.account_id FROM ledger_account_versions v JOIN ledger_accounts a ON a.id=v.account_id WHERE a.ledger_id=?",
                (ledger,),
            )
        }
    for code, name, category, normal in [
        ("6602.02", "虚构费用", "expense", "debit"),
        ("1602", "虚构累计折旧", "asset", "credit"),
    ]:
        accounts[code] = OpeningService(db).create_account(
            owner,
            AccountCreate(
                ledger_id=ledger,
                code=code,
                name=name,
                category=category,
                normal_balance=normal,
                effective_from="2026-01",
                reason="恢复后继续办理合成配置",
            ),
        )["id"]
    periodic = PeriodicService(db)
    asset = periodic.create_asset(
        owner,
        DepreciableAsset(
            book_id=book["id"],
            kind="fixed",
            name="恢复合成设备",
            cost="100.00",
            residual="0.00",
            life_months=3,
            start_period="2026-10",
            expense_account_id=accounts["6602.02"],
            expense_account_version=1,
            accum_account_id=accounts["1602"],
            accum_account_version=1,
            reason="恢复前原始资产依据",
        ),
    )
    view = periodic.asset_reconciliation(owner, asset["id"], book["id"])
    draft = periodic.reconcile_asset(
        owner,
        asset["id"],
        AssetReconcile(
            book_id=book["id"],
            expected_fingerprint=view["fingerprint"],
            reason="恢复前明确生成未审核草稿",
        ),
    )
    posting = PostingService(db)

    def posted(description, debit, credit, amount):
        return post_reviewed_manual(
            posting,
            owner,
            ManualVoucher(
                book_id=book["id"],
                command_key=uuid4().hex,
                voucher_date="2026-10-20",
                description=description,
                lines=[
                    ManualLine(account_id=accounts[debit], account_version=1, debit=amount),
                    ManualLine(account_id=accounts[credit], account_version=1, credit=amount),
                ],
            ),
        )

    payable = posted("恢复合成应付原记录", "6602.02", "2202", "10000.00")
    payment = posted("恢复合成部分付款", "2202", "1002", "3000.00")
    sub = SubledgerService(db)
    sub.configure_control_account(
        owner,
        ControlAccountCreate(
            book_id=book["id"],
            kind="payable",
            control_account_id=accounts["2202"],
            control_account_version=1,
            effective_from="2026-01",
            reason="恢复应付控制科目",
        ),
    )
    party = LedgersService(db).create_entity(
        owner, book["id"], EntityCreate(kind="counterparty", code="RECOVERY", name="虚构恢复供应方")
    )
    item = sub.create_item(
        owner,
        OpenItemCreate(
            book_id=book["id"],
            kind="payable",
            counterparty_id=party["id"],
            control_account_id=accounts["2202"],
            control_account_version=1,
            original_date="2026-10-20",
            amount="10000.00",
            source_kind="voucher",
            source_id=payable["voucher"]["id"],
            reason="绑定恢复前应付凭证",
        ),
    )
    request = AllocationCreate(
        book_id=book["id"],
        voucher_id=payment["voucher"]["id"],
        parts=[AllocationPart(item_id=item["id"], amount="3000.00")],
        idempotency_key=uuid4().hex,
        expected_fingerprint=sub.item_workspace(owner, book["id"], item["id"])["fingerprint"],
        reason="恢复前核销",
    )
    allocation = sub.allocate(owner, request)["allocations"][0]
    sub.reverse_allocation(
        owner,
        allocation["id"],
        AllocationReverse(
            idempotency_key=uuid4().hex,
            reason="恢复前撤回核销以便后续核对",
            expected_fingerprint=sub.item_workspace(owner, book["id"], item["id"])["fingerprint"],
        ),
    )
    return {
        "book_id": book["id"],
        "company_id": company_id,
        "asset_id": asset["id"],
        "draft_id": draft["draft"]["id"],
        "asset_fingerprint": view["fingerprint"],
        "item_id": item["id"],
        "voucher_id": payment["voucher"]["id"],
        "original_allocation_id": allocation["id"],
    }


def verify_finance_recovery_http(client, headers, evidence):
    from p01_recovery_business import _response

    def call(method, path, body=None, actor="owner", expected=200):
        return _response(client, method, path, headers[actor], body, expected).json()

    book = evidence["book_id"]
    asset = evidence["asset_id"]
    draft = evidence["draft_id"]
    item = evidence["item_id"]
    path = f"/api/periodic/assets/{asset}/reconciliation?book_id={book}"
    current = call("GET", path)
    assert (
        current["fingerprint"] == evidence["asset_fingerprint"] and current["amount_cents"] == 3333
    )
    workspace = call("GET", f"/api/periodic/workspace?book_id={book}")
    pending = next(row["draft"] for row in workspace["drafts"] if row["draft"]["id"] == draft)
    assert pending["status"] == "draft" and pending["posted_voucher_id"] is None
    for role in ("employee", "outsider"):
        assert client.get(path, headers=headers[role]).status_code in (403, 404)
    assert (
        client.post(
            f"/api/periodic/drafts/{draft}/approve",
            headers=headers["owner"],
            json={"expected_version": pending["version"], "reason": "不得自我复核"},
        ).status_code
        == 403
    )
    approved = call(
        "POST",
        f"/api/periodic/drafts/{draft}/approve",
        {"expected_version": pending["version"], "reason": "恢复后独立核对原始资产"},
        actor="reviewer",
    )
    posted = call(
        "POST",
        f"/api/periodic/drafts/{draft}/post",
        {
            "expected_version": approved["draft"]["version"],
            "command_key": uuid4().hex,
            "voucher_date": "2026-10-28",
            "reason": "恢复后已独立复核再记账",
        },
    )
    assert posted["draft"]["status"] == "posted"
    current = call("GET", f"/api/subledger/items/{item}/workspace?book_id={book}")
    assert (
        current["item"]["remaining_cents"] == 1000000 and current["allocations"][0]["reversal_id"]
    )
    request = {
        "book_id": book,
        "voucher_id": evidence["voucher_id"],
        "parts": [{"item_id": item, "amount": "2000.00"}],
        "idempotency_key": uuid4().hex,
        "expected_fingerprint": current["fingerprint"],
        "reason": "恢复后依据原付款重新部分核销",
    }
    first = call("POST", "/api/subledger/allocations", request, expected=201)
    again = call("POST", "/api/subledger/allocations", request, expected=201)
    assert first["allocations"][0]["id"] == again["allocations"][0]["id"]
    after = call("GET", f"/api/subledger/items/{item}/workspace?book_id={book}")
    assert after["item"]["remaining_cents"] == 800000 and len(after["allocations"]) == 2
    return {
        "asset_review_post": True,
        "settlement_resume": True,
        "idempotency": True,
        "isolation": True,
    }
