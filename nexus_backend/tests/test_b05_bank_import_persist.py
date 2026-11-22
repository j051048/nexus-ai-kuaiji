"""Batch 05：银行流水导入落库与核销到流水行的强绑定。

两条性质：
1. 同一条柜台流水重复导入不会落第二行（external_ref 幂等），失败时整批拒绝；
2. 核销要么绑到一条校验通过的流水行，要么留空——绝不按金额猜一行。
"""

from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.tax.fees import TaxFeeService
from app.treasury.imports import BankStatementImportService
from test_business import business  # noqa: F401
from test_tax06_fees import POLICY, _bank, _masters, _source

CSV = (
    "account,serial,date,counterparty,amount,direction\n"
    "6222000000000001,S-1,2026-10-10,张师傅核算主体,90.00,income\n"
    "6222000000000001,S-2,2026-10-11,李师傅核算主体,135.00,income\n"
)


def test_bank_csv_confirm_persists_statement_lines_idempotently(business):
    case = business
    service = BankStatementImportService(case.db)
    first = service.confirm(case.manager, case.book["id"], csv_text=CSV)
    assert first["inserted_count"] == 2 and first["skipped_count"] == 0
    assert first["posted"] is False
    with case.db.transaction(actor=case.manager) as conn:
        rows = conn.execute(
            "SELECT external_ref,amount_cents,status,line_date FROM bank_statement_lines "
            "WHERE book_id=? ORDER BY external_ref",
            (case.book["id"],),
        ).fetchall()
    assert [(row["external_ref"], row["amount_cents"]) for row in rows] == [
        ("6222000000000001|S-1", 9_000),
        ("6222000000000001|S-2", 13_500),
    ]
    assert {row["status"] for row in rows} == {"unmatched"}
    # 重复导入同一份文件：不新增行，返回原行 id。
    again = service.confirm(case.manager, case.book["id"], csv_text=CSV)
    assert again["inserted_count"] == 0 and again["skipped_count"] == 2
    with case.db.transaction(actor=case.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM bank_statement_lines").fetchone()[0] == 2


def test_bank_csv_confirm_refuses_a_broken_file_instead_of_partially_posting(business):
    case = business
    service = BankStatementImportService(case.db)
    with pytest.raises(AccountingError) as refused:
        service.confirm(
            case.manager,
            case.book["id"],
            csv_text="account,serial,date,counterparty,amount,direction\n"
            "X,S-9,2026-10-10,某人,10.00,sideways\n",
        )
    assert refused.value.status == 400
    with case.db.transaction(actor=case.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM bank_statement_lines").fetchone()[0] == 0


def _reviewed_fee_batch(case):
    _driver, _vehicle, driver2, vehicle2 = _masters(case)
    from app.ledgers.models import TransportAssignmentCreate
    from app.ledgers.service import LedgersService

    LedgersService(case.db).create_transport_assignment(
        case.manager,
        case.book["id"],
        TransportAssignmentCreate(
            vehicle_id=vehicle2["id"],
            driver_id=driver2["id"],
            shift="full",
            contract_external_id="C1",
            valid_from="2026-10-05",
            valid_to="2026-10-05",
        ),
    )
    source = _source(case)
    service = TaxFeeService(case.db)
    batch = service.generate(
        case.manager,
        case.book["id"],
        period="2026-10",
        source_file_ids=[source["id"]],
        policy=POLICY,
    )
    service.review(
        case.actors["accountant"],
        case.book["id"],
        batch["id"],
        expected_version=batch["version"],
        reason="独立复核规费批次",
    )
    return service, service.list_receivables(case.actors["accountant"], case.book["id"])


def _line(
    case, *, amount_cents, line_date="2026-10-10", book_id=None, source_id=None, status="unmatched"
):
    identifier = uuid4().hex
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "INSERT INTO bank_statement_lines(id,book_id,period,line_date,description,amount_cents,"
            "status,created_by,created_at,source_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                book_id or case.book["id"],
                "2026-10",
                line_date,
                "张师傅核算主体 S-ALLOC",
                amount_cents,
                status,
                case.manager.user_id,
                "2026-10-10T00:00:00+00:00",
                source_id,
            ),
        )
    return identifier


def test_allocation_binds_the_validated_statement_line(business):
    case = business
    service, receivables = _reviewed_fee_batch(case)
    item = receivables["items"][0]
    bank_id = _bank(case, "income", int(item["amount_cents"]), "B-ALLOC-1")
    line_id = _line(case, amount_cents=int(item["amount_cents"]))
    allocation = service.allocate_receivable(
        case.actors["accountant"],
        case.book["id"],
        item["receivable_id"],
        bank_source_id=bank_id,
        amount_cents=int(item["amount_cents"]),
        idempotency_key="alloc-line-1",
        bank_statement_line_id=line_id,
    )
    assert allocation["bank_statement_line_id"] == line_id
    with case.db.transaction(actor=case.manager) as conn:
        stored = conn.execute(
            "SELECT bank_statement_line_id FROM fee_receivable_allocations WHERE id=?",
            (allocation["allocation_id"],),
        ).fetchone()
    assert stored["bank_statement_line_id"] == line_id


def test_allocation_refuses_a_line_that_does_not_match_the_receipt(business):
    case = business
    service, receivables = _reviewed_fee_batch(case)
    item = receivables["items"][0]
    bank_id = _bank(case, "income", int(item["amount_cents"]), "B-ALLOC-2")
    wrong_amount = _line(case, amount_cents=int(item["amount_cents"]) + 1)
    with pytest.raises(AccountingError) as refused:
        service.allocate_receivable(
            case.actors["accountant"],
            case.book["id"],
            item["receivable_id"],
            bank_source_id=bank_id,
            amount_cents=int(item["amount_cents"]),
            idempotency_key="alloc-line-bad-amount",
            bank_statement_line_id=wrong_amount,
        )
    assert "金额" in refused.value.detail
    other_book_line = _line(
        case, amount_cents=int(item["amount_cents"]), book_id=case.other_book["id"]
    )
    with pytest.raises(AccountingError) as denied:
        service.allocate_receivable(
            case.actors["accountant"],
            case.book["id"],
            item["receivable_id"],
            bank_source_id=bank_id,
            amount_cents=int(item["amount_cents"]),
            idempotency_key="alloc-line-other-book",
            bank_statement_line_id=other_book_line,
        )
    assert denied.value.status == 404
    with case.db.transaction(actor=case.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fee_receivable_allocations").fetchone()[0] == 0, (
            "被拒的核销不能留下任何分配行"
        )


def test_allocation_auto_links_a_line_already_bound_to_the_bank_source(business):
    case = business
    service, receivables = _reviewed_fee_batch(case)
    item = receivables["items"][0]
    bank_id = _bank(case, "income", int(item["amount_cents"]), "B-ALLOC-3")
    line_id = _line(case, amount_cents=int(item["amount_cents"]), source_id=bank_id)
    allocation = service.allocate_receivable(
        case.actors["accountant"],
        case.book["id"],
        item["receivable_id"],
        bank_source_id=bank_id,
        amount_cents=int(item["amount_cents"]),
        idempotency_key="alloc-line-auto",
    )
    assert allocation["bank_statement_line_id"] == line_id
