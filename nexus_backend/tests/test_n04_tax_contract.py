"""Real read-only HTTP contracts for the N04 tax preparation front end."""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.accounting.domain import AccountingError
from app.accounting.models import BookMemberGrant
from app.opening.models import AccountRevision
from app.opening.service import OpeningService
from app.tax.router import build_tax_router
from app.tax.workpaper import TaxWorkpaperService
from test_business import business  # noqa: F401
from test_n02_voucher_snapshot import formal_tax, _post, _mapping  # noqa: F401
from test_tax_workpaper import invoices, profile, row


@pytest.fixture
def context_http(business):
    app = FastAPI()
    app.state.tax = TaxWorkpaperService(business.db)
    actors = {"owner": business.manager, **business.actors}

    def actor(x_test_actor: str = Header(default="accountant")):
        return actors[x_test_actor]

    @app.exception_handler(AccountingError)
    async def accounting_error(_request: Request, failure: AccountingError):
        return JSONResponse(status_code=failure.status, content={"detail": failure.detail})

    app.include_router(build_tax_router(actor))
    with TestClient(app) as client:
        yield client


def context(client, book_id, actor="accountant", period="2026-10"):
    return client.get(
        "/api/tax/context",
        headers={"X-Test-Actor": actor},
        params={"book_id": book_id, "period": period},
    )


def test_context_is_scoped_read_only_and_does_not_require_account_management(
    business, context_http
):
    case = business
    with case.db.transaction() as conn:
        before = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    response = context(context_http, case.book["id"])
    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data) == {"scope", "permissions", "profile", "accounts", "blocking_reasons"}
    assert data["scope"] == {
        "firm_id": case.manager.firm_id,
        "company_id": case.company["id"],
        "book_id": case.book["id"],
        "period": "2026-10",
        "book_name": case.book["name"],
    }
    assert data["permissions"] == {"edit": True, "review": True, "export": True, "manage": False}
    assert data["profile"] is None and data["accounts"] == []
    assert any("税务档案" in reason for reason in data["blocking_reasons"])
    with case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == before


@pytest.mark.parametrize("target", ["other_company", "employee", "wrong_period"])
def test_context_refuses_unauthorized_or_wrong_scope(business, context_http, target):
    response = context(
        context_http,
        business.other_book["id"] if target == "other_company" else business.book["id"],
        actor="employee" if target == "employee" else "accountant",
        period="2026-11" if target == "wrong_period" else "2026-10",
    )
    assert response.status_code == (409 if target == "wrong_period" else 404)
    assert "accounts" not in response.json()


def test_context_permissions_come_from_current_book_grants(business, context_http):
    case = business
    case.directory.grant_book_member(
        case.manager, case.book["id"], "finance", BookMemberGrant(role="viewer")
    )
    result = context(context_http, case.book["id"], "finance")
    assert result.status_code == 200, result.text
    assert result.json()["permissions"] == {
        "edit": False,
        "review": False,
        "export": False,
        "manage": False,
    }
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "DELETE FROM book_access WHERE book_id=? AND user_id='finance'", (case.book["id"],)
        )
    assert context(context_http, case.book["id"], "finance").status_code == 404


def test_context_keeps_all_posted_account_versions_even_when_net_movement_is_zero(
    formal_tax, context_http
):
    _post(formal_tax, "n04-normal")
    _post(formal_tax, "n04-opposite", opposite=True)
    result = context(context_http, formal_tax.case.book["id"])
    assert result.status_code == 200, result.text
    data = result.json()
    assert {(a["account_id"], a["account_version"]) for a in data["accounts"]} == {
        (formal_tax.opening["bank"]["id"], 1),
        (formal_tax.opening["payable"]["id"], 1),
    }
    assert all(
        set(a) == {"account_id", "account_version", "code", "name"} for a in data["accounts"]
    )
    assert data["blocking_reasons"] == []


def test_context_uses_frozen_posted_version_after_current_account_is_revised(
    formal_tax, context_http
):
    _post(formal_tax, "n04-post-before-account-revision")
    account = formal_tax.opening["bank"]
    revised = OpeningService(formal_tax.case.db).revise_account(
        formal_tax.case.manager,
        account["id"],
        AccountRevision(
            ledger_id=formal_tax.opening["ledger_id"],
            expected_version=1,
            code=account["code"],
            name="下期重新命名的银行账户",
            category="asset",
            normal_balance="debit",
            effective_from="2026-11",
            reason="N04 虚构下期科目名称修订",
        ),
    )
    assert revised["version"] == 2
    response = context(context_http, formal_tax.case.book["id"])
    assert response.status_code == 200, response.text
    actual = next(a for a in response.json()["accounts"] if a["account_id"] == account["id"])
    assert actual == {
        "account_id": account["id"],
        "account_version": 1,
        "code": account["code"],
        "name": account["name"],
    }


@pytest.mark.parametrize(
    "profile_changes,phrase",
    [({"taxpayer_type": "general"}, "一般纳税人"), ({"filing_cycle": "quarterly"}, "季度")],
)
def test_context_explains_existing_unsupported_boundaries(
    business, context_http, profile_changes, phrase
):
    TaxWorkpaperService(business.db).set_profile(
        business.manager, business.book["id"], profile(**profile_changes)
    )
    result = context(context_http, business.book["id"])
    assert result.status_code == 200, result.text
    assert any(phrase in reason for reason in result.json()["blocking_reasons"])


def test_context_reports_no_source_rows_without_guessing_tax(business, context_http):
    TaxWorkpaperService(business.db).set_profile(business.manager, business.book["id"], profile())
    result = context(context_http, business.book["id"])
    assert result.status_code == 200, result.text
    assert any("发票" in reason for reason in result.json()["blocking_reasons"])
    assert "tax" not in result.json()


def test_context_reports_rejected_invoice_sources(business, context_http):
    TaxWorkpaperService(business.db).set_profile(business.manager, business.book["id"], profile())
    invoices(business, [row("output", 10000, 300) | {"red": True}])
    result = context(context_http, business.book["id"])
    assert result.status_code == 200, result.text
    assert any("红字" in reason for reason in result.json()["blocking_reasons"])


def capture_contract(fixture, client):
    """Capture actual fictional HTTP values; also used to validate the shared example."""
    case = fixture.case
    _post(fixture, "n04-shared-contract-voucher")
    create_request = {
        "book_id": case.book["id"],
        "period": case.book["period"],
        "reason": "N04 本地虚构资料核对",
        "vat_accounts": _mapping(fixture),
    }
    prepared = context(client, case.book["id"])
    assert prepared.status_code == 200, prepared.text
    profile_response = client.get(
        "/api/tax/profile", params={"book_id": case.book["id"], "period": case.book["period"]}
    )
    created = client.post("/api/tax/workpapers", json=create_request)
    assert created.status_code == 201, created.text
    draft = created.json()
    review_request = {
        "book_id": case.book["id"],
        "expected_version": draft["version"],
        "reason": "独立核对原始发票及正式凭证",
    }
    reviewed = client.post(
        f"/api/tax/workpapers/{draft['id']}/review",
        headers={"X-Test-Actor": "owner"},
        json=review_request,
    )
    assert reviewed.status_code == 200, reviewed.text
    listed = client.get(
        "/api/tax/workpapers", params={"book_id": case.book["id"], "period": case.book["period"]}
    )
    query = {
        "book_id": case.book["id"],
        "expected_version": draft["version"],
        "source_digest": draft["source_digest"],
    }
    exported = client.get(f"/api/tax/workpapers/{draft['id']}/csv", params=query)
    assert exported.status_code == 200, exported.text
    return {
        "context": prepared.json(),
        "profile_response": profile_response.json(),
        "create_request": create_request,
        "draft": draft,
        "reviewed": reviewed.json(),
        "list_response": listed.json(),
        "review_request": review_request,
        "export_query": query,
    }


def shape(value):
    if isinstance(value, dict):
        return {key: shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [shape(item) for item in value]
    return type(value).__name__


def test_shared_example_matches_actual_tax_http_shapes_and_fixed_fact_values(
    formal_tax, context_http
):
    from app.accounting.service import fingerprint

    live = capture_contract(formal_tax, context_http)
    sample = json.loads(
        (Path(__file__).parent / "fixtures/n04_tax_contract.json").read_text(encoding="utf-8")
    )
    assert shape(sample) == shape(live)
    for field in [
        "output_net",
        "output_tax",
        "input_net",
        "input_tax",
        "tax",
        "surtax",
        "credit_carry",
        "ledger",
        "variance",
        "status",
        "filing",
    ]:
        assert sample["reviewed"][field] == live["reviewed"][field]
    assert sample["reviewed"]["source_digest"] == fingerprint(sample["reviewed"]["source_snapshot"])
    assert sample["reviewed"]["created_by"] != sample["reviewed"]["reviewed_by"]
    assert sample["export_query"]["source_digest"] == sample["reviewed"]["source_digest"]
    assert {(a["account_id"], a["account_version"]) for a in sample["context"]["accounts"]} == {
        (a["account_id"], a["account_version"]) for a in sample["create_request"]["vat_accounts"]
    }
