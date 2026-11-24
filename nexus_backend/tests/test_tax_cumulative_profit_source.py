from __future__ import annotations

from test_business import business  # noqa: F401  (pytest fixture)

from app.tax import profit_source


def _report(case, *, period: str, version: int, status: str, profit_cents: int | None) -> str:
    """Insert one report snapshot the way the report pipeline would store it."""
    from app.accounting.domain import AccountingError  # noqa: F401  (kept for parity)

    identifier = f"run-{period}-{status}-{version}"
    mode = "formal" if status in ("reviewed", "published") else "trial"
    policy_version = 1 if mode == "formal" else None
    policy_approver = case.manager.user_id if mode == "formal" else None
    content = (
        {"profit_statement": {"profit_cents": profit_cents, "revenue_cents": 1_000_000}}
        if profit_cents is not None
        else {"balance_sheet": {"assets_cents": 0}}
    )
    import json

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
                case.book["id"],
                period,
                "small_enterprise",
                1,
                status,
                version,
                "fingerprint-" + identifier,
                json.dumps(content),
                "hash-" + identifier,
                case.manager.user_id,
                "2026-10-31T10:00:00+00:00",
                mode,
                policy_version,
                policy_approver,
            ),
        )
    return identifier


def _suggest(case, period: str = "2026-10"):
    with case.db.transaction(actor=case.manager) as conn:
        book = case.service.access.book(conn, case.manager, case.book["id"], "read")
        return profit_source.suggest(conn, book, period)


def test_reviewed_report_wins_and_is_final(business):
    period = business.book["period"]
    _report(business, period=period, version=1, status="draft", profit_cents=111_100)
    _report(business, period=period, version=2, status="reviewed", profit_cents=222_200)

    result = _suggest(business, period)

    assert result["suggested_profit_cents"] == 222_200
    assert result["source_kind"] == "reviewed_report"
    assert result["source_label"] == "已复核利润表快照"
    assert result["source_period"] == period
    assert result["source_version"] == 2
    assert result["is_final_report"] is True
    assert result["notice"] is None


def test_trial_report_is_labelled_as_not_final(business):
    period = business.book["period"]
    _report(business, period=period, version=1, status="validated", profit_cents=333_300)

    result = _suggest(business, period)

    assert result["suggested_profit_cents"] == 333_300
    assert result["source_kind"] == "trial_report"
    assert result["is_final_report"] is False
    assert "试算" in (result["notice"] or "")


def test_superseded_and_expired_reports_are_never_used(business):
    period = business.book["period"]
    _report(business, period=period, version=1, status="superseded", profit_cents=400_000)
    _report(business, period=period, version=2, status="expired", profit_cents=500_000)

    result = _suggest(business, period)

    assert result["suggested_profit_cents"] is None
    assert result["source_kind"] is None


def test_missing_statement_of_profit_is_not_guessed(business):
    period = business.book["period"]
    _report(business, period=period, version=1, status="reviewed", profit_cents=None)

    result = _suggest(business, period)

    assert result["suggested_profit_cents"] is None


def test_no_source_explains_it_never_falls_back_to_ledger_tax(business):
    result = _suggest(business, business.book["period"])

    assert result["suggested_profit_cents"] is None
    assert result["source_label"] is None
    assert result["is_final_report"] is False
    # 明确写出「不拿账载税额代替」，避免任何人把底稿税额当成利润。
    assert "账载税额" in result["notice"]
    assert "不猜" in result["notice"] or "不猜测" in result["notice"]


def test_other_period_never_leaks_into_this_period(business):
    period = business.book["period"]
    _report(business, period="2026-09", version=9, status="published", profit_cents=999_900)

    result = _suggest(business, period)

    assert result["suggested_profit_cents"] is None
