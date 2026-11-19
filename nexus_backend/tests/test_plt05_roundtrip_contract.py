import pytest

from app.accounting.domain import AccountingError
from app.integrations.roundtrip import parse_return_file


HEADER = "批次标识,凭证标识,对方凭证号,结果,失败原因,对方过账日期,金额,科目\n"
EXPORTED = [
    {"voucher_code": "V-001", "amount_cents": 10000, "accounts": ["1002", "6001"]},
    {"voucher_code": "V-002", "amount_cents": 20000, "accounts": ["1002", "6001"]},
]


def csv(*rows):
    return (HEADER + "\n".join(rows) + "\n").encode()


def test_all_accepted_with_dates_is_received_and_posted():
    result = parse_return_file(
        csv(
            "B-1,V-001,EXT-1,accepted,,2026-11-20,100.00,1002",
            "B-1,V-002,EXT-2,accepted,,2026-11-20,200.00,6001",
        ),
        batch_code="B-1",
        exported=EXPORTED,
    )
    assert result["received"] is True
    assert result["posted"] is True
    assert result["status"] == "accepted"
    assert result["counts"]["accepted"] == 2


def test_missing_and_extra_are_discrepancies_not_success():
    result = parse_return_file(
        csv(
            "B-1,V-001,EXT-1,accepted,,2026-11-20,100.00,1002",
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


def test_amount_and_account_mismatch_are_not_accepted():
    result = parse_return_file(
        csv(
            "B-1,V-001,EXT-1,accepted,,2026-11-20,99.00,1002",
            "B-1,V-002,EXT-2,accepted,,2026-11-20,200.00,6602",
        ),
        batch_code="B-1",
        exported=EXPORTED,
    )
    rows = {row["voucher_code"]: row for row in result["rows"]}
    assert rows["V-001"]["status"] == "amount_mismatch"
    assert rows["V-002"]["status"] == "account_mismatch"
    assert result["received"] is False


def test_duplicate_voucher_or_wrong_batch_is_rejected():
    with pytest.raises(AccountingError) as duplicate:
        parse_return_file(
            csv(
                "B-1,V-001,EXT-1,accepted,,2026-11-20,100.00,1002",
                "B-1,V-001,EXT-2,accepted,,2026-11-20,100.00,1002",
            ),
            batch_code="B-1",
            exported=EXPORTED,
        )
    assert duplicate.value.status == 422
    with pytest.raises(AccountingError) as wrong:
        parse_return_file(
            csv("B-2,V-001,EXT-1,accepted,,2026-11-20,100.00,1002"), batch_code="B-1", exported=EXPORTED
        )
    assert wrong.value.status == 422


def test_received_without_full_posted_dates_is_not_posted():
    result = parse_return_file(
        csv(
            "B-1,V-001,EXT-1,accepted,,2026-11-20,100.00,1002",
            "B-1,V-002,EXT-2,accepted,,,200.00,1002",
        ),
        batch_code="B-1",
        exported=EXPORTED,
    )
    assert result["received"] is True
    assert result["posted"] is False
    assert result["status"] == "accepted"
