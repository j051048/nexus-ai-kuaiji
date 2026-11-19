"""PLT09-B: the five-company measurement ledger keeps unknown and zero apart."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "plt09_measurement.py"


def load_module():
    spec = importlib.util.spec_from_file_location("plt09_measurement", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def measure():
    return load_module()


def test_blank_ledger_covers_five_companies_fifteen_roles_and_two_periods(measure):
    rows = measure.blank_rows(("2026-10", "2026-11"))
    assert len(rows) == 150
    assert {row.company_code for row in rows} == {
        "taxi",
        "hospital",
        "property",
        "advertising",
        "trading",
    }
    assert {row.role_code for row in rows} == {f"R{index:02d}" for index in range(1, 16)}
    assert all(row.handled_minutes is None for row in rows)
    assert all(row.review_minutes is None for row in rows)
    assert measure.summarize(rows) == {
        "rows": 150,
        "unknown_rows": 150,
        "partial_rows": 0,
        "complete_rows": 0,
        "measured_zeroes": 0,
        "status": "未采集",
    }


def test_known_zero_is_not_counted_as_unknown(measure):
    rows = measure.blank_rows(("2026-10",))
    taxi = [
        row
        for row in rows
        if row.company_code == "taxi" and row.role_code == "R01"
    ][0]
    filled = measure.Cell(
        **{
            **taxi.__dict__,
            "business_volume": 0,
            "handled_minutes": 0,
            "review_minutes": 0,
            "waiting_internal_minutes": 0,
            "waiting_external_minutes": 0,
            "rework_count": 0,
            "line_excel_count": 0,
            "expedite_count": 0,
            "first_pass_numerator": 0,
            "first_pass_denominator": 0,
            "error_review_count": 0,
            "error_post_count": 0,
            "transferred_minutes": 0,
            "external_support_minutes": 0,
            "source": "合成演练",
        }
    )
    rows[rows.index(taxi)] = filled
    summary = measure.summarize(rows)
    assert summary["unknown_rows"] == 74
    assert summary["complete_rows"] == 1
    assert summary["measured_zeroes"] == 14
    assert summary["status"] == "部分采集"


def test_partial_cell_is_not_complete(measure):
    rows = measure.blank_rows(("2026-10",))
    rows[0] = measure.Cell(
        **{**rows[0].__dict__, "business_volume": 3, "source": "合成演练"}
    )
    summary = measure.summarize(rows)
    assert summary["partial_rows"] == 1
    assert summary["unknown_rows"] == 74
    assert summary["complete_rows"] == 0
    assert summary["status"] == "部分采集"
