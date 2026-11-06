"""IMP08: deterministic, read-only accounting analyses."""

from types import SimpleNamespace

import pytest

from app.accounting.domain import AccountingError
from app.assistant import analysis
from app.posting.models import ManualLine, ManualVoucher
from review_policy_fixtures import independent_reviewer, post_reviewed_manual
from test_b04_pilot import setup_b03
from test_business import business


def _voucher(case, opening, posting, key, lines):
    return post_reviewed_manual(
        posting,
        case.manager,
        ManualVoucher(
            command_key=key,
            book_id=case.book["id"],
            voucher_date="2026-10-20",
            description="IMP08 虚构凭证",
            lines=[
                ManualLine(
                    account_id=opening[code]["id"],
                    account_version=1,
                    debit=debit,
                    credit=credit,
                )
                for code, debit, credit in lines
            ],
        ),
    )


@pytest.fixture()
def posted(business):
    case = business
    opening, posting = setup_b03(case)
    first = _voucher(
        case,
        opening,
        posting,
        "imp08-v-001",
        [("expense", "100.00", "0.00"), ("bank", "0.00", "100.00")],
    )
    second = _voucher(
        case,
        opening,
        posting,
        "imp08-v-002",
        [("expense", "50.00", "0.00"), ("bank", "0.00", "50.00")],
    )
    with case.db.transaction() as conn:
        conn.execute(
            "INSERT INTO voucher_sources(id,voucher_id,source_kind,source_id,source_version,amount_cents,evidence_ref) "
            "VALUES(?,?,?,?,?,?,?)",
            ("imp08-src-1", first["voucher"]["id"], "source", "invoice-1", 1, 6000, "/receipt/1"),
        )
        conn.execute(
            "INSERT INTO voucher_sources(id,voucher_id,source_kind,source_id,source_version,amount_cents,evidence_ref) "
            "VALUES(?,?,?,?,?,?,?)",
            ("imp08-src-2", first["voucher"]["id"], "source", "invoice-2", 1, 3000, "/receipt/2"),
        )
    return SimpleNamespace(
        case=case, opening=opening, posting=posting, first=first, second=second
    )


def test_expense_bridge_is_partial_without_previous_period(posted):
    case = posted.case
    result = analysis.analyze(
        case.db,
        case.manager,
        case.book["id"],
        "2026-10",
        "expense_bridge",
        account_code="6602.02",
    )
    assert result["kind"] == "expense_bridge"
    assert result["company_id"] == case.company["id"]
    assert result["status"] == "partial"
    assert "previous_period" in result["missing"]
    assert result["facts"]["current_total"] == "150.00"
    assert result["coverage"]["rows_read"] == 2
    assert result["model_calls"] == 0


def test_invoice_difference_uses_source_total_and_progressive_disclosure(posted):
    case = posted.case
    result = analysis.analyze(
        case.db,
        case.manager,
        case.book["id"],
        "2026-10",
        "invoice_voucher_diff",
        target_voucher_id=posted.first["voucher"]["id"],
        limit=1,
    )
    assert result["status"] == "partial"
    assert result["coverage"]["has_more"] is True
    assert result["facts"]["voucher_total"] == "100.00"
    assert result["facts"]["source_total"] == "60.00"

    full = analysis.analyze(
        case.db,
        case.manager,
        case.book["id"],
        "2026-10",
        "invoice_voucher_diff",
        target_voucher_id=posted.first["voucher"]["id"],
        limit=50,
    )
    assert full["status"] == "complete"
    assert full["facts"]["difference"] == "10.00"


def test_cross_company_and_wrong_book_are_rejected(posted):
    case = posted.case
    with pytest.raises(AccountingError) as exc:
        analysis.analyze(
            case.db,
            case.manager,
            case.other_book["id"],
            "2026-10",
            "invoice_voucher_diff",
            target_voucher_id=posted.first["voucher"]["id"],
        )
    assert exc.value.status == 404


def test_claim_rejects_wrong_amount_cross_company_causality_and_missing_fact(posted):
    case = posted.case
    result = analysis.analyze(
        case.db,
        case.manager,
        case.book["id"],
        "2026-10",
        "invoice_voucher_diff",
        target_voucher_id=posted.first["voucher"]["id"],
    )
    fact_id = result["facts"]["fact_ids"][0]
    analysis.validate_claim(
        result,
        {"company_id": case.company["id"], "amount_cents": 1000, "fact_ids": [fact_id], "text": "差额为 10.00 元"},
    )
    for claim in (
        {"company_id": case.company["id"], "amount_cents": 9900, "fact_ids": [fact_id]},
        {"company_id": "other-company", "amount_cents": 1000, "fact_ids": [fact_id]},
        {"company_id": case.company["id"], "amount_cents": 1000, "fact_ids": [fact_id], "causal": "员工导致"},
        {"company_id": case.company["id"], "amount_cents": 1000, "fact_ids": ["not-a-fact"]},
    ):
        with pytest.raises(AccountingError):
            analysis.validate_claim(result, claim)


def test_invalid_kind_and_account_are_422(posted):
    case = posted.case
    for kind, kwargs in (
        ("drop_table", {}),
        ("expense_bridge", {"account_code": "6602; DROP TABLE vouchers"}),
    ):
        with pytest.raises(AccountingError) as exc:
            analysis.analyze(case.db, case.manager, case.book["id"], "2026-10", kind, **kwargs)
        assert exc.value.status == 422


def test_http_capability_is_dark_by_default_and_flag_enables_read_only_analysis(
    tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(tmp_path / "imp08-http.db", seed=True)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 45901)) as client:
        with app.state.service.db.transaction() as conn:
            book = conn.execute("SELECT id,period,company_id FROM books LIMIT 1").fetchone()
        token = client.get("/api/session").json()["token"]
        headers = {"Authorization": "Bearer " + token}
        capability = client.get(
            f"/api/assistant/analyses/capability?book_id={book['id']}", headers=headers
        )
        assert capability.status_code == 200
        assert capability.json()["enabled"] is False
        dark = client.post(
            "/api/assistant/analyses",
            headers=headers,
            json={"book_id": book["id"], "period": book["period"], "kind": "expense_bridge"},
        )
        assert dark.status_code == 403

        monkeypatch.setenv("ACCOUNTING_ASSISTANT_INVESTIGATION", "1")
        enabled = client.post(
            "/api/assistant/analyses",
            headers=headers,
            json={
                "book_id": book["id"],
                "period": book["period"],
                "kind": "expense_bridge",
                "account_code": "6602.02",
            },
        )
        assert enabled.status_code == 200, enabled.text
        body = enabled.json()
        assert body["company_id"] == book["company_id"]
        assert body["model_calls"] == 0
        assert body["real_model_evaluated"] is False


def test_analysis_fails_closed_when_book_access_is_revoked(posted, monkeypatch):
    case = posted.case
    class Revoked:
        def book(self, conn, actor, book_id, mode):
            raise AccountingError("当前岗位没有这项权限", 403)
    monkeypatch.setattr(analysis, "AccessControl", Revoked)
    with pytest.raises(AccountingError) as caught:
        analysis.analyze(
            case.db, case.manager, case.book["id"], "2026-10", "expense_bridge", account_code="6602.02"
        )
    assert caught.value.status == 403


def test_period_mismatch_fails_closed(posted):
    case = posted.case
    with pytest.raises(AccountingError) as caught:
        analysis.analyze(
            case.db, case.manager, case.book["id"], "2026-11", "expense_bridge", account_code="6602.02"
        )
    assert caught.value.status == 422
