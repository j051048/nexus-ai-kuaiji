"""简报来源契约：只有已发布冻结报表才是客户可见成果，其余一律留空。

用脚本化的只读连接验证状态机与形状校验；数据库触发器（close 插入守卫）
留给真实服务测试，这里不绕过它们。
"""

import json
from uuid import uuid4

import pytest

from app.monthly.briefing_source import _amounts, _parse_close, _parse_package, _parse_report
from app.monthly.briefing_source import resolve_source

BOOK = {"id": "book-a", "company_id": "company-a", "period": "2026-10", "name": "客户甲"}


def sha():
    return uuid4().hex + uuid4().hex


def content(revenue=100000, expenses=30000, profit=70000, receivable=0, payable=0, tax=0, **broken):
    body = {
        "balance_sheet": {
            "items": [
                {"code": "1122", "name": "应收账款", "amount_cents": receivable},
                {"code": "2202", "name": "应付账款", "amount_cents": payable},
                {"code": "2221.01", "name": "应交增值税", "amount_cents": tax},
            ]
        },
        "profit_statement": {
            "revenue_cents": revenue,
            "expense_cents": expenses,
            "profit_cents": profit,
            "items": [{"code": "6602.02", "name": "办公费", "amount_cents": expenses}],
        },
        "checks": {},
    }
    body.update(broken)
    return body


def report_row(**changes):
    row = {
        "id": "report-1",
        "version": 1,
        "template_id": "balance-sheet",
        "template_version": 1,
        "status": "published",
        "content_hash": sha(),
        "inputs_fingerprint": sha(),
        "published_at": "2026-10-31T10:10:00+00:00",
        "published_by": "user-reviewer",
        "supersedes_id": None,
        "content_json": json.dumps(content(), ensure_ascii=False),
    }
    row.update(changes)
    return row


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class Conn:
    """按 SQL 关键字返回脚本化结果，模拟只读查询。"""

    def __init__(self, **tables):
        self.tables = tables

    def execute(self, sql, params=()):  # noqa: ARG002
        normalized = " ".join(sql.split())
        for key in (
            "report_runs",
            "internal_close_runs",
            "periods",
            "monthly_delivery_packages",
            "exports",
            "tax_workpapers",
            "delivery_notifications",
            "monthly_delivery_acknowledgements",
        ):
            if key in normalized:
                value = self.tables.get(key, [])
                return Cursor(value if isinstance(value, list) else [value])
        return Cursor([])


def source(**tables):
    dialect = tables.pop("dialect", "sqlite")
    return resolve_source(Conn(**tables), BOOK, "2026-10", dialect)


def test_published_frozen_report_is_the_only_client_visible_source():
    result = source(
        report_runs=[report_row()],
        internal_close_runs=[
            {
                "id": "close-1",
                "version": 2,
                "status": "locked",
                "report_run_id": "report-1",
                "locked_at": "2026-10-31T10:30:00+00:00",
                "inputs_fingerprint": sha(),
            }
        ],
    )
    assert result["state"] == "published"
    assert result["client_visible"] is True
    assert result["amounts"]["flows"] == {"income": 100000, "expense": 30000, "net": 70000}
    assert result["amounts"]["tax"] == 0
    assert result["close"]["status"] == "locked"
    assert result["axes"]["internal_close"]["done"] is True
    assert result["axes"]["client_sign"]["done"] is False
    assert result["axis_labels"]["notification"] == "通知送达"


def test_without_published_report_there_are_no_amounts():
    result = source()
    assert result["state"] == "pending_report"
    assert result["client_visible"] is False
    assert result["amounts"] is None and result["report"] is None


def test_report_published_before_close_lock_is_inconsistent():
    result = source(
        report_runs=[report_row()],
        internal_close_runs=[{"id": "c", "version": 1, "status": "draft", "report_run_id": "report-1"}],
        periods=[{"status": "closed"}],
    )
    assert result["state"] == "inconsistent"
    assert result["reason"] == "close_not_locked"
    assert result["client_visible"] is False and result["amounts"] is None


def test_close_pointing_at_another_report_is_inconsistent():
    result = source(
        report_runs=[report_row()],
        internal_close_runs=[
            {"id": "c", "version": 1, "status": "locked", "report_run_id": "report-other"}
        ],
    )
    assert result["state"] == "inconsistent"
    assert result["reason"] == "close_report_mismatch"


def test_package_must_use_the_same_report_version():
    result = source(
        report_runs=[report_row()],
        internal_close_runs=[
            {"id": "c", "version": 1, "status": "locked", "report_run_id": "report-1"}
        ],
        monthly_delivery_packages=[
            {
                "id": "package-1",
                "version": 3,
                "report_id": "report-1",
                "report_version": 2,
                "manifest_sha256": sha(),
                "created_at": "2026-10-31T11:00:00+00:00",
            }
        ],
    )
    assert result["state"] == "inconsistent"
    assert result["reason"] == "package_report_mismatch"


def test_package_axes_need_the_delivered_package():
    package = {
        "id": "package-1",
        "version": 1,
        "report_id": "report-1",
        "report_version": 1,
        "manifest_sha256": sha(),
        "created_at": "2026-10-31T11:00:00+00:00",
    }
    base = {
        "report_runs": [report_row()],
        "internal_close_runs": [
            {"id": "c", "version": 1, "status": "locked", "report_run_id": "report-1"}
        ],
        "monthly_delivery_packages": [package],
    }
    undelivered = source(**base, delivery_notifications=[], monthly_delivery_acknowledgements=[])
    assert undelivered["axes"]["client_sign"]["done"] is False
    delivered = source(
        **base,
        delivery_notifications=[{"total": 1}],
        monthly_delivery_acknowledgements=[{"total": 1}],
    )
    assert delivered["axes"]["notification"]["done"] is True
    assert delivered["axes"]["client_sign"]["done"] is True
    assert delivered["axes"]["tax_filing"]["done"] is False


@pytest.mark.parametrize(
    "broken, reason",
    [
        ({"content_hash": "not-a-sha256"}, "bad_report_hash"),
        ({"inputs_fingerprint": "abc"}, "bad_report_fingerprint"),
        ({"version": 0}, "bad_report_version"),
        ({"content_json": "not-json"}, "bad_report_content"),
        ({"content_json": json.dumps([], ensure_ascii=False)}, "bad_report_content"),
    ],
)
def test_malformed_report_fails_closed(broken, reason):
    parsed = _parse_report(report_row(**broken))
    assert parsed == {"ok": False, "reason": reason}


def test_non_integer_or_non_object_amounts_fail_closed():
    assert _amounts(content(revenue="1000")) == {"ok": False, "reason": "bad_report_amounts"}
    assert _amounts(content(profit_statement=[])) == {"ok": False, "reason": "bad_report_content"}
    assert _amounts({"profit_statement": {}, "balance_sheet": {"items": []}}) == {
        "ok": False,
        "reason": "bad_report_amounts",
    }


def test_close_and_package_parsers_reject_shape_errors():
    assert _parse_close({"status": "draft"}, "report-1")["reason"] == "close_not_locked"
    assert _parse_close(None, "report-1") is None
    assert _parse_package({"report_id": "r", "report_version": 1}, None)["reason"] == (
        "package_without_report"
    )
    assert (
        _parse_package(
            {
                "id": "p",
                "version": 1,
                "report_id": "report-1",
                "report_version": 1,
                "manifest_sha256": "bad",
            },
            {"report_id": "report-1", "version": 1},
        )["reason"]
        == "bad_package_manifest"
    )


def test_pending_and_published_shapes_from_the_service():
    from app.monthly.briefing import BriefingService

    service = BriefingService.__new__(BriefingService)
    book = {"id": "book-a", "name": "客户甲", "period": "2026-10", "company_id": "company-a"}
    company = {"name": "客户甲有限公司"}
    pending = service._pending({"state": "pending_report", "reason": "no_published_report"}, book, company)
    assert pending["client_visible"] is False
    assert pending["flows"] is None and pending["top_expenses"] == []
    assert pending["pending_label"] == "待会计复核 / 发布"
    assert pending["book"]["period"] == "2026-10"

    published = service._published(
        {
            "state": "published",
            "report": {"version": 2, "content_hash": "a" * 64},
            "amounts": {
                "flows": {"income": 100000, "expense": 30000, "net": 70000},
                "receivable": 150000,
                "payable": 80000,
                "tax": 2000,
                "expenses": [
                    {"code": "6602.02", "name": "办公费", "amount_cents": 30000},
                    {"code": "6601", "name": "工资", "amount_cents": 0},
                ],
            },
        },
        book,
        company,
    )
    assert published["client_visible"] is True
    assert published["flows"]["income"] == "1000.00"
    assert published["flows"]["net"] == "700.00"
    assert published["balances"]["receivable"] == "1500.00"
    assert published["tax"]["change"] == "20.00"
    assert [row["name"] for row in published["top_expenses"]] == ["办公费"]
    assert "V2" in published["balances"]["basis"]
