from __future__ import annotations

import json

from app.accounting.models import BookCreate
from app.tax import profit_source
from test_business import business  # noqa: F401  (pytest fixture)


def _ensure_book(case, period: str, *, name: str | None = None, force_new: bool = False):
    if not force_new and case.book["period"] == period:
        return case.book
    with case.db.transaction(actor=case.manager) as conn:
        if not force_new:
            row = conn.execute(
                "SELECT * FROM books WHERE firm_id=? AND company_id=? AND period=? ORDER BY id LIMIT 1",
                (case.manager.firm_id, case.company["id"], period),
            ).fetchone()
            if row:
                return dict(row)
    return case.ledger.create_book(
        case.manager,
        BookCreate(
            name=name or f"合成账套 {period}",
            period=period,
            company_id=case.company["id"],
        ),
    )


def _report(
    case,
    *,
    book: dict,
    period: str,
    version: int,
    status: str,
    profit_cents: int | None,
) -> str:
    """Insert one report snapshot the way the report pipeline would store it."""
    identifier = f"run-{book['id'][-6:]}-{period}-{status}-{version}"
    mode = "formal" if status in ("reviewed", "published") else "trial"
    policy_version = 1 if mode == "formal" else None
    policy_approver = case.manager.user_id if mode == "formal" else None
    content = (
        {"profit_statement": {"profit_cents": profit_cents, "revenue_cents": 1_000_000}}
        if profit_cents is not None
        else {"balance_sheet": {"assets_cents": 0}}
    )
    with case.db.transaction(
        write=True,
        actor=case.manager,
        company_id=case.company["id"],
        write_scope="company",
    ) as conn:
        conn.execute(
            "INSERT INTO report_runs(id,book_id,period,template_id,template_version,status,version,"
            "inputs_fingerprint,content_json,content_hash,created_by,created_at,"
            "mode,policy_version,policy_approver) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                book["id"],
                period,
                "small_enterprise",
                1,
                status,
                version,
                "fingerprint-" + identifier,
                json.dumps(content),
                "hash-" + identifier,
                case.manager.user_id,
                f"{period}-28T10:00:00+00:00",
                mode,
                policy_version,
                policy_approver,
            ),
        )
    return identifier


def _suggest(case, book: dict, period: str):
    with case.db.transaction(actor=case.manager) as conn:
        resolved = case.service.access.book(conn, case.manager, book["id"], "read")
        return profit_source.suggest(conn, resolved, period)


def test_ytd_cumulative_profit_sums_january_profit_and_february_loss(business):
    """复现并根治：1 月盈利 10,000 元、2 月亏损 2,000 元，截至 2 月累计应为 +8,000 元，绝不是 -2,000 元。"""
    jan = _ensure_book(business, "2026-01")
    feb = _ensure_book(business, "2026-02")
    _report(business, book=jan, period="2026-01", version=1, status="reviewed", profit_cents=1_000_000)
    _report(business, book=feb, period="2026-02", version=1, status="reviewed", profit_cents=-200_000)

    result = _suggest(business, feb, "2026-02")

    assert result["suggested_profit_cents"] == 800_000
    assert result["source_kind"] == "reviewed_report"
    assert result["period_range"] == "2026-01..2026-02"
    assert result["source_period"] == "2026-01..2026-02"
    assert result["month_count"] == 2
    assert result["is_final_report"] is True
    assert result["missing_months"] == []
    assert [m["period"] for m in result["months"]] == ["2026-01", "2026-02"]
    assert result["notice"] is None


def test_reviewed_report_wins_and_version_upgrade_updates_ytd(business):
    jan = _ensure_book(business, "2026-01")
    feb = _ensure_book(business, "2026-02")
    _report(business, book=jan, period="2026-01", version=1, status="draft", profit_cents=111_100)
    _report(business, book=jan, period="2026-01", version=2, status="reviewed", profit_cents=222_200)
    _report(business, book=feb, period="2026-02", version=1, status="reviewed", profit_cents=100_000)

    jan_only = _suggest(business, jan, "2026-01")
    assert jan_only["suggested_profit_cents"] == 222_200
    assert jan_only["source_period"] == "2026-01"
    assert jan_only["source_version"] == 2
    assert jan_only["is_final_report"] is True

    # 1 月换版 v3 后，2 月累计立刻按新版重算。
    _report(business, book=jan, period="2026-01", version=3, status="published", profit_cents=300_000)
    feb_ytd = _suggest(business, feb, "2026-02")
    assert feb_ytd["suggested_profit_cents"] == 400_000
    assert feb_ytd["months"][0]["version"] == 3


def test_trial_month_inside_ytd_marks_result_as_not_final(business):
    jan = _ensure_book(business, "2026-01")
    feb = _ensure_book(business, "2026-02")
    _report(business, book=jan, period="2026-01", version=1, status="reviewed", profit_cents=500_000)
    _report(business, book=feb, period="2026-02", version=1, status="validated", profit_cents=150_000)

    result = _suggest(business, feb, "2026-02")

    assert result["suggested_profit_cents"] == 650_000
    assert result["source_kind"] == "trial_report"
    assert result["is_final_report"] is False
    assert "试算" in (result["notice"] or "")


def test_missing_prior_month_returns_none_instead_of_single_month_profit(business):
    """缺少 1 月快照时，2 月绝不能直接把 2 月单月利润当成本年累计。"""
    feb = _ensure_book(business, "2026-02")
    _report(business, book=feb, period="2026-02", version=1, status="reviewed", profit_cents=-200_000)

    result = _suggest(business, feb, "2026-02")

    assert result["suggested_profit_cents"] is None
    assert result["missing_months"] == ["2026-01"]
    assert "2026-01" in result["notice"]
    assert "账载税额" in result["notice"]


def test_cross_year_does_not_leak_previous_year_into_january(business):
    dec = _ensure_book(business, "2026-12")
    jan_next = _ensure_book(business, "2027-01")
    _report(business, book=dec, period="2026-12", version=1, status="published", profit_cents=9_000_000)
    _report(business, book=jan_next, period="2027-01", version=1, status="reviewed", profit_cents=120_000)

    result = _suggest(business, jan_next, "2027-01")

    assert result["suggested_profit_cents"] == 120_000
    assert result["period_range"] == "2027-01..2027-01"
    assert result["month_count"] == 1


def test_multiple_books_in_same_month_refuses_auto_merge(business):
    jan_a = _ensure_book(business, "2026-01", name="1月主账套")
    jan_b = _ensure_book(business, "2026-01", name="1月副账套", force_new=True)
    _report(business, book=jan_a, period="2026-01", version=1, status="reviewed", profit_cents=100_000)
    _report(business, book=jan_b, period="2026-01", version=1, status="reviewed", profit_cents=200_000)

    result = _suggest(business, jan_a, "2026-01")

    assert result["suggested_profit_cents"] is None
    assert result["ambiguous_months"] == ["2026-01"]
    assert "多个账套" in result["notice"]


def test_superseded_expired_and_missing_profit_statement_are_never_used(business):
    jan = _ensure_book(business, "2026-01")
    _report(business, book=jan, period="2026-01", version=1, status="superseded", profit_cents=400_000)
    _report(business, book=jan, period="2026-01", version=2, status="expired", profit_cents=500_000)
    _report(business, book=jan, period="2026-01", version=3, status="reviewed", profit_cents=None)

    result = _suggest(business, jan, "2026-01")

    assert result["suggested_profit_cents"] is None
    assert result["source_kind"] is None
    assert "账载税额" in result["notice"]
