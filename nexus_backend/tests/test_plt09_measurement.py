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


# --- 反作弊判定：SCORECARD.md 七条规则 ------------------------------------


def _baseline(measure, **overrides):
    defaults = dict(
        period="2026-09",
        review_backlog=10,
        error_post_count=2,
        transferred_minutes=600,
        business_volume=60,
        duty_scope="15人/R01-R15",
        external_support_minutes=0,
        external_support_recorded=True,
    )
    defaults.update(overrides)
    return measure.Aggregate(**defaults)


def _current(measure, **overrides):
    defaults = dict(
        period="2026-11",
        review_backlog=10,
        error_post_count=2,
        transferred_minutes=580,
        business_volume=60,
        duty_scope="15人/R01-R15",
        external_support_minutes=0,
        external_support_recorded=True,
    )
    defaults.update(overrides)
    return measure.Aggregate(**defaults)


def _filled_rows(measure):
    rows = measure.blank_rows(("2026-10",))
    filled = []
    for row in rows:
        values = {field: 1 for field in measure.NUMERIC_FIELDS}
        filled.append(measure.Cell(**{**row.__dict__, **values, "source": "合成演练"}))
    return filled


def test_clean_period_is_only_handed_to_manual_review(measure):
    result = measure.evaluate(
        _current(measure), _baseline(measure), rows=_filled_rows(measure)
    )
    assert result["verdict"] == measure.VERDICT_MANUAL_REVIEW
    assert result["reasons"] == []
    assert result["reduction_reportable"] is False


def test_review_backlog_rise_blocks_success(measure):
    result = measure.evaluate(
        _current(measure, review_backlog=12),
        _baseline(measure),
        rows=_filled_rows(measure),
    )
    assert result["verdict"] == measure.VERDICT_CANNOT_JUDGE
    assert [item["code"] for item in result["reasons"]] == ["review_backlog_up"]


def test_post_error_rise_blocks_success(measure):
    result = measure.evaluate(
        _current(measure, error_post_count=3),
        _baseline(measure),
        rows=_filled_rows(measure),
    )
    assert result["verdict"] == measure.VERDICT_CANNOT_JUDGE
    assert [item["code"] for item in result["reasons"]] == ["post_error_up"]


def test_transfer_time_rise_is_compared_per_object(measure):
    # 业务量翻倍时总时间更高，但每对象时间更低，不算上升。
    result = measure.evaluate(
        _current(measure, transferred_minutes=900, business_volume=120),
        _baseline(measure),
        rows=_filled_rows(measure),
    )
    assert result["reasons"] == []

    worse = measure.evaluate(
        _current(measure, transferred_minutes=900, business_volume=60),
        _baseline(measure),
        rows=_filled_rows(measure),
    )
    assert [item["code"] for item in worse["reasons"]] == ["transfer_time_up"]


def test_scope_mismatch_blocks_success(measure):
    result = measure.evaluate(
        _current(measure, duty_scope="5人/合并岗位"),
        _baseline(measure),
        rows=_filled_rows(measure),
    )
    assert result["verdict"] == measure.VERDICT_CANNOT_JUDGE
    assert [item["code"] for item in result["reasons"]] == ["scope_mismatch"]


def test_unrecorded_external_support_blocks_success(measure):
    hidden = measure.evaluate(
        _current(measure, external_support_minutes=0, external_support_recorded=False),
        _baseline(measure),
        rows=_filled_rows(measure),
    )
    assert [item["code"] for item in hidden["reasons"]] == ["external_support_unrecorded"]

    uncollected = measure.evaluate(
        _current(measure, external_support_minutes=None),
        _baseline(measure),
        rows=_filled_rows(measure),
    )
    assert [item["code"] for item in uncollected["reasons"]] == ["external_support_unrecorded"]


def test_insufficient_coverage_and_unknown_as_zero_block_success(measure):
    rows = measure.blank_rows(("2026-10",))
    empty = measure.evaluate(_current(measure), _baseline(measure), rows=rows)
    codes = {item["code"] for item in empty["reasons"]}
    assert codes == {"coverage_insufficient"}

    sneaky = measure.Cell(
        **{**rows[0].__dict__, "handled_minutes": 0, "source": "未采集"}
    )
    rows[0] = sneaky
    mixed = measure.evaluate(_current(measure), _baseline(measure), rows=rows)
    codes = {item["code"] for item in mixed["reasons"]}
    assert codes == {"coverage_insufficient", "unknown_as_zero"}


def test_missing_rows_are_reported_not_ignored(measure):
    result = measure.evaluate(_current(measure), _baseline(measure))
    assert [item["code"] for item in result["reasons"]] == ["coverage_missing"]


def test_denominator_change_must_be_disclosed(measure):
    hidden = measure.evaluate(
        _current(measure, denominator_changed=True),
        _baseline(measure),
        rows=_filled_rows(measure),
    )
    assert [item["code"] for item in hidden["reasons"]] == [
        "denominator_change_undisclosed"
    ]

    disclosed = measure.evaluate(
        _current(
            measure,
            denominator_changed=True,
            denominator_note="过账对象留在当期分母，报告中已披露",
        ),
        _baseline(measure),
        rows=_filled_rows(measure),
    )
    assert disclosed["verdict"] == measure.VERDICT_MANUAL_REVIEW


def test_evaluation_never_reports_a_reduction_or_saving(measure):
    result = measure.evaluate(
        _current(measure, review_backlog=1, error_post_count=0),
        _baseline(measure),
        rows=_filled_rows(measure),
    )
    assert result["reduction_reportable"] is False
    assert "saving" not in str(result).lower()
    assert "减员" in str(result["note"])
