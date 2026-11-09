"""Real independently reviewed carry-forward opening for synthetic tests."""

from uuid import uuid4
from app.accounting.invariants import cents_to_money, signed_cents_to_money
from app.opening.models import OpeningPackageCreate, OpeningImport, OpeningLineInput, OpeningCommand
from app.opening.service import OpeningService
from app.posting.models import OpeningBind
from app.posting.service import PostingService


def bind_next_opening(db, owner, reviewer, source, target, period):
    posting = PostingService(db)
    balance = posting.trial_balance(owner, source)
    with db.transaction(actor=owner) as conn:
        ledger = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (source,)
        ).fetchone()[0]
        policy = conn.execute(
            "SELECT id FROM accounting_policies WHERE ledger_id=?", (ledger,)
        ).fetchone()[0]
        versions = {
            r["id"]: r["current_version"]
            for r in conn.execute(
                "SELECT id,current_version FROM ledger_accounts WHERE ledger_id=?", (ledger,)
            )
        }
    opening = OpeningService(db)
    package = opening.create_package(
        owner, OpeningPackageCreate(ledger_id=ledger, target_period=period, policy_id=policy)
    )
    rows = [row for row in balance["rows"] if row["is_leaf"]]
    version = opening.import_version(
        owner,
        package["package"]["id"],
        OpeningImport(
            idempotency_key=uuid4().hex,
            reason="合成测试：逐科目独立核对前期正式余额",
            lines=[
                OpeningLineInput(
                    account_id=r["account_id"],
                    account_version=versions[r["account_id"]],
                    year_opening="0.00",
                    ytd_debit=cents_to_money(max(r["closing_cents"], 0)),
                    ytd_credit=cents_to_money(max(-r["closing_cents"], 0)),
                    opening=signed_cents_to_money(r["closing_cents"]),
                )
                for r in rows
            ],
        ),
    )["version"]["id"]
    opening.validate_version(owner, version)
    command = OpeningCommand(expected_version=1, reason="独立复核前期余额与本期开户")
    opening.submit_version(owner, version, command)
    opening.approve_version(reviewer, version, command)
    opening.freeze_version(reviewer, version, command)
    posting.bind_opening(
        owner,
        OpeningBind(
            book_id=target, package_version_id=version, reason="绑定已独立复核的连续期间期初"
        ),
    )
