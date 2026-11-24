"""印花税三态：未提供合同 / 确认零业务 / 已按合同计提。

「没给资料」和「确认没有」是两件事。把前者当成 0 元会让会计以为印花税已经算过，
把后者当成未知又会让零业务期间无法结账，所以两者必须是不同的状态。
"""

from contextlib import contextmanager

import pytest

from app.accounting.domain import AccountingError
from app.tax import accrual, four_tax

REVIEWED = {
    "id": "wp-1",
    "version": 3,
    "tax_cents": 30_000,
    "surtax_cents": 3_600,
    "credit_carry_cents": 0,
    "ledger_cents": 30_000,
    "source_digest": "abc123",
    "status": "reviewed",
}


class Conn:
    def __init__(self, row):
        self.row = row

    def execute(self, sql, params=()):
        return self

    def fetchone(self):
        return self.row


class Service:
    def __init__(self, row):
        self.conn = Conn(row)
        self.access = self
        self.db = self

    @contextmanager
    def transaction(self, **_):
        yield self.conn

    def book(self, conn, actor, book_id, capability):
        return {"id": book_id, "period": "2026-10"}


def run(**kwargs):
    defaults = dict(
        surtax_items=[{"code": "city", "rate_bp": 700}],
        cumulative_profit_cents=100_000,
        cit_rate_bp=2_500,
    )
    return four_tax.package(
        Service(REVIEWED), object(), "book-1", "2026-10", **{**defaults, **kwargs}
    )


def test_missing_contracts_are_unprovided_not_zero():
    result = run()
    stamp = result["stamp_duty"]
    assert stamp["status"] == "unprovided"
    assert stamp["total_cents"] is None
    assert stamp["accrued"] is False
    assert "不能视作本期零业务" in stamp["notice"]
    # 其他税种照常计算，不会被印花税拖住。
    assert result["surtax"]["total_cents"] == 2_100
    assert result["cit_prepayment"]["due_cents"] == 25_000


def test_confirmed_zero_business_is_zero_not_unknown():
    result = run(stamp_zero_confirmed=True)
    stamp = result["stamp_duty"]
    assert stamp["status"] == "zero_confirmed"
    assert stamp["total_cents"] == 0
    assert stamp["accrued"] is True
    assert "已确认本期无应税合同" in stamp["notice"]


def test_contracts_are_computed_through_the_engine():
    result = run(
        stamp_contracts=[{"key": "c-1", "kind": "purchase", "cents": 1_000_000}],
        stamp_rates={"purchase": 30},
    )
    stamp = result["stamp_duty"]
    assert stamp["status"] == "computed"
    assert stamp["total_cents"] == 3_000  # 1,000,000 分 * 30bp（0.3%）= 3,000 分
    assert stamp["accrued"] is True
    assert stamp["notice"] is None
    assert [line["key"] for line in stamp["lines"]] == ["c-1"]


def test_zero_confirmed_with_contracts_is_refused():
    with pytest.raises(AccountingError) as error:
        run(
            stamp_zero_confirmed=True,
            stamp_contracts=[{"key": "c-1", "kind": "purchase", "cents": 1_000_000}],
            stamp_rates={"purchase": 30},
        )
    assert error.value.status == 422
    assert "零业务" in error.value.detail


def test_accrual_skips_stamp_duty_when_contracts_are_missing():
    package = run()
    lines = accrual.accrual_lines(package)
    assert all(item["account"] != "2221.04" for item in lines["entries"])
    assert lines["stamp_duty_status"] == "unprovided"
    assert "印花税" in lines["stamp_duty_notice"]
    assert lines["debit_cents"] == lines["credit_cents"] == 2_100 + 25_000
    # 明确告诉会计「本次计提不含印花税」，而不是悄悄少一行。
    assert any("印花税" in note for note in lines["notices"])


def test_accrual_posts_stamp_duty_when_contracts_are_provided():
    package = run(
        stamp_contracts=[{"key": "c-1", "kind": "purchase", "cents": 1_000_000}],
        stamp_rates={"purchase": 30},
    )
    lines = accrual.accrual_lines(package)
    assert any(item["account"] == "2221.04" for item in lines["entries"])
    assert lines["stamp_duty_status"] == "computed"
    assert lines["debit_cents"] == lines["credit_cents"] == 2_100 + 25_000 + 3_000


def test_accrual_reports_zero_business_without_fake_lines():
    package = run(stamp_zero_confirmed=True)
    lines = accrual.accrual_lines(package)
    assert all(item["account"] != "2221.04" for item in lines["entries"])
    assert lines["stamp_duty_status"] == "zero_confirmed"
    assert "已确认本期无应税合同" in lines["stamp_duty_notice"]
