"""Source ingestion limits apply to rejected rows and strict version identity."""

import pytest

from app.accounting.domain import AccountingError
from app.tax import sources
from test_business import business  # noqa: F401
from test_tax04_sources import xlsx
from test_tax06_fees import COLS, ROWS


def ingest(case, rows, version=1):
    return sources.TaxSourceAdapter(case.db).import_source(
        case.manager, case.book["id"], filename="input-limits.xlsx",
        data=xlsx({"运输明细": rows}), source_system="合成输入边界",
        external_id="SOURCE-LIMIT", version=version, sheet_name="运输明细",
        column_map=COLS, rule_version="synthetic-v1",
    )


def test_malformed_rows_cannot_bypass_source_row_limit(business, monkeypatch):
    monkeypatch.setattr(sources, "MAX_ROWS", 2)
    bad = [*ROWS[1]]
    bad[3] = "not-a-number"
    with pytest.raises(AccountingError) as rejected:
        ingest(business, [ROWS[0], bad, bad, bad])
    assert rejected.value.status == 413
    assert sources.TaxSourceAdapter(business.db).list_sources(
        business.manager, business.book["id"]
    )["items"] == []


def test_exact_row_limit_keeps_invalid_rows_visible(business, monkeypatch):
    monkeypatch.setattr(sources, "MAX_ROWS", 2)
    bad = [*ROWS[1]]
    bad[3] = "not-a-number"
    result = ingest(business, [ROWS[0], bad, bad])
    assert len(result["rows"]) == 2
    assert all(row["status"] == "needs_review" for row in result["rows"])


def test_boolean_cannot_be_used_as_source_version(business):
    with pytest.raises(AccountingError) as rejected:
        ingest(business, ROWS[:2], version=True)
    assert rejected.value.status == 422
