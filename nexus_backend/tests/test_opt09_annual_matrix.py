"""OPT09-C：年度矩阵按授权范围返回企业 × 12 月，不把未建账写成已完成。"""

import pytest

from app.accounting.domain import AccountingError
from app.cases.service import CaseService
from app.monthly.workbench import MonthlyWorkbenchService
from test_business import business


def service(case):
    return MonthlyWorkbenchService(case.db, case.ledger, case.service, CaseService(case.db, case.service))


def test_annual_matrix_returns_twelve_months_and_honest_stage(business):
    matrix = service(business).annual(business.manager, 2026)
    assert matrix["year"] == 2026
    assert matrix["months"] == [f"2026-{month:02d}" for month in range(1, 13)]
    rows = {row["company_id"]: row for row in matrix["rows"]}
    assert business.company["id"] in rows
    cell = next(
        item for item in rows[business.company["id"]]["months"] if item["period"] == "2026-10"
    )
    assert cell["book_count"] == 1
    assert cell["stage"] in {"open", "review", "materials", "delivery", "closed", "delivered"}
    assert cell["label"]
    assert cell["next"]
    assert all(item["month"] for item in rows[business.company["id"]]["months"])
    assert matrix["scope"] == "authorized_books_v1"


def test_annual_matrix_rejects_invalid_year(business):
    with pytest.raises(AccountingError):
        service(business).annual(business.manager, 1999)
    with pytest.raises(AccountingError):
        service(business).annual(business.manager, "2026")  # type: ignore[arg-type]
