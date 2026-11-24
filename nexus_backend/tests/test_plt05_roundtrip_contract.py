"""PLT05-B 返回文件核对契约。

2026-11-23 修复（包 01）：
- 科目比对从「有交集即通过」改为**严格集合相等**，冻结凭证多科目时少给/多给/夹带错科目都算差异；
- 逐笔「已核实过账」必须同时具备对方凭证号、对方过账日期、金额与完整科目集合；
- 对方凭证号与凭证标识、失败原因一样接受公式前缀注入检查。
"""

import pytest

from app.accounting.domain import AccountingError
from app.integrations.roundtrip import parse_return_file


HEADER = "批次标识,凭证标识,对方凭证号,结果,失败原因,对方过账日期,金额,科目\n"
EXPORTED = [
    {"voucher_code": "V-001", "amount_cents": 10000, "accounts": ["1002", "6001"]},
    {"voucher_code": "V-002", "amount_cents": 20000, "accounts": ["1002", "6602.02"]},
]


def csv(*rows):
    return (HEADER + "\n".join(rows) + "\n").encode()


def complete_rows(batch="B-1"):
    return (
        f"{batch},V-001,EXT-1,accepted,,2026-11-20,100.00,1002|6001",
        f"{batch},V-002,EXT-2,accepted,,2026-11-20,200.00,1002|6602.02",
    )


def test_all_accepted_with_full_fields_is_received_and_posted():
    result = parse_return_file(csv(*complete_rows()), batch_code="B-1", exported=EXPORTED)
    assert result["received"] is True
    assert result["posted"] is True
    assert result["status"] == "accepted"
    assert result["counts"]["accepted"] == 2


def test_missing_and_extra_are_discrepancies_not_success():
    result = parse_return_file(
        csv(
            "B-1,V-001,EXT-1,accepted,,2026-11-20,100.00,1002|6001",
            "B-1,V-X,EXT-X,accepted,,2026-11-20,300.00,6001",
        ),
        batch_code="B-1",
        exported=EXPORTED,
    )
    assert result["received"] is False
    assert result["status"] == "partial"
    assert {row["voucher_code"]: row["status"] for row in result["rows"]} == {
        "V-001": "accepted",
        "V-002": "missing",
        "V-X": "extra",
    }
    assert result["posted"] is False


def test_amount_and_account_mismatch_are_not_accepted():
    result = parse_return_file(
        csv(
            "B-1,V-001,EXT-1,accepted,,2026-11-20,99.00,1002|6001",
            "B-1,V-002,EXT-2,accepted,,2026-11-20,200.00,6602",
        ),
        batch_code="B-1",
        exported=EXPORTED,
    )
    rows = {row["voucher_code"]: row for row in result["rows"]}
    assert rows["V-001"]["status"] == "amount_mismatch"
    assert rows["V-002"]["status"] == "account_mismatch"
    assert result["received"] is False
    assert result["posted"] is False


def test_partial_accounts_are_mismatch_even_if_one_account_matches():
    """少给一个科目必须判差异，不能被「有一个科目对上」放过。"""
    result = parse_return_file(
        csv(
            "B-1,V-001,EXT-1,accepted,,2026-11-20,100.00,1002",
            "B-1,V-002,EXT-2,accepted,,2026-11-20,200.00,1002|6602.02",
        ),
        batch_code="B-1",
        exported=EXPORTED,
    )
    rows = {row["voucher_code"]: row for row in result["rows"]}
    assert rows["V-001"]["status"] == "account_mismatch"
    assert "科目" in rows["V-001"]["reason"]
    assert result["received"] is False
    assert result["posted"] is False


def test_extra_or_dirty_account_is_mismatch():
    result = parse_return_file(
        csv(
            "B-1,V-001,EXT-1,accepted,,2026-11-20,100.00,1002|6001|6602",
            "B-1,V-002,EXT-2,accepted,,2026-11-20,200.00,1002|6602.02|9999",
        ),
        batch_code="B-1",
        exported=EXPORTED,
    )
    rows = {row["voucher_code"]: row for row in result["rows"]}
    assert {row["status"] for row in rows.values()} == {"account_mismatch"}
    assert result["posted"] is False


def test_duplicate_voucher_or_wrong_batch_is_rejected():
    with pytest.raises(AccountingError) as duplicate:
        parse_return_file(
            csv(
                "B-1,V-001,EXT-1,accepted,,2026-11-20,100.00,1002|6001",
                "B-1,V-001,EXT-2,accepted,,2026-11-20,100.00,1002|6001",
            ),
            batch_code="B-1",
            exported=EXPORTED,
        )
    assert duplicate.value.status == 422
    with pytest.raises(AccountingError) as wrong:
        parse_return_file(
            csv("B-2,V-001,EXT-1,accepted,,2026-11-20,100.00,1002|6001"),
            batch_code="B-1",
            exported=EXPORTED,
        )
    assert wrong.value.status == 422


def test_received_without_full_posted_dates_is_not_posted():
    result = parse_return_file(
        csv(
            "B-1,V-001,EXT-1,accepted,,2026-11-20,100.00,1002|6001",
            "B-1,V-002,EXT-2,accepted,,,200.00,1002|6602.02",
        ),
        batch_code="B-1",
        exported=EXPORTED,
    )
    assert result["received"] is True
    assert result["posted"] is False
    assert result["status"] == "accepted"


def test_empty_external_code_caps_at_received_not_posted():
    result = parse_return_file(
        csv(
            "B-1,V-001,,accepted,,2026-11-20,100.00,1002|6001",
            "B-1,V-002,EXT-2,accepted,,2026-11-20,200.00,1002|6602.02",
        ),
        batch_code="B-1",
        exported=EXPORTED,
    )
    rows = {row["voucher_code"]: row for row in result["rows"]}
    assert result["received"] is True
    assert result["posted"] is False
    assert rows["V-001"]["status"] == "accepted"
    assert "凭证号" in rows["V-001"]["reason"]


def test_formula_prefix_in_external_code_is_rejected():
    for cell in ("=1+1", "+1", "-1", "@SUM(1)"):
        with pytest.raises(AccountingError) as error:
            parse_return_file(
                csv(
                    f"B-1,V-001,{cell},accepted,,2026-11-20,100.00,1002|6001",
                    "B-1,V-002,EXT-2,accepted,,2026-11-20,200.00,1002|6602.02",
                ),
                batch_code="B-1",
                exported=EXPORTED,
            )
        assert error.value.status == 422
        assert "公式前缀" in str(error.value)
