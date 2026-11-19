from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.assistant import analysis
from test_business import business  # noqa: F401


def _source(conn, book_id, source_id, name, amount, kind="business", evidence="原件", date="2026-10-18"):
    conn.execute(
        "INSERT INTO sources(id,book_id,external_id,source_name,date,description,"
        "counterparty,amount_cents,direction,evidence_ref,fingerprint,source_kind) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            source_id,
            book_id,
            f"ext-{source_id}",
            name,
            date,
            name,
            "虚构平台",
            amount,
            "income",
            evidence,
            uuid4().hex,
            kind,
        ),
    )


@pytest.fixture()
def settlement_case(business):  # noqa: F811
    case = business
    with case.db.transaction() as conn:
        _source(conn, case.book["id"], "settle-001", "平台结算单", 10_000_000)
        _source(conn, case.book["id"], "bank-001", "银行到账", 9_965_000, "bank", "银行回单")
    return case


def _run(case, **extra):
    return analysis.analyze(
        case.db,
        case.manager,
        case.book["id"],
        "2026-10",
        "settlement_difference",
        settlement_source_id="settle-001",
        bank_source_id="bank-001",
        **extra,
    )


def test_unexplained_difference_states_amount_missing_evidence_and_owner(settlement_case):
    result = _run(settlement_case)
    assert result["status"] == "partial"
    assert result["facts"]["difference_cents"] == 35000
    assert result["facts"]["difference"] == "350.00"
    assert result["facts"]["reason"] == "unresolved"
    assert "fee_evidence" in result["missing"]
    assert result["handoff"]["owner_role"] == "accountant"
    assert "手续费" in result["handoff"]["reason"]
    assert "source=bank-001" in result["handoff"]["action_target"]
    assert result["model_calls"] == 0


def test_verified_fee_evidence_explains_only_matching_difference(settlement_case):
    with settlement_case.db.transaction() as conn:
        _source(conn, settlement_case.book["id"], "fee-001", "平台手续费", 35_000)
    result = _run(settlement_case, fee_source_id="fee-001")
    assert result["status"] == "complete"
    assert result["facts"]["reason"] == "fee_evidence"
    assert result["facts"]["fee_total"] == "350.00"
    assert result["handoff"] is None
    assert result["facts"]["fee_source"]["evidence_href"].endswith("source=fee-001")


def test_fee_evidence_mismatch_is_not_accepted(settlement_case):
    with settlement_case.db.transaction() as conn:
        _source(conn, settlement_case.book["id"], "fee-002", "不匹配手续费", 34_000)
    result = _run(settlement_case, fee_source_id="fee-002")
    assert result["status"] == "partial"
    assert result["facts"]["reason"] == "fee_evidence_mismatch"
    assert "fee_evidence_mismatch" in result["missing"]


def test_other_company_source_cannot_explain_this_case(settlement_case):
    with settlement_case.db.transaction() as conn:
        _source(conn, settlement_case.other_book["id"], "other-fee", "他企业手续费", 35_000)
    with pytest.raises(AccountingError) as exc:
        _run(settlement_case, fee_source_id="other-fee")
    assert exc.value.status == 404
    assert "其他企业" not in str(exc.value) and "他企业" not in str(exc.value)


def test_same_amount_fee_from_another_period_cannot_explain_this_difference(settlement_case):
    with settlement_case.db.transaction() as conn:
        _source(conn, settlement_case.book["id"], "fee-old", "上期平台手续费", 35_000, date="2026-09-30")
    result = _run(settlement_case, fee_source_id="fee-old")
    assert result["status"] == "partial"
    assert result["facts"]["reason"] == "cross_period_evidence"
    assert "fee_evidence_coverage" in result["missing"]
    assert "覆盖" in result["handoff"]["reason"]


def test_revoked_book_access_fails_closed(settlement_case, monkeypatch):
    class Revoked:
        def book(self, conn, actor, book_id, mode):
            raise AccountingError("当前岗位没有这项权限", 403)

    monkeypatch.setattr(analysis, "AccessControl", Revoked)
    with pytest.raises(AccountingError) as exc:
        _run(settlement_case)
    assert exc.value.status == 403
    assert "结算单" not in str(exc.value)


def test_source_change_invalidates_expected_version(settlement_case):
    first = _run(settlement_case)
    with settlement_case.db.transaction() as conn:
        conn.execute(
            "UPDATE sources SET evidence_ref='换版后的手续费' WHERE id='bank-001'"
        )
    with pytest.raises(AccountingError) as exc:
        _run(settlement_case, expected_source_version=first["source_version"])
    assert exc.value.status == 409
