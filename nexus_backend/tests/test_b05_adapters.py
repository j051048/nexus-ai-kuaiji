"""银行双适配器：不同来源归一，坏数据整批拒绝而不是猜。"""

import pytest

from app.treasury.adapters import (
    AdapterError,
    merge,
    parse_bank_csv,
    parse_receipt_json,
)

CSV = """account,serial,date,amount,direction,counterparty
622200001111,20261001-01,2026-10-01,"1,200.50",income,客户甲
622200001111,20261001-02,2026-10-01,80.00,expense,供应商乙
"""


def test_csv_and_receipt_normalize_to_the_same_shape():
    csv_lines = parse_bank_csv(CSV)
    receipts = parse_receipt_json(
        {
            "receipts": [
                {
                    "account": "622200001111",
                    "serial": "20261001-01",
                    "date": "2026-10-01",
                    "amount_cents": 120050,
                    "direction": "income",
                    "counterparty": "客户甲",
                }
            ]
        }
    )
    assert csv_lines[0].as_dict() | {"source": None} == receipts[0].as_dict() | {"source": None}
    assert csv_lines[0].amount_cents == 120_050
    assert receipts[0].amount_cents == 120_050


def test_csv_rejects_unknown_direction_instead_of_guessing():
    bad = CSV.replace("income", "转账")
    with pytest.raises(AdapterError) as caught:
        parse_bank_csv(bad)
    assert "第 2 行" in str(caught.value)
    assert "收支方向" in str(caught.value)


def test_csv_rejects_missing_column():
    with pytest.raises(AdapterError) as caught:
        parse_bank_csv("account,serial,date,amount\n1,2,3,4\n")
    assert "缺少列" in str(caught.value)


def test_csv_rejects_zero_amount():
    with pytest.raises(AdapterError):
        parse_bank_csv(CSV.replace("80.00", "0.00"))


def test_receipt_rejects_float_amounts():
    with pytest.raises(AdapterError) as caught:
        parse_receipt_json(
            {
                "receipts": [
                    {
                        "account": "a",
                        "serial": "s",
                        "date": "2026-10-01",
                        "amount_cents": 1200.5,
                        "direction": "income",
                        "counterparty": "客户甲",
                    }
                ]
            }
        )
    assert "正整数分" in str(caught.value)


def test_merge_drops_duplicates_across_sources():
    csv_lines = parse_bank_csv(CSV)
    replayed = parse_bank_csv(CSV, source="counter_csv_replay")
    merged = merge(csv_lines, replayed)
    assert len(merged) == 2
    assert [line.serial for line in merged] == ["20261001-01", "20261001-02"]
