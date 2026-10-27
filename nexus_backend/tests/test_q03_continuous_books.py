"""Q03: continuous-period books over one long-term ledger.

The contract under test: a range query starts from the opening package that is
bound to the *first* period in the range, never re-adds the opening packages of
later periods, reports every missing, unauthorized, open or discontinuous
period instead of returning a quietly incomplete total, and exposes one
fingerprint that both the page and the CSV carry.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.accounting.domain import AccountingError, Actor
from app.accounting.models import BookCreate, BookMemberGrant, FirmMemberGrant
from app.accounting.seed import LOCAL_ACTOR
from app.ledgers.models import PeriodLink
from app.ledgers.service import LedgersService
from app.main import create_app
from app.opening.models import (
    AccountCreate,
    MappingCreate,
    OpeningCommand,
    OpeningImport,
    OpeningLineInput,
    OpeningPackageCreate,
    PolicyCreate,
)
from app.opening.service import OpeningService
from app.posting.models import ManualLine, ManualVoucher, OpeningBind, PostDraft
from app.posting.presentation import (
    export_range_account_ledger,
    export_range_trial_balance,
)
from app.posting.service import PostingService
from period_fixtures import restore_period_state
from test_business import business
from review_policy_fixtures import post_reviewed_manual
from test_posting_service import admin, approved_draft, frozen_opening, ledger_of


def bound(case):
    """October book with a frozen opening package, bound and posting-ready."""
    opening = frozen_opening(case)
    posting = PostingService(case.db)
    posting.bind_opening(
        case.manager,
        OpeningBind(
            book_id=case.book["id"],
            package_version_id=opening["version_id"],
            reason="Q03 绑定期初",
        ),
    )
    return posting, opening


def post(case, posting, book_id, key, day, debit, credit, description="Q03 跨月凭证"):
    bank, payable = "1002", "2202"
    accounts = {}
    with case.db.transaction() as conn:
        for row in conn.execute(
            "SELECT a.id,v.version,v.code FROM ledger_accounts a JOIN ledger_account_versions v "
            "ON v.account_id=a.id AND v.version=a.current_version WHERE a.ledger_id=?",
            (ledger_of(case),),
        ):
            accounts[row["code"]] = (row["id"], row["version"])
    return post_reviewed_manual(
        posting,
        case.manager,
        ManualVoucher(
            command_key=key,
            book_id=book_id,
            voucher_date=day,
            description=description,
            lines=[
                ManualLine(
                    account_id=accounts[bank][0],
                    account_version=accounts[bank][1],
                    debit=debit,
                ),
                ManualLine(
                    account_id=accounts[payable][0],
                    account_version=accounts[payable][1],
                    credit=credit,
                ),
            ],
        ),
    )


def second_month(case, period, opening_lines, *, key):
    """Create a physical book for ``period``, link it to the same ledger, bind opening."""
    book = case.ledger.create_book(
        case.manager,
        BookCreate(name=f"{period} 账套", period=period, company_id=case.company["id"]),
    )
    with case.db.transaction() as conn:
        own_ledger = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book["id"],)
        ).fetchone()["ledger_id"]
    LedgersService(case.db).link_period(
        case.manager,
        case.book["id"],
        PeriodLink(
            source_book_id=book["id"],
            reason="Q03 关联长期账套",
            expected_ledger_id=own_ledger,
        ),
    )
    ledger_id = ledger_of(case)
    opening = OpeningService(case.db)
    with case.db.transaction() as conn:
        policy_id = conn.execute(
            "SELECT policy_id FROM opening_packages WHERE ledger_id=? AND target_period=?",
            (ledger_id, "2026-10"),
        ).fetchone()["policy_id"]
        accounts = {
            row["code"]: (row["id"], row["version"])
            for row in conn.execute(
                "SELECT a.id,v.version,v.code FROM ledger_accounts a JOIN ledger_account_versions v "
                "ON v.account_id=a.id AND v.version=a.current_version WHERE a.ledger_id=?",
                (ledger_id,),
            )
        }
    package = opening.create_package(
        case.manager,
        OpeningPackageCreate(ledger_id=ledger_id, target_period=period, policy_id=policy_id),
    )
    version = opening.import_version(
        case.manager,
        package["package"]["id"],
        OpeningImport(
            idempotency_key=key,
            reason="Q03 次月期初",
            lines=[
                OpeningLineInput(
                    account_id=accounts[code][0],
                    account_version=accounts[code][1],
                    auxiliary_values=aux,
                    year_opening=amount,
                    ytd_debit="0.00",
                    ytd_credit="0.00",
                    opening=amount,
                )
                for code, amount, *rest in opening_lines
                for aux in [rest[0] if rest else {}]
            ],
        ),
    )
    opening.validate_version(case.manager, version["version"]["id"])
    opening.submit_version(
        case.manager,
        version["version"]["id"],
        OpeningCommand(expected_version=1, reason="提交次月期初"),
    )
    reviewer = admin(case)
    opening.approve_version(
        reviewer,
        version["version"]["id"],
        OpeningCommand(expected_version=1, reason="独立核准次月期初"),
    )
    opening.freeze_version(
        reviewer,
        version["version"]["id"],
        OpeningCommand(expected_version=1, reason="冻结次月期初"),
    )
    for code in accounts:
        opening.create_mapping(
            case.manager,
            MappingCreate(
                book_id=book["id"],
                book_account_code=code,
                account_id=accounts[code][0],
                account_version=accounts[code][1],
                reason="Q03 次月映射",
            ),
        )
    posting = PostingService(case.db)
    posting.bind_opening(
        case.manager,
        OpeningBind(
            book_id=book["id"],
            package_version_id=version["version"]["id"],
            reason="Q03 绑定次月期初",
        ),
    )
    return book


def two_months(case, *, november_bank="10000.30", november_payable="-10000.30"):
    posting, opening = bound(case)
    post(case, posting, case.book["id"], "q03-oct-001", "2026-10-15", "0.30", "0.30")
    book = second_month(
        case,
        "2026-11",
        [("1002", november_bank), ("2202", november_payable)],
        key="q03-nov-opening",
    )
    post(case, posting, book["id"], "q03-nov-001", "2026-11-10", "12.00", "12.00")
    return posting, opening, book


def test_range_rejects_invalid_and_inverted_periods(business):
    posting, _ = bound(business)
    ledger_id = ledger_of(business)
    with pytest.raises(AccountingError) as bad:
        posting.range_trial_balance(business.manager, ledger_id, "2026-10", "2026-9")
    assert "期间" in bad.value.detail
    with pytest.raises(AccountingError):
        posting.range_trial_balance(business.manager, ledger_id, "2026-10", "2026-13")


def test_range_enumerates_months_across_a_year_boundary():
    posting = PostingService.__new__(PostingService)
    assert posting._months("2025-12", "2026-02") == ["2025-12", "2026-01", "2026-02"]
    assert posting._months("2026-10", "2026-10") == ["2026-10"]
    with pytest.raises(AccountingError) as too_long:
        posting._months("2000-01", "2030-01")
    assert "240" in too_long.value.detail


def test_range_opens_from_first_period_and_sums_activity(business):
    posting, _, _ = two_months(business)
    result = posting.range_trial_balance(
        business.manager, ledger_of(business), "2026-10", "2026-11"
    )
    rows = {row["code"]: row for row in result["rows"]}
    assert rows["1002"]["opening_cents"] == 1000000
    assert rows["1002"]["debit_cents"] == 1230
    assert rows["1002"]["closing_cents"] == 1001230
    assert rows["2202"]["closing_cents"] == -1001230
    assert result["start"] == "2026-10" and result["end"] == "2026-11"
    assert [item["period"] for item in result["coverage"]] == ["2026-10", "2026-11"]


def test_range_does_not_add_later_opening_packages(business):
    posting, _, _ = two_months(business)
    result = posting.range_trial_balance(
        business.manager, ledger_of(business), "2026-10", "2026-11"
    )
    rows = {row["code"]: row for row in result["rows"]}
    # November's frozen opening repeats October's closing: the range must not add it.
    assert rows["1002"]["opening_cents"] == 1000000
    assert rows["2202"]["opening_cents"] == -1000000
    assert {item["code"] for item in result["blockers"]} == {"period_open"}


def test_range_reports_open_period_without_hiding_it(business):
    posting, _, _ = two_months(business)
    result = posting.range_trial_balance(
        business.manager, ledger_of(business), "2026-10", "2026-11"
    )
    assert result["complete"] is False
    assert {item["code"] for item in result["blockers"]} == {"period_open"}
    assert {item["period"] for item in result["blockers"]} == {"2026-10", "2026-11"}
    restore_period_state(business.db, business.book["id"], "closed")
    closed = posting.range_trial_balance(
        business.manager, ledger_of(business), "2026-10", "2026-10"
    )
    assert closed["complete"] is True
    assert closed["blockers"] == []


def test_range_reports_missing_month(business):
    posting, _ = bound(business)
    second_month(
        business, "2026-12", [("1002", "10000.00"), ("2202", "-10000.00")], key="q03-dec-opening"
    )
    result = posting.range_trial_balance(
        business.manager, ledger_of(business), "2026-10", "2026-12"
    )
    assert result["complete"] is False
    missing = [item for item in result["blockers"] if item["code"] == "missing_period"]
    assert [item["period"] for item in missing] == ["2026-11"]
    assert {item["period"] for item in result["coverage"]} == {"2026-10", "2026-11", "2026-12"}
    assert {item["period"]: item["status"] for item in result["coverage"]}["2026-11"] == "missing"


def test_range_reports_opening_discontinuity(business):
    # Balanced package, different allocation: totals agree, accounts do not.
    posting, _, _ = two_months(business, november_bank="0.00", november_payable="0.00")
    result = posting.range_trial_balance(
        business.manager, ledger_of(business), "2026-10", "2026-11"
    )
    mismatched = [item for item in result["blockers"] if item["code"] == "opening_mismatch"]
    assert [item["period"] for item in mismatched] == ["2026-11"]
    assert result["complete"] is False


def test_range_reports_unauthorized_period_and_hides_fully_invisible_ledger(business):
    posting, _, _ = two_months(business)
    # The firm reviewer can read the October book only: November must surface as
    # unauthorized rather than silently joining the total.
    result = posting.range_trial_balance(
        business.actors["accountant"], ledger_of(business), "2026-10", "2026-11"
    )
    unauthorized = [item for item in result["blockers"] if item["code"] == "unauthorized"]
    assert [item["period"] for item in unauthorized] == ["2026-11"]
    assert result["complete"] is False


def test_range_rejects_ledger_the_actor_cannot_see(business):
    posting, _ = bound(business)
    with business.db.transaction() as conn:
        ledger_id = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (business.other_book["id"],)
        ).fetchone()["ledger_id"]
    with pytest.raises(AccountingError) as denied:
        posting.range_trial_balance(business.actors["employee"], ledger_id, "2026-10", "2026-10")
    assert denied.value.status in {403, 404}


def test_range_fingerprint_is_stable_and_scoped(business):
    posting, _, _ = two_months(business)
    ledger_id = ledger_of(business)
    first = posting.range_trial_balance(business.manager, ledger_id, "2026-10", "2026-11")
    again = posting.range_trial_balance(business.manager, ledger_id, "2026-10", "2026-11")
    shorter = posting.range_trial_balance(business.manager, ledger_id, "2026-10", "2026-10")
    assert first["fingerprint"] == again["fingerprint"]
    assert first["fingerprint"] != shorter["fingerprint"]
    assert len(first["fingerprint"]) == 64


def test_account_ledger_range_carries_balance_across_months(business):
    posting, _, _ = two_months(business)
    with business.db.transaction() as conn:
        bank = conn.execute(
            "SELECT a.id,v.version FROM ledger_accounts a JOIN ledger_account_versions v "
            "ON v.account_id=a.id AND v.version=a.current_version WHERE a.ledger_id=? AND v.code='1002'",
            (ledger_of(business),),
        ).fetchone()
    result = posting.range_account_ledger(
        business.manager, ledger_of(business), bank["id"], "2026-10", "2026-11"
    )
    assert result["opening_cents"] == 1000000
    assert [entry["period"] for entry in result["entries"]] == ["2026-10", "2026-11"]
    assert result["entries"][0]["balance_cents"] == 1000030
    assert result["entries"][1]["balance_cents"] == 1001230
    assert result["closing_cents"] == 1001230


def test_range_csv_matches_page_and_escapes_formulas(business):
    posting, _, book = two_months(business)
    post(
        case=business,
        posting=posting,
        book_id=book["id"],
        key="q03-nov-formula",
        day="2026-11-20",
        debit="1.00",
        credit="1.00",
        description='=HYPERLINK("http://example.invalid")',
    )
    ledger_id = ledger_of(business)
    page = posting.range_trial_balance(business.manager, ledger_id, "2026-10", "2026-11")
    csv, fingerprint = export_range_trial_balance(
        posting, business.manager, ledger_id, "2026-10", "2026-11"
    )
    assert fingerprint == page["fingerprint"]
    assert csv.startswith("\ufeff")
    assert "科目编码,科目名称,期初余额（借正贷负）,本期借方,本期贷方,期末余额（借正贷负）" in csv
    assert "1002,银行存款,10000.00,13.30,0.00,10013.30" in csv
    detail, detail_fingerprint = export_range_account_ledger(
        posting, business.manager, ledger_id, _bank_id(business), "2026-10", "2026-11"
    )
    assert len(detail_fingerprint) == 64
    assert "'=HYPERLINK" in detail
    assert "\n=HYPERLINK" not in detail


def _bank_id(case):
    with case.db.transaction() as conn:
        return conn.execute(
            "SELECT a.id FROM ledger_accounts a JOIN ledger_account_versions v "
            "ON v.account_id=a.id AND v.version=a.current_version WHERE a.ledger_id=? AND v.code='1002'",
            (ledger_of(case),),
        ).fetchone()["id"]


def test_http_range_endpoints_require_period_bounds(tmp_path):
    app = create_app(tmp_path / "q03-http.db", seed=True)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 45678)) as client:
        token = client.get("/api/session").json()["token"]
        headers = {"Authorization": "Bearer " + token}
        with app.state.service.db.transaction() as conn:
            book = conn.execute("SELECT id FROM books LIMIT 1").fetchone()
            ledger_id = conn.execute(
                "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book["id"],)
            ).fetchone()["ledger_id"]
        bad = client.get(
            f"/api/ledgers/{ledger_id}/trial-balance",
            params={"start": "2026-11", "end": "2026-10"},
            headers=headers,
        )
        assert bad.status_code in {409, 422}
        missing = client.get(f"/api/ledgers/{ledger_id}/trial-balance", headers=headers)
        assert missing.status_code in {409, 422}
