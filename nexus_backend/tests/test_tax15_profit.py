from __future__ import annotations

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, DraftLine, DraftUpdate, ImportRequest, SourceRow
from app.opening.models import AccountCreate, MappingCreate
from app.opening.service import OpeningService
from app.posting.models import OpeningBind, PostDraft
from app.posting.service import PostingService
from app.reports.profit import ProfitDrilldownService, ProfitMappingItem, ProfitMappingWrite
from app.reports.service import ReportService
from test_business import business  # noqa: F401
from test_posting_service import frozen_opening, ledger_of


def _account(case, code, name, category, normal):
    opening = OpeningService(case.db)
    account = opening.create_account(
        case.manager,
        AccountCreate(
            ledger_id=ledger_of(case), code=code, name=name, category=category,
            normal_balance=normal, effective_from="2026-01", effective_to="2026-12",
            reason="TAX15 利润下钻测试科目",
        ),
    )
    opening.create_mapping(
        case.manager,
        MappingCreate(
            book_id=case.book["id"], book_account_code=code, account_id=account["id"],
            account_version=account["version"], reason="TAX15 利润下钻映射",
        ),
    )
    return account


def _post(case, external_id, amount, direction, voucher_date, command, debit, credit):
    case.ledger.import_sources(
        case.manager, case.book["id"],
        ImportRequest(
            source_name="利润下钻测试",
            rows=[SourceRow(
                external_id=external_id, date=voucher_date, description=f"测试 {external_id}",
                counterparty="虚构客户", amount=amount, direction=direction,
                evidence_ref=f"证据-{external_id}",
            )],
        ),
    )
    case.ledger.suggestions(case.manager, case.book["id"])
    library = case.ledger.get_book(case.manager, case.book["id"])
    source = next(row for row in library["sources"] if row["external_id"] == external_id)
    draft = next(row for row in library["drafts"] if row["source_id"] == source["id"])
    draft = case.ledger.update_draft(
        case.manager, case.book["id"], draft["id"],
        DraftUpdate(
            expected_version=draft["version"], description=f"测试 {external_id}",
            lines=[
                DraftLine(account_code=debit, debit=amount, credit="0.00"),
                DraftLine(account_code=credit, debit="0.00", credit=amount),
            ],
        ),
    )
    approved = case.ledger.approve(
        case.actors["accountant"], case.book["id"], draft["id"],
        ApprovalRequest(expected_version=draft["version"]),
    )
    PostingService(case.db).post_draft(
        case.manager, case.book["id"], approved["id"],
        PostDraft(command_key=command, expected_version=approved["version"], voucher_date=voucher_date),
    )
    return approved


def test_profit_drilldown_maps_lines_and_keeps_unknown_tax_out_of_zero(business):
    case = business
    opening = frozen_opening(case)
    binding = PostingService(case.db).bind_opening(
        case.manager,
        OpeningBind(book_id=case.book["id"], package_version_id=opening["version_id"], reason="冻结期初"),
    )
    assert binding["binding"]["status"] == "active"
    _account(case, "1122", "应收账款", "asset", "debit")
    _account(case, "6001", "主营业务收入", "income", "credit")

    _post(case, "profit-revenue-1", "5000.00", "income", "2026-10-10", "profit-revenue-1", "1002", "6001")
    _post(case, "profit-expense-1", "1000.00", "expense", "2026-10-11", "profit-expense-1", "6602.02", "1002")

    service = ProfitDrilldownService(case.db)
    mapping = service.mapping(case.manager, case.book["id"])
    assert mapping["version"] == 1
    assert any(item["line_kind"] == "revenue" for item in mapping["items"])

    view = service.overview(case.manager, case.book["id"], "2026-10")
    assert view["totals"]["revenue_cents"] == 500000
    assert view["totals"]["cost_and_expense_cents"] == 100000
    assert view["totals"]["operating_profit_cents"] == 400000
    assert view["totals"]["income_tax_cents"] is None
    assert view["totals"]["net_profit_cents"] is None
    assert view["totals"]["year_to_date_known"] is False
    revenue = [line for line in view["lines"] if line["line_kind"] == "revenue"][0]
    assert revenue["amount_cents"] == 500000
    assert revenue["items_count"] == 1
    assert revenue["accounts"] == [
        {"account_code": "6001", "account_name": "主营业务收入", "amount_cents": 500000, "items_count": 1}
    ]

    detail = service.overview(case.manager, case.book["id"], "2026-10", "revenue")
    assert len(detail["items"]) == 1
    assert detail["items"][0]["account_code"] == "6001"
    assert detail["items"][0]["evidence_ref"] == "证据-profit-revenue-1"
    assert detail["items"][0]["original_url"] is None

    with case.db.transaction(actor=case.manager) as conn:
        source_id = conn.execute(
            "SELECT id FROM sources WHERE book_id=? AND external_id='profit-revenue-1'",
            (case.book["id"],),
        ).fetchone()["id"]
        book_scope = conn.execute(
            "SELECT firm_id,company_id FROM books WHERE id=?", (case.book["id"],)
        ).fetchone()
        conn.execute(
            "INSERT INTO bank_import_files(id,book_id,firm_id,company_id,storage_key,filename,media_type,sha256,size_bytes,table_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "file-profit-revenue",
                case.book["id"],
                book_scope["firm_id"],
                book_scope["company_id"],
                "storage-profit-revenue",
                "profit-revenue.csv",
                "text/csv",
                "a" * 64,
                1,
                '{"headers":[]}',
                case.manager.user_id,
                "2026-10-31T00:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO bank_import_previews(id,book_id,firm_id,company_id,file_id,payload_json,fingerprint,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "preview-profit-revenue",
                case.book["id"],
                book_scope["firm_id"],
                book_scope["company_id"],
                "file-profit-revenue",
                '{"rows":[]}',
                "b" * 64,
                case.manager.user_id,
                "2026-10-31T00:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO bank_import_source_files(preview_id,file_id,book_id,firm_id,company_id,source_id,row_number) VALUES(?,?,?,?,?,?,?)",
            (
                "preview-profit-revenue",
                "file-profit-revenue",
                case.book["id"],
                book_scope["firm_id"],
                book_scope["company_id"],
                source_id,
                2,
            ),
        )

    linked = service.overview(case.manager, case.book["id"], "2026-10", "revenue")
    assert linked["items"][0]["original_file_id"] == "file-profit-revenue"
    assert linked["items"][0]["original_url"] == (
        f"/api/books/{case.book['id']}/import-wizard/files/file-profit-revenue"
    )

    impacts = service.impacts(
        case.manager, case.book["id"], detail["items"][0]["voucher_id"]
    )
    # The voucher also debits 1122, which is intentionally outside the profit mapping.
    assert impacts["unmapped_line_count"] == 1
    assert impacts["lines"] == [
        {
            "line_code": "revenue",
            "line_name": "营业收入",
            "line_kind": "revenue",
            "amount_cents": 500000,
            "line_count": 1,
        }
    ]


def test_profit_snapshot_freezes_line_amounts_and_reports_changes(business):
    case = business
    opening = frozen_opening(case)
    PostingService(case.db).bind_opening(
        case.manager,
        OpeningBind(book_id=case.book["id"], package_version_id=opening["version_id"], reason="冻结期初"),
    )
    _account(case, "1122", "应收账款", "asset", "debit")
    _account(case, "6001", "主营业务收入", "income", "credit")
    _post(case, "profit-snapshot-revenue-1", "5000.00", "income", "2026-10-10", "profit-snapshot-revenue-1", "1002", "6001")
    _post(case, "profit-snapshot-expense-1", "1000.00", "expense", "2026-10-11", "profit-snapshot-expense-1", "6602.02", "1002")

    report = ReportService(case.db).create(case.manager, case.book["id"], "2026-10")["report"]
    _post(case, "profit-snapshot-revenue-2", "3000.00", "income", "2026-10-12", "profit-snapshot-revenue-2", "1002", "6001")

    service = ProfitDrilldownService(case.db)
    live = service.overview(case.manager, case.book["id"], "2026-10")
    assert live["totals"]["revenue_cents"] == 800000
    assert live["snapshot"] is None

    frozen = service.overview(case.manager, case.book["id"], "2026-10", "revenue", report["id"])
    assert frozen["totals"]["revenue_cents"] == 500000
    assert frozen["totals"]["cost_and_expense_cents"] == 100000
    assert frozen["totals"]["operating_profit_cents"] == 400000
    assert frozen["snapshot"]["report_run_id"] == report["id"]
    assert frozen["snapshot"]["version"] == report["version"]
    assert frozen["snapshot"]["changed_line_codes"] == ["revenue"]
    revenue = [line for line in frozen["lines"] if line["line_kind"] == "revenue"][0]
    assert revenue["amount_cents"] == 500000
    assert revenue["live_amount_cents"] == 800000
    assert revenue["snapshot_changed"] is True
    assert revenue["items_count"] == 2

    with pytest.raises(AccountingError):
        service.overview(case.manager, case.book["id"], "2026-11", "revenue", report["id"])


def test_profit_mapping_versions_and_permissions(business):
    case = business
    opening = frozen_opening(case)
    PostingService(case.db).bind_opening(
        case.manager,
        OpeningBind(book_id=case.book["id"], package_version_id=opening["version_id"], reason="冻结期初"),
    )
    _account(case, "1122", "应收账款", "asset", "debit")
    _account(case, "6001", "主营业务收入", "income", "credit")
    service = ProfitDrilldownService(case.db)
    with pytest.raises(AccountingError):
        service.overview(case.actors["peer"], case.book["id"], "2026-10", "revenue")

    changed = service.save_mapping(
        case.manager, case.book["id"],
        ProfitMappingWrite(items=[
            ProfitMappingItem(
                line_code="revenue", line_name="营业收入", line_kind="revenue",
                account_prefix="6001", sign=1,
            )
        ]),
    )
    assert changed["version"] == 1
    changed = service.save_mapping(
        case.manager, case.book["id"],
        ProfitMappingWrite(items=[
            ProfitMappingItem(
                line_code="revenue", line_name="营业收入", line_kind="revenue",
                account_prefix="6001", sign=1,
            )
        ]),
    )
    assert changed["version"] == 2
    with pytest.raises(AccountingError):
        service.save_mapping(
            case.actors["accountant"], case.book["id"],
            ProfitMappingWrite(items=[
                ProfitMappingItem(
                    line_code="revenue", line_name="营业收入", line_kind="revenue",
                    account_prefix="6001", sign=1,
                )
            ]),
        )


def test_profit_variance_is_arithmetic_with_evidence_anchors(business, monkeypatch):
    case = business
    service = ProfitDrilldownService(case.db)
    books = {
        "current": {"id": "b-current", "company_id": "c1", "firm_id": "f1", "period": "2026-10"},
        "baseline": {"id": "b-baseline", "company_id": "c1", "firm_id": "f1", "period": "2026-09"},
    }
    monkeypatch.setattr(service, "_book", lambda conn, actor, book_id: books[book_id])
    monkeypatch.setattr(service, "_book_period", lambda actor, book_id: books[book_id]["period"])

    def overview(actor, book_id, period, line_code=""):
        amount = 150000 if book_id == "current" else 125000
        anchor = "v-current" if book_id == "current" else "v-baseline"
        return {
            "book_id": book_id,
            "period": period,
            "lines": [
                {"line_code": "revenue", "line_name": "营业收入", "line_kind": "revenue", "amount_cents": amount}
            ],
            "items": [
                {
                    "line_code": "revenue", "voucher_id": anchor, "voucher_no": 1,
                    "voucher_date": period + "-10", "description": "测试", "line_no": 1,
                    "account_code": "6001", "account_name": "收入", "debit_cents": 0,
                    "credit_cents": amount, "net_cents": amount,
                    "source_ref": "s-" + anchor, "evidence_ref": "e-" + anchor,
                }
            ],
        }

    monkeypatch.setattr(service, "overview", overview)
    result = service.variance(case.manager, "current", "baseline", "revenue")
    assert result["lines"][0]["delta_cents"] == 25000
    assert result["checks"]["causal_claim"] is False
    assert result["contributors"]["revenue"]["current"][0]["evidence_ref"] == "e-v-current"
    assert result["contributors"]["revenue"]["baseline"][0]["voucher_id"] == "v-baseline"
