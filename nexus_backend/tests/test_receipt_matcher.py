"""回单匹配：唯一命中才自动挂接，其余进人工队列，绝不按金额猜。"""

from app.treasury.receipt_matcher import Receipt, StatementLine, match


def receipt(**changes):
    data = dict(
        account="6222 0001 1111",
        serial="B20261005001",
        traded_on="2026-10-05",
        amount_cents=120_000,
        counterparty="客户甲",
        receipt_id="r1",
    )
    data.update(changes)
    return Receipt(**data)


def line(**changes):
    data = dict(
        account="6222 0001 1111",
        serial="B20261005001",
        traded_on="2026-10-05",
        amount_cents=120_000,
        counterparty="客户甲",
        line_id="l1",
    )
    data.update(changes)
    return StatementLine(**data)


def test_unique_five_field_match_is_attached():
    result = match([receipt()], [line()])
    assert result.matched_pairs == [("r1", "l1")]
    assert result.manual == []
    assert result.unmatched_lines == []


def test_one_cent_difference_is_not_guessed():
    result = match([receipt(amount_cents=120_001)], [line()])
    assert result.matched_pairs == []
    assert result.manual[0]["reason"] == "no_candidate"
    assert result.unmatched_lines == ["l1"]


def test_same_day_same_amount_is_ambiguous_not_auto_matched():
    result = match([receipt()], [line(line_id="l1"), line(line_id="l2")])
    assert result.matched_pairs == []
    assert result.manual[0]["reason"] == "ambiguous"
    assert "猜测" in result.manual[0]["detail"]


def test_incomplete_receipt_goes_to_manual_queue():
    result = match([receipt(counterparty="")], [line()])
    assert result.manual[0]["reason"] == "receipt_incomplete"
    assert result.matched_pairs == []


def test_second_receipt_for_the_same_line_is_not_reused():
    result = match(
        [receipt(receipt_id="r1"), receipt(receipt_id="r2")],
        [line()],
    )
    assert result.matched_pairs == [("r1", "l1")]
    assert [item["reason"] for item in result.manual] == ["no_candidate"]


def test_cross_account_receipt_never_matches():
    result = match([receipt(account="6222 0002 2222")], [line()])
    assert result.matched_pairs == []
    assert result.manual[0]["reason"] == "no_candidate"


def test_incomplete_line_is_reported_once_without_pairs():
    result = match([receipt()], [line(traded_on="")])
    assert result.matched_pairs == []
    assert result.manual[0]["reason"] == "line_incomplete"


def test_leftover_lines_are_listed_for_the_desk():
    result = match([receipt()], [line(), line(line_id="l2", serial="B20261005002")])
    assert result.matched_pairs == [("r1", "l1")]
    assert result.unmatched_lines == ["l2"]
