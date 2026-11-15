"""TAX02 formal red tests for period-end closing completeness."""

from __future__ import annotations

import json
import pytest

from app.accounting.domain import AccountingError
from app.accounting.invariants import cents_to_money
from app.ledgers.models import EntityCreate
from app.ledgers.service import LedgersService
from app.opening.models import AccountCreate, AccountRevision
from app.opening.service import OpeningService
from app.periodic.models import ClosingGenerate, DraftCommand, DraftPost
from app.periodic.service import PeriodicService
from app.posting.models import ManualLine, ManualVoucher
from review_policy_fixtures import independent_reviewer, post_reviewed_manual
from test_b04_pilot import setup_b03
from test_business import business  # fixture registration


def _account(case, code, name, category, normal, aux=()):
    ledger_id = case.book["_ledger_id"] if "_ledger_id" in case.book else None
    if ledger_id is None:
        from app.accounting.service import AccountingService

        with case.db.transaction(actor=case.manager) as conn:
            ledger_id = conn.execute(
                "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (case.book["id"],)
            ).fetchone()[0]
    return OpeningService(case.db).create_account(
        case.manager,
        AccountCreate(
            ledger_id=ledger_id,
            code=code,
            name=name,
            category=category,
            normal_balance=normal,
            auxiliary_requirements=list(aux),
            effective_from="2026-01",
            effective_to="2026-12",
            reason="TAX02 结转测试科目",
        ),
    )


def _post(case, posting, lines, key):
    return post_reviewed_manual(
        posting,
        case.manager,
        ManualVoucher(
            command_key=key,
            book_id=case.book["id"],
            voucher_date="2026-10-31",
            voucher_type="记",
            description="TAX02 结转前余额",
            reason="TAX02 结转测试",
            lines=lines,
        ),
    )


def _line(account, debit="0.00", credit="0.00", aux=None):
    return ManualLine(
        account_id=account["id"],
        account_version=account["version"],
        debit=debit,
        credit=credit,
        auxiliary_values=aux or {},
    )


def _closing(case, income, expense, profit):
    return PeriodicService(case.db).generate_closing(
        case.manager,
        ClosingGenerate(
            book_id=case.book["id"],
            period=case.book["period"],
            profit_account_id=profit["id"],
            profit_account_version=profit["version"],
            idempotency_key="tax02-closing",
            reason="TAX02 期末结转",
        ),
    )


def _lines(draft):
    return draft["lines"]


def test_closing_includes_both_income_and_contra_expense(business):
    case = business
    opening, posting = setup_b03(case)
    income = _account(case, "500101", "主营业务收入", "income", "credit")
    profit = _account(case, "3103", "本年利润", "equity", "credit")
    _post(
        case,
        posting,
        [_line(income, credit="100.00"), _line(opening["bank"], debit="100.00")],
        "tax02-income-100",
    )
    _post(
        case,
        posting,
        [_line(opening["expense"], credit="10.00"), _line(opening["bank"], debit="10.00")],
        "tax02-expense-credit-10",
    )

    draft = _closing(case, income, opening["expense"], profit)
    by_account = {}
    for line in _lines(draft):
        by_account.setdefault(line["account_id"], []).append(line)

    assert sum(int(line["debit_cents"]) for line in by_account[income["id"]]) == 10000
    assert sum(int(line["debit_cents"]) for line in by_account[opening["expense"]["id"]]) == 1000
    profit_debit = sum(int(line["debit_cents"]) for line in by_account[profit["id"]])
    profit_credit = sum(int(line["credit_cents"]) for line in by_account[profit["id"]])
    assert profit_credit - profit_debit == 11000


def test_closing_debits_profit_for_contra_income_debit(business):
    case = business
    opening, posting = setup_b03(case)
    income = _account(case, "500102", "其他业务收入", "income", "credit")
    profit = _account(case, "3103", "本年利润", "equity", "credit")
    _post(
        case,
        posting,
        [_line(income, debit="5.00"), _line(opening["bank"], credit="5.00")],
        "tax02-income-debit-5",
    )

    draft = _closing(case, income, opening["expense"], profit)
    profit_debit = sum(int(line["debit_cents"]) for line in _lines(draft) if line["account_id"] == profit["id"])
    assert profit_debit == 500


def test_net_loss_debits_profit(business):
    case = business
    opening, posting = setup_b03(case)
    income = _account(case, "500103", "服务收入", "income", "credit")
    profit = _account(case, "3103", "本年利润", "equity", "credit")
    _post(
        case,
        posting,
        [_line(income, credit="10.00"), _line(opening["bank"], debit="10.00")],
        "tax02-net-income-10",
    )
    _post(
        case,
        posting,
        [_line(opening["expense"], debit="20.00"), _line(opening["bank"], credit="20.00")],
        "tax02-net-expense-20",
    )

    draft = _closing(case, income, opening["expense"], profit)
    profit_debit = sum(int(line["debit_cents"]) for line in _lines(draft) if line["account_id"] == profit["id"])
    assert profit_debit == 1000


def test_no_profit_and_loss_balance_is_rejected(business):
    case = business
    setup_b03(case)
    income = _account(case, "500104", "无余额收入", "income", "credit")
    profit = _account(case, "3103", "本年利润", "equity", "credit")
    with pytest.raises(AccountingError) as error:
        _closing(case, income, case.book, profit)
    assert error.value.status == 409


def test_closing_preserves_auxiliary_project_dimensions(business):
    case = business
    opening, posting = setup_b03(case)
    projects = LedgersService(case.db)
    first = projects.create_entity(
        case.manager, case.book["id"], EntityCreate(kind="project", code="TAX-P1", name="TAX 项目一")
    )
    second = projects.create_entity(
        case.manager, case.book["id"], EntityCreate(kind="project", code="TAX-P2", name="TAX 项目二")
    )
    expense = _account(case, "6602.10", "项目费用", "expense", "debit", ("project",))
    profit = _account(case, "3103", "本年利润", "equity", "credit")
    _post(
        case,
        posting,
        [
            _line(expense, debit="30.00", aux={"project": first["id"]}),
            _line(opening["bank"], credit="30.00"),
        ],
        "tax02-aux-30",
    )
    _post(
        case,
        posting,
        [
            _line(expense, debit="20.00", aux={"project": second["id"]}),
            _line(opening["bank"], credit="20.00"),
        ],
        "tax02-aux-20",
    )

    draft = _closing(case, expense, expense, profit)
    expense_lines = [line for line in _lines(draft) if line["account_id"] == expense["id"]]
    assert sorted(json.loads(line["aux_json"]).get("project") for line in expense_lines) == sorted([first["id"], second["id"]])
    assert sorted(int(line["credit_cents"]) for line in expense_lines) == [2000, 3000]
    assert any(line["account_id"] == profit["id"] for line in _lines(draft))


def _approve_and_post(case, draft):
    service = PeriodicService(case.db)
    reviewer = independent_reviewer(case.db, case.manager, case.book["id"])
    approved = service.approve(
        reviewer,
        draft["draft"]["id"],
        DraftCommand(expected_version=draft["draft"]["version"], reason="TAX02 独立复核"),
    )
    return service.post(
        reviewer,
        approved["draft"]["id"],
        DraftPost(
            expected_version=approved["draft"]["version"],
            command_key="tax02-closing-post",
            voucher_date="2026-10-31",
            reason="TAX02 结转记账",
        ),
    )


def test_closing_approval_and_post_zero_the_auxiliary_balances(business):
    case = business
    opening, posting = setup_b03(case)
    income = _account(case, "500105", "主营收入", "income", "credit")
    profit = _account(case, "3103", "本年利润", "equity", "credit")
    _post(
        case,
        posting,
        [_line(income, credit="100.00"), _line(opening["bank"], debit="100.00")],
        "tax02-post-income",
    )
    _post(
        case,
        posting,
        [_line(opening["expense"], credit="10.00"), _line(opening["bank"], debit="10.00")],
        "tax02-post-expense",
    )

    posted = _approve_and_post(case, _closing(case, income, opening["expense"], profit))
    assert posted["draft"]["status"] == "posted"
    with case.db.transaction(actor=case.manager) as conn:
        rows = conn.execute(
            "SELECT l.account_id,SUM(l.debit_cents) AS debit,SUM(l.credit_cents) AS credit "
            "FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id WHERE v.book_id=? "
            "GROUP BY l.account_id",
            (case.book["id"],),
        ).fetchall()
    balances = {row["account_id"]: int(row["debit"]) - int(row["credit"]) for row in rows}
    assert balances[income["id"]] == 0
    assert balances[opening["expense"]["id"]] == 0
    assert balances[profit["id"]] == -11000


def test_cost_account_is_not_closed_as_profit_and_loss(business):
    case = business
    opening, posting = setup_b03(case)
    cost = _account(case, "500201", "生产成本", "cost", "debit")
    profit = _account(case, "3103", "本年利润", "equity", "credit")
    _post(
        case,
        posting,
        [_line(cost, debit="50.00"), _line(opening["bank"], credit="50.00")],
        "tax02-cost-50",
    )
    with pytest.raises(AccountingError) as error:
        _closing(case, cost, cost, profit)
    assert error.value.status == 409


def test_changed_account_category_blocks_silent_reclassification(business):
    case = business
    opening, posting = setup_b03(case)
    income = _account(case, "500106", "待改分类收入", "income", "credit")
    profit = _account(case, "3103", "本年利润", "equity", "credit")
    _post(
        case,
        posting,
        [_line(income, credit="100.00"), _line(opening["bank"], debit="100.00")],
        "tax02-category-income",
    )
    ledger_id = None
    with case.db.transaction(actor=case.manager) as conn:
        ledger_id = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (case.book["id"],)
        ).fetchone()[0]
    OpeningService(case.db).revise_account(
        case.manager,
        income["id"],
        AccountRevision(
            ledger_id=ledger_id,
            code="500106",
            name="待改分类收入",
            category="expense",
            normal_balance="debit",
            effective_from="2026-10",
            effective_to="2026-12",
            expected_version=1,
            reason="TAX02 分类变更测试",
        ),
    )
    with pytest.raises(AccountingError) as error:
        _closing(case, income, opening["expense"], profit)
    assert error.value.status == 409


def test_source_change_voids_old_closing_draft(business):
    case = business
    opening, posting = setup_b03(case)
    income = _account(case, "500107", "源变更收入", "income", "credit")
    profit = _account(case, "3103", "本年利润", "equity", "credit")
    _post(
        case,
        posting,
        [_line(income, credit="100.00"), _line(opening["bank"], debit="100.00")],
        "tax02-source-income",
    )
    first = _closing(case, income, opening["expense"], profit)
    _post(
        case,
        posting,
        [_line(opening["expense"], credit="10.00"), _line(opening["bank"], debit="10.00")],
        "tax02-source-expense-late",
    )
    second = _closing(case, income, opening["expense"], profit)
    assert second["draft"]["id"] != first["draft"]["id"]
    with case.db.transaction(actor=case.manager) as conn:
        old_run = conn.execute(
            "SELECT status,run_key FROM periodic_runs WHERE draft_id=?", (first["draft"]["id"],)
        ).fetchone()
    assert old_run["status"] == "void"
    profit_credit = sum(
        int(line["credit_cents"]) for line in second["lines"] if line["account_id"] == profit["id"]
    )
    assert profit_credit == 11000
    reviewer = independent_reviewer(case.db, case.manager, case.book["id"])
    with pytest.raises(AccountingError) as error:
        PeriodicService(case.db).approve(
            reviewer,
            first["draft"]["id"],
            DraftCommand(expected_version=1, reason="TAX02 旧草稿复核"),
        )
    assert error.value.status == 409
