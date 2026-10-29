"""N02: reviewed tax exports stay bound to formal, scoped source versions.

The HTTP harness uses the real router and SQLite domain services. Authentication
is supplied by existing synthetic fixture actors; this is not a login test.
Formal vouchers below use the actual independent-review and posting services.
"""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.accounting.domain import AccountingError
from app.accounting.catalog import CatalogService
from app.accounting.catalog_models import AccountDefinition
from app.accounting.models import (
    ApprovalRequest,
    DraftLine,
    DraftUpdate,
    ExportRequest,
    ImportRequest,
    SourceRow,
)
from app.opening.models import AccountCreate
from app.opening.service import OpeningService
from app.posting.models import ManualLine, ManualVoucher, OpeningBind
from app.posting.service import PostingService
from app.tax.router import build_tax_router
from review_policy_fixtures import post_reviewed_manual
from test_business import business  # noqa: F401 - fixture dependency
from test_posting_service import frozen_opening
from test_tax_workpaper import invoices, profile, row, tax  # noqa: F401


@pytest.fixture
def tax_http(tax):
    app = FastAPI()
    app.state.tax = tax.tax
    actors = {"owner": tax.manager, **tax.actors}

    def actor(x_test_actor: str = Header(default="accountant")):
        return actors[x_test_actor]

    @app.exception_handler(AccountingError)
    async def accounting_error(_request: Request, error: AccountingError):
        return JSONResponse(status_code=error.status, content={"detail": error.detail})

    app.include_router(build_tax_router(actor))
    with TestClient(app) as client:
        yield client


def paper(case, *, reviewed=True, profile_changes=None):
    case.tax.set_profile(case.manager, case.book["id"], profile(**(profile_changes or {})))
    invoices(case, [row("output", 100_000, 3_000)])
    result = case.tax.generate(case.actor_accountant, case.book["id"], "2026-10")
    if reviewed:
        result = case.tax.review(
            case.manager, case.book["id"], result["id"], "隔离核对：税额尚未计提", result["version"]
        )
    return result


def export(client, case, workpaper, *, actor="accountant", **changes):
    return client.get(
        f"/api/tax/workpapers/{workpaper['id']}/csv",
        headers={"X-Test-Actor": actor},
        params={
            "book_id": case.book["id"],
            "expected_version": workpaper["version"],
            "source_digest": workpaper["source_digest"],
            **changes,
        },
    )


@pytest.mark.parametrize(
    "omit", [("expected_version",), ("source_digest",), ("expected_version", "source_digest")]
)
def test_http_export_requires_explicit_reviewed_version_and_source(tax, tax_http, omit):
    current = paper(tax)
    params = {
        "book_id": tax.book["id"],
        "expected_version": current["version"],
        "source_digest": current["source_digest"],
    }
    for name in omit:
        params.pop(name)
    response = tax_http.get(f"/api/tax/workpapers/{current['id']}/csv", params=params)
    assert response.status_code == 422, "HTTP must not infer the reviewed version or source"


@pytest.mark.parametrize("change", [{"expected_version": 999}, {"source_digest": "f" * 64}])
def test_http_export_rejects_mismatched_version_or_digest(tax, tax_http, change):
    current = paper(tax)
    response = export(tax_http, tax, current, **change)
    assert response.status_code == 409


def test_http_current_reviewed_export_succeeds_but_draft_does_not(tax, tax_http):
    current = paper(tax, reviewed=False)
    assert export(tax_http, tax, current).status_code == 409
    reviewed = tax.tax.review(
        tax.manager, tax.book["id"], current["id"], "独立核对真实来源", current["version"]
    )
    response = export(tax_http, tax, reviewed)
    assert response.status_code == 200
    assert "1000.00" in response.text and "30.00" in response.text
    assert response.headers["X-Content-Sha256"]


@pytest.mark.parametrize("change", ["invoice", "profile"])
def test_http_reviewed_export_revalidates_current_sources(tax, tax_http, change):
    current = paper(tax)
    if change == "invoice":
        invoices(tax, [row("output", 5_000, 150, day="09")], tag="later")
    else:
        tax.tax.set_profile(
            tax.manager, tax.book["id"], profile(effective_from="2026-10", vat_rate_bp=100)
        )
    response = export(tax_http, tax, current)
    assert response.status_code == 409, "A prior review is not approval of changed source facts"
    historical = tax.tax.get(tax.actor_accountant, tax.book["id"], current["id"])
    assert historical["status"] == "reviewed"
    assert historical["source_digest"] == current["source_digest"]


@pytest.mark.parametrize("change", ["original_hash", "invoice_identity"])
def test_reviewed_export_rejects_changed_invoice_identity_even_when_totals_match(
    tax, tax_http, change
):
    current = paper(tax)
    # Local fault injection changes original metadata only; monetary facts match.
    with tax.db.transaction(write=True) as conn:
        if change == "original_hash":
            conn.execute(
                "UPDATE invoice_import_batches SET sha256=? WHERE id=?", ("b" * 64, "batch-1")
            )
        else:
            raw = conn.execute(
                "SELECT rows_json FROM invoice_import_batches WHERE id=?", ("batch-1",)
            ).fetchone()[0]
            rows = json.loads(raw)
            rows[0]["invoice_number"] = "000000000099"
            conn.execute(
                "UPDATE invoice_import_batches SET rows_json=? WHERE id=?",
                (json.dumps(rows), "batch-1"),
            )
    assert export(tax_http, tax, current).status_code == 409


def test_http_superseded_review_remains_readable_but_not_current_export(tax, tax_http):
    old = paper(tax)
    latest = tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    assert latest["version"] == old["version"] + 1
    assert tax.tax.get(tax.actor_accountant, tax.book["id"], old["id"])["status"] == "reviewed"
    assert export(tax_http, tax, old).status_code == 409


@pytest.fixture
def formal_tax(tax):
    opening = frozen_opening(tax)
    posting = PostingService(tax.db)
    posting.bind_opening(
        tax.manager,
        OpeningBind(
            book_id=tax.book["id"], package_version_id=opening["version_id"], reason="N02 正式账源"
        ),
    )
    return SimpleNamespace(case=tax, opening=opening, posting=posting)


def post_formal(fixture, key, *, debit_account=None, credit_account=None):
    case, opening = fixture.case, fixture.opening
    debit_account = debit_account or opening["expense"]
    credit_account = credit_account or opening["bank"]
    return post_reviewed_manual(
        fixture.posting,
        case.manager,
        ManualVoucher(
            command_key=key,
            book_id=case.book["id"],
            voucher_date="2026-10-15",
            description="N02 虚构正式凭证来源核验",
            reason="本地虚构资料经另一会计复核",
            lines=[
                ManualLine(
                    account_id=debit_account["id"], account_version=1, debit="30.00", credit="0.00"
                ),
                ManualLine(
                    account_id=credit_account["id"], account_version=1, debit="0.00", credit="30.00"
                ),
            ],
        ),
    )


@pytest.mark.parametrize("already_reviewed", [False, True])
def test_same_tax_total_new_formal_voucher_invalidates_frozen_source(
    formal_tax, tax_http, already_reviewed
):
    case = formal_tax.case
    current = paper(case, reviewed=already_reviewed)
    extra = post_formal(formal_tax, "n02-formal-after")
    assert extra["posting"] == "posted"
    assert current["ledger"] == "0.00", "The newly posted expense voucher does not change VAT total"
    if already_reviewed:
        response = export(tax_http, case, current)
        assert response.status_code == 409
        assert "来源" in response.json()["detail"]
    else:
        with pytest.raises(AccountingError) as failure:
            case.tax.review(
                case.manager, case.book["id"], current["id"], "来源必须重新生成", current["version"]
            )
        assert failure.value.status == 409
        assert "来源" in failure.value.detail


def test_negative_unmapped_formal_tax_is_a_controlled_block(formal_tax):
    case = formal_tax.case
    unknown = OpeningService(case.db).create_account(
        case.manager,
        AccountCreate(
            ledger_id=formal_tax.opening["ledger_id"],
            code="2221.99",
            name="尚未映射的虚构税种",
            category="liability",
            normal_balance="credit",
            effective_from="2026-01",
            effective_to="2026-12",
            reason="N02 负数未映射测试",
        ),
    )
    post_formal(formal_tax, "n02-unmapped-debit", debit_account=unknown)
    case.tax.set_profile(case.manager, case.book["id"], profile())
    invoices(case, [row("output", 100_000, 3_000)])
    with pytest.raises(AccountingError) as failure:
        case.tax.generate(case.actor_accountant, case.book["id"], "2026-10")
    assert failure.value.status == 409
    assert "2221.99" in failure.value.detail and "-30.00" in failure.value.detail


@pytest.mark.parametrize(
    "profile_changes",
    [{"filing_cycle": "quarterly"}, {"taxpayer_type": "general", "vat_rate_bp": 1300}],
)
def test_unsupported_cycle_or_unverified_deductions_do_not_generate_a_tax_result(
    tax, profile_changes
):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile(**profile_changes))
    invoices(tax, [row("output", 100_000, 13_000), row("input", 50_000, 6_500)])
    with pytest.raises(AccountingError) as failure:
        tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    assert failure.value.status == 409


def test_generate_cannot_use_a_different_period_under_the_same_monthly_book(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    invoices(tax, [{**row("output", 100_000, 3_000), "date": "2026-11-05"}])
    with pytest.raises(AccountingError) as failure:
        tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-11")
    assert failure.value.status == 409


@pytest.mark.parametrize(
    "actor,wrong_book", [("employee", False), ("accountant", True), ("owner", True)]
)
def test_http_export_checks_identity_and_book_before_exposing_data(
    tax, tax_http, actor, wrong_book
):
    current = paper(tax)
    scope = {"book_id": tax.other_book["id"]} if wrong_book else {}
    response = export(tax_http, tax, current, actor=actor, **scope)
    assert response.status_code in (403, 404)
    assert "1000.00" not in response.text and "30.00" not in response.text


@pytest.mark.parametrize("draft_status", ["draft", "approved", "exported"])
def test_drafts_are_not_formal_tax_ledger_even_after_review_or_export(tax, draft_status):
    before = paper(tax, reviewed=False)
    CatalogService(tax.db).create_account(
        tax.manager,
        tax.book["id"],
        AccountDefinition(
            code="2221.01", name="虚构增值税测试科目", category="liability", normal_balance="credit"
        ),
    )
    tax.ledger.import_sources(
        tax.manager,
        tax.book["id"],
        ImportRequest(
            source_name="N02 虚构税款支付",
            rows=[
                SourceRow(
                    external_id="n02-draft-only",
                    date="2026-10-15",
                    description="虚构税款支出",
                    counterparty="虚构收款方",
                    amount="30.00",
                    direction="expense",
                    evidence_ref="N02 合成原始凭据",
                )
            ],
        ),
    )
    tax.ledger.suggestions(tax.manager, tax.book["id"])
    draft = tax.ledger.get_book(tax.manager, tax.book["id"])["drafts"][0]
    updated = tax.ledger.update_draft(
        tax.manager,
        tax.book["id"],
        draft["id"],
        DraftUpdate(
            expected_version=draft["version"],
            description="只测试草稿不等于正式账",
            lines=[
                DraftLine(account_code="2221.01", debit="30.00", credit="0.00"),
                DraftLine(account_code="1002", debit="0.00", credit="30.00"),
            ],
        ),
    )
    if draft_status in {"approved", "exported"}:
        tax.ledger.approve(
            tax.actor_accountant,
            tax.book["id"],
            draft["id"],
            ApprovalRequest(
                expected_version=updated["version"],
                review_reason="不同会计复核虚构草稿",
            ),
        )
    if draft_status == "exported":
        tax.ledger.export(
            tax.manager,
            tax.book["id"],
            ExportRequest(
                draft_ids=[draft["id"]],
                idempotency_key="n02-exported-not-posted",
            ),
        )
    assert tax.ledger.get_draft(tax.manager, tax.book["id"], draft["id"])["status"] == draft_status
    after = tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    assert after["ledger"] == "0.00"
    assert after["source_digest"] == before["source_digest"]
