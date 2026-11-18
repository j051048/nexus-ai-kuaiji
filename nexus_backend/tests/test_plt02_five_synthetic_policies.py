"""PLT02-D: five synthetic policies stay non-formal and need independent approval."""

from app.accounting.models import BookCreate, CompanyCreate
from app.opening.models import PolicyCreate
from app.opening.service import OpeningService
from app.reports.service import ReportService
from policy_fixtures import approve_policy_independently
from test_business import business  # noqa: F401


COMPANIES = [
    "澄光科技",
    "禾田商贸",
    "青屿设计",
    "启程物流",
    "远山广告",
]


def _ledger_id(case, book_id):
    with case.db.transaction() as conn:
        return conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book_id,)
        ).fetchone()["ledger_id"]


def test_five_synthetic_policies_require_independent_approval_and_stay_trial(business):
    case = business
    opening = OpeningService(case.db)
    reports = ReportService(case.db)
    policies = []
    for index, company_name in enumerate(COMPANIES):
        company = case.directory.create_company(
            case.manager, CompanyCreate(name=f"PLT02-D {company_name}（虚构）")
        )
        book = case.ledger.create_book(
            case.manager,
            BookCreate(
                name=f"PLT02-D {company_name} 账套",
                period="2026-10",
                company_id=company["id"],
            ),
        )
        policy = opening.create_policy(
            case.manager,
            PolicyCreate(
                ledger_id=_ledger_id(case, book["id"]),
                standard_kind="small_enterprise",
                taxpayer_kind="general",
                effective_from="2026-01",
                effective_to="2026-12",
                reason=f"PLT02-D {company_name} 合成演练政策",
                is_synthetic=True,
            ),
        )
        approver, approved = approve_policy_independently(
            opening, case.db, case.manager, policy["id"]
        )
        assert approver.user_id != approved["created_by"]
        policies.append((company, approved))

    assert len(policies) == 5
    with case.db.transaction() as conn:
        for company, policy in policies:
            assert policy["is_synthetic"] == 1
            assert policy["approved_by"] != policy["created_by"]
            template = reports._template_for_standard(conn, "small_enterprise")
            content = {
                "checks": {
                    "balance_sheet_passed": True,
                    "profit_statement_passed": True,
                    "cash_flow_passed": True,
                    "unclassified_cash_cents": 0,
                    "unclassified_cash_count": 0,
                    "unclassified_cash_absolute_cents": 0,
                }
            }
            book = conn.execute(
                "SELECT * FROM books WHERE company_id=? AND period='2026-10'",
                (company["id"],),
            ).fetchone()
            reasons = reports._formal_reasons(conn, book, policy, template, content)
            assert "核算政策为合成演练，不可正式出表" in reasons
