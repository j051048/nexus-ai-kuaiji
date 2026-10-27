"""C01 remaining controls: dimensions, capacity and independent bank evidence."""

from review_policy_fixtures import post_reviewed_manual, seed_pre_review_manual_voucher

import json
import sqlite3
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.ledgers.models import EntityCreate
from app.ledgers.service import LedgersService
from app.posting.models import ManualLine, ManualVoucher
from app.posting.service import PostingService
from app.subledger.models import (
    AllocationCreate,
    AllocationPart,
    BankAdjustment,
    ControlAccountCreate,
    OpenItemCreate,
)
from app.subledger.service import SubledgerService
from test_b04_pilot import setup_b03
from test_business import business
from test_c01_auxiliary_entities import _aux_case
from test_c01_bank_evidence import coverage_case


def command(case, voucher, item, amount="3000.00"):
    return AllocationCreate(
        book_id=case.book["id"],
        voucher_id=voucher["voucher"]["id"],
        parts=[AllocationPart(item_id=item["id"], amount=amount)],
        idempotency_key="closure-" + uuid4().hex,
        reason="真实核销辅助维度验证",
    )


def payment(case, account, bank, aux=None):
    return post_reviewed_manual(
        PostingService(case.db),
        case.manager,
        ManualVoucher(
            book_id=case.book["id"],
            voucher_date="2026-10-20",
            command_key="closure-" + uuid4().hex,
            description="合成付款凭证",
            lines=[
                ManualLine(
                    account_id=account,
                    account_version=1,
                    debit="3000.00",
                    auxiliary_values=aux or {},
                ),
                ManualLine(account_id=bank, account_version=1, credit="3000.00"),
            ],
        ),
    )


def test_same_control_total_cannot_hide_wrong_auxiliary_bucket(business):
    c = business
    sub, base, _, project, _ = _aux_case(c, ("project",))
    sub.create_item(
        c.manager, OpenItemCreate(**{**base, "amount": "10000.00"}, aux={"project": project["id"]})
    )
    result = sub.control_reconciliation(c.manager, c.book["id"])
    assert result["passed"] is False
    assert any(
        row.get("aux") == {"project": project["id"]} and not row["passed"] for row in result["rows"]
    )


def test_allocation_cannot_pay_a_different_project(business):
    c = business
    sub, base, _, project, _ = _aux_case(c, ("project",))
    second = LedgersService(c.db).create_entity(
        c.manager, c.book["id"], EntityCreate(kind="project", code="PROJECT-B", name="另一项目")
    )
    item = sub.create_item(
        c.manager, OpenItemCreate(**{**base, "amount": "10000.00"}, aux={"project": project["id"]})
    )
    with c.db.transaction(actor=c.manager) as conn:
        bank = conn.execute(
            "SELECT account_id FROM ledger_account_versions WHERE code='1002'"
        ).fetchone()[0]
    voucher = payment(c, base["control_account_id"], bank, {"project": second["id"]})
    with pytest.raises(AccountingError):
        sub.allocate(c.manager, command(c, voucher, item))


@pytest.mark.parametrize("kind", ["advance_paid", "advance_received"])
def test_advance_settlement_uses_reducing_side(kind, business):
    c = business
    opening, posting = setup_b03(c)
    sub = SubledgerService(c.db)
    account = opening["bank"] if kind == "advance_paid" else opening["payable"]
    sub.configure_control_account(
        c.manager,
        ControlAccountCreate(
            book_id=c.book["id"],
            kind=kind,
            control_account_id=account["id"],
            control_account_version=1,
            effective_from="2026-01",
            reason="预收预付控制科目",
        ),
    )
    party = LedgersService(c.db).create_entity(
        c.manager,
        c.book["id"],
        EntityCreate(kind="counterparty", code="ADVANCE", name="预收预付往来方"),
    )
    item = sub.create_item(
        c.manager,
        OpenItemCreate(
            book_id=c.book["id"],
            kind=kind,
            counterparty_id=party["id"],
            control_account_id=account["id"],
            control_account_version=1,
            original_date="2026-10-01",
            amount="10000.00",
            reason="预收预付期初",
        ),
    )
    voucher = payment(c, opening["payable"]["id"], opening["bank"]["id"])
    result = sub.allocate(c.manager, command(c, voucher, item))
    assert result["allocations"][0]["amount_cents"] == 300000


def test_legacy_unresolved_adjustments_cannot_cancel_each_other(tmp_path, monkeypatch):
    import app.accounting.database as database

    migration_files = database.migration_files
    # Build actual pre-upgrade history, then apply the forward migration unchanged.
    monkeypatch.setattr(
        database,
        "migration_files",
        lambda dialect: [p for p in migration_files(dialect) if int(p.name[:3]) < 96],
    )
    c = business.__wrapped__(tmp_path)
    opening, posting = setup_b03(c)
    sub = SubledgerService(c.db)
    voucher = seed_pre_review_manual_voucher(
        posting,
        c.manager,
        ManualVoucher(
            book_id=c.book["id"],
            voucher_date="2026-10-20",
            command_key="empty-bank-balance",
            description="清偿期初",
            lines=[
                ManualLine(
                    account_id=opening["payable"]["id"], account_version=1, debit="10000.00"
                ),
                ManualLine(account_id=opening["bank"]["id"], account_version=1, credit="10000.00"),
            ],
        ),
    )
    # Native historic fixture: offsets are still separate unresolved facts.
    with c.db.transaction(write=True, actor=c.manager) as conn:
        for cents in (100, -100):
            conn.execute(
                "INSERT INTO bank_recon_adjustments(id,book_id,period,kind,amount_cents,explanation,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    c.book["id"],
                    "2026-10",
                    "other",
                    cents,
                    "分别未解决的调整项",
                    "open",
                    c.manager.user_id,
                    "2026-10-21",
                ),
            )
        line = next(x for x in voucher["lines"] if x["account_id"] == opening["bank"]["id"])
        conn.execute(
            "INSERT INTO bank_statement_lines(id,book_id,period,line_date,description,amount_cents,matched_voucher_line_id,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                uuid4().hex,
                c.book["id"],
                "2026-10",
                "2026-10-20",
                "合成清偿流水",
                -1000000,
                line["id"],
                "matched",
                c.manager.user_id,
                "2026-10-20",
            ),
        )
    monkeypatch.setattr(database, "migration_files", migration_files)
    c.db._migrate_sqlite()
    result = sub.bank_reconciliation(c.manager, c.book["id"])
    assert result["unexplained_cents"] == 0
    assert result["passed"] is False
    assert result["unresolved_adjustment_count"] == 2


def test_native_allocation_cannot_claim_another_book(business):
    c = business
    sub, base, _, _, _ = _aux_case(c)
    item = sub.create_item(c.manager, OpenItemCreate(**{**base, "amount": "10000.00"}))
    with c.db.transaction(actor=c.manager) as conn:
        bank = conn.execute(
            "SELECT account_id FROM ledger_account_versions WHERE code='1002'"
        ).fetchone()[0]
    voucher = payment(c, base["control_account_id"], bank)
    with pytest.raises(sqlite3.IntegrityError):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "INSERT INTO subledger_allocations(id,firm_id,company_id,book_id,item_id,voucher_id,amount_cents,allocated_on,idempotency_key,request_fingerprint,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    c.manager.firm_id,
                    c.other["id"],
                    c.other_book["id"],
                    item["id"],
                    voucher["voucher"]["id"],
                    1,
                    "2026-10-20",
                    uuid4().hex,
                    "a" * 64,
                    c.manager.user_id,
                    "2026-10-20",
                ),
            )


def test_adjustment_requires_account_and_independent_exact_resolution(coverage_case):
    from app.subledger.models import BankLineCreate, BankMatch, BankAdjustmentResolve
    from test_c01_bank_evidence import declaration, review

    x = coverage_case
    c = x.c
    sub = x.sub
    review(x, c.actors["accountant"], sub.declare_bank_coverage(c.manager, declaration(x)))
    with c.db.transaction(actor=c.manager) as conn:
        bank = conn.execute(
            "SELECT ledger_account_id FROM bank_accounts WHERE id=?", (x.account["id"],)
        ).fetchone()[0]
        payable = conn.execute(
            "SELECT a.id FROM ledger_accounts a JOIN ledger_account_versions v ON v.account_id=a.id WHERE v.code='2202'"
        ).fetchone()[0]
    voucher = post_reviewed_manual(
        PostingService(c.db),
        c.manager,
        ManualVoucher(
            book_id=c.book["id"],
            voucher_date="2026-10-01",
            command_key="resolution-real-payment",
            description="调整项真实付款",
            lines=[
                ManualLine(account_id=payable, account_version=1, debit="0.30"),
                ManualLine(account_id=bank, account_version=1, credit="0.30"),
            ],
        ),
    )
    line = sub.add_bank_line(
        c.manager,
        BankLineCreate(
            book_id=c.book["id"],
            bank_account_id=x.account["id"],
            line_date="2026-10-01",
            description="调整项真实银行原件行",
            amount="-0.30",
        ),
    )
    sub.match_bank_line(
        c.manager,
        line["id"],
        BankMatch(
            voucher_line_id=next(r["id"] for r in voucher["lines"] if r["account_id"] == bank)
        ),
    )
    assert sub.bank_reconciliation(c.manager, c.book["id"])["passed"]
    req = BankAdjustment(
        book_id=c.book["id"],
        period="2026-10",
        kind="bank_fee",
        amount="-0.30",
        explanation="逐笔费用差异调查",
    )
    with pytest.raises(AccountingError):
        sub.add_bank_adjustment(c.manager, req)
    adjustment = sub.add_bank_adjustment(
        c.manager, req.model_copy(update={"bank_account_id": x.account["id"]})
    )
    assert not sub.bank_reconciliation(c.manager, c.book["id"])["passed"]
    resolve = BankAdjustmentResolve(bank_line_id=line["id"], reason="独立核对正式凭证及原件")
    with pytest.raises(AccountingError):
        sub.resolve_bank_adjustment(c.manager, adjustment["id"], resolve)
    first = sub.resolve_bank_adjustment(c.actors["accountant"], adjustment["id"], resolve)
    assert sub.resolve_bank_adjustment(c.actors["accountant"], adjustment["id"], resolve) == first
    assert sub.bank_reconciliation(c.manager, c.book["id"])["passed"]
    with pytest.raises(sqlite3.IntegrityError):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "UPDATE bank_statement_lines SET description=? WHERE id=?",
                ("不得重写已解决证据", line["id"]),
            )
    with pytest.raises(AccountingError):
        sub.resolve_bank_adjustment(
            c.actors["accountant"],
            adjustment["id"],
            resolve.model_copy(update={"reason": "不得修改已冻结解决证据"}),
        )


def test_aging_does_not_include_future_original_items(business):
    c = business
    sub, base, _, _, _ = _aux_case(c)
    sub.create_item(
        c.manager, OpenItemCreate(**{**base, "original_date": "2026-10-25", "amount": "10000.00"})
    )
    assert sub.list_items(c.manager, c.book["id"], as_of="2026-10-01") == []
    assert sum(sub.aging(c.manager, c.book["id"], "2026-10-01")["buckets"].values()) == 0


@pytest.mark.parametrize(
    "gap", ["offset_unmatched_formal", "shifted_opening", "extra_matched_pair"]
)
def test_bank_net_zero_cannot_hide_unmatched_formal_or_wrong_opening(coverage_case, gap):
    from app.subledger.models import BankLineCreate, BankMatch
    from test_c01_bank_evidence import declaration, review

    x = coverage_case
    c = x.c
    sub = x.sub
    with c.db.transaction(actor=c.manager) as conn:
        bank = conn.execute(
            "SELECT ledger_account_id FROM bank_accounts WHERE id=?", (x.account["id"],)
        ).fetchone()[0]
        payable = conn.execute(
            "SELECT a.id FROM ledger_accounts a JOIN ledger_account_versions v ON v.account_id=a.id WHERE v.code='2202'"
        ).fetchone()[0]

    def post(amount, income=False):
        return post_reviewed_manual(
            PostingService(c.db),
            c.manager,
            ManualVoucher(
                book_id=c.book["id"],
                voucher_date="2026-10-01",
                command_key=uuid4().hex,
                description="逐笔银行差异测试",
                lines=[
                    ManualLine(
                        account_id=bank,
                        account_version=1,
                        debit=amount if income else "0.00",
                        credit="0.00" if income else amount,
                    ),
                    ManualLine(
                        account_id=payable,
                        account_version=1,
                        debit="0.00" if income else amount,
                        credit=amount if income else "0.00",
                    ),
                ],
            ),
        )

    voucher = post("0.30")
    line = sub.add_bank_line(
        c.manager,
        BankLineCreate(
            book_id=c.book["id"],
            bank_account_id=x.account["id"],
            line_date="2026-10-01",
            description="实际原件付款行",
            amount="-0.30",
        ),
    )
    sub.match_bank_line(
        c.manager,
        line["id"],
        BankMatch(
            voucher_line_id=next(r["id"] for r in voucher["lines"] if r["account_id"] == bank)
        ),
    )
    income = post("1.00", True)
    changes = {}
    if gap in {"offset_unmatched_formal", "extra_matched_pair"}:
        expense = post("1.00")
        if gap == "extra_matched_pair":
            for extra, amount in ((income, "1.00"), (expense, "-1.00")):
                bank_line = sub.add_bank_line(
                    c.manager,
                    BankLineCreate(
                        book_id=c.book["id"],
                        bank_account_id=x.account["id"],
                        line_date="2026-10-01",
                        description="原件没有的相抵流水",
                        amount=amount,
                    ),
                )
                sub.match_bank_line(
                    c.manager,
                    bank_line["id"],
                    BankMatch(
                        voucher_line_id=next(
                            r["id"] for r in extra["lines"] if r["account_id"] == bank
                        )
                    ),
                )
    else:
        changes = {"opening": "10001.00", "closing": "10000.70"}
    review(
        x, c.actors["accountant"], sub.declare_bank_coverage(c.manager, declaration(x, **changes))
    )
    result = sub.bank_reconciliation(c.manager, c.book["id"])
    assert not result["passed"]
    if gap == "extra_matched_pair":
        assert not result["rows"][0]["statement_source_matches"]
    else:
        assert result["rows"][0]["unmatched_formal_count"] >= 1


def test_manual_legacy_bank_lines_without_account_and_original_cannot_close(business):
    from app.subledger.models import BankLineCreate, BankMatch

    c = business
    opening, posting = setup_b03(c)
    sub = SubledgerService(c.db)
    voucher = post_reviewed_manual(
        posting,
        c.manager,
        ManualVoucher(
            book_id=c.book["id"],
            voucher_date="2026-10-20",
            command_key="legacy-zero-net-no-original",
            description="清偿期初但没有银行原件",
            lines=[
                ManualLine(
                    account_id=opening["payable"]["id"], account_version=1, debit="10000.00"
                ),
                ManualLine(account_id=opening["bank"]["id"], account_version=1, credit="10000.00"),
            ],
        ),
    )
    line = sub.add_bank_line(
        c.manager,
        BankLineCreate(
            book_id=c.book["id"],
            line_date="2026-10-20",
            description="手工流水未提供原件",
            amount="-10000.00",
        ),
    )
    sub.match_bank_line(
        c.manager,
        line["id"],
        BankMatch(
            voucher_line_id=next(
                r["id"] for r in voucher["lines"] if r["account_id"] == opening["bank"]["id"]
            )
        ),
    )
    result = sub.bank_reconciliation(c.manager, c.book["id"])
    assert not result["passed"]
    assert result["bank_account_required"]


def test_future_original_item_cannot_be_recorded_in_current_book(business):
    c = business
    sub, base, _, _, _ = _aux_case(c)
    with pytest.raises(AccountingError):
        sub.create_item(
            c.manager,
            OpenItemCreate(**{**base, "original_date": "2026-11-01", "amount": "10000.00"}),
        )


def test_historical_future_item_cannot_make_current_control_balance_pass(business):
    c = business
    sub, base, _, _, _ = _aux_case(c)
    item = sub.create_item(c.manager, OpenItemCreate(**{**base, "amount": "5000.00"}))
    with c.db.transaction(write=True, actor=c.manager) as conn:
        row = dict(
            conn.execute("SELECT * FROM subledger_items WHERE id=?", (item["id"],)).fetchone()
        )
        row.update(id=uuid4().hex, original_date="2026-11-01")
        conn.execute(
            "INSERT INTO subledger_items("
            + ",".join(row)
            + ") VALUES("
            + ",".join("?" for _ in row)
            + ")",
            tuple(row.values()),
        )
    result = sub.control_reconciliation(c.manager, c.book["id"])
    assert not result["passed"]
    assert result["status"] == "blocked" and result["future_item_count"] == 1
