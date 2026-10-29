"""N02 formal-source contracts: real frozen opening, independent review and posting.

Only the invoice import fixture is synthetic seed data. No test inserts formal
vouchers, review receipts or account mappings with raw SQL.
"""

from types import SimpleNamespace

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ImportRequest, SourceRow
from app.accounting.service import fingerprint
from app.opening.models import AccountCreate
from app.opening.service import OpeningService
from app.posting.models import ManualLine, ManualVoucher, OpeningBind
from app.posting.service import PostingService
from app.tax.workpaper import TaxWorkpaperService
from review_policy_fixtures import post_reviewed_manual
from test_business import business  # noqa: F401 - fixture dependency
from test_posting_service import frozen_opening
from test_tax_workpaper import invoices, profile, row


@pytest.fixture
def formal_tax(business):
    case = business
    case.tax = TaxWorkpaperService(case.db)
    opening = frozen_opening(case)
    posting = PostingService(case.db)
    posting.bind_opening(
        case.manager,
        OpeningBind(
            book_id=case.book["id"],
            package_version_id=opening["version_id"],
            reason="N02 本地冻结期初及正式来源",
        ),
    )
    case.tax.set_profile(case.manager, case.book["id"], profile())
    invoices(
        case,
        [
            row("output", 100_000, 3_000)
            | {
                "number": "000000000001",
                "code": "N02",
                "line": 2,
                "key": "N02:000000000001",
                "seller": "第一客户企业",
                "seller_tax": "N02SELLER",
                "buyer": "N02 虚构购方",
                "buyer_tax": "N02BUYER",
                "kind": "增值税普通发票",
                "item": "本地测试服务",
                "status": "正常",
                "red": False,
                "void": False,
                "duplicate": False,
                "fingerprint": "b" * 64,
            }
        ],
    )
    return SimpleNamespace(case=case, opening=opening, posting=posting)


def _mapping(fixture):
    return [
        {
            "account_id": fixture.opening["payable"]["id"],
            "account_version": 1,
            "role": "vat_payable",
        },
        {
            "account_id": fixture.opening["bank"]["id"],
            "account_version": 1,
            "role": "excluded",
        },
    ]


def _post(fixture, key, *, opposite=False):
    case, opening = fixture.case, fixture.opening
    debit, credit = (
        (opening["payable"], opening["bank"]) if opposite else (opening["bank"], opening["payable"])
    )
    return post_reviewed_manual(
        fixture.posting,
        case.manager,
        ManualVoucher(
            command_key=key,
            book_id=case.book["id"],
            voucher_date="2026-10-15",
            description="N02 显式稳定科目税务来源",
            reason="虚构数据经独立会计逐行复核",
            lines=[
                ManualLine(account_id=debit["id"], account_version=1, debit="30.00", credit="0.00"),
                ManualLine(
                    account_id=credit["id"], account_version=1, debit="0.00", credit="30.00"
                ),
            ],
        ),
    )


def _generate(fixture, mappings):
    case = fixture.case
    return case.tax.generate(
        case.actors["accountant"],
        case.book["id"],
        case.book["period"],
        reason="N02 本地明确税务角色",
        vat_accounts=mappings,
    )


def _review(fixture, paper):
    case = fixture.case
    return case.tax.review(
        case.manager,
        case.book["id"],
        paper["id"],
        "核对完整正式凭证及原始发票",
        paper["version"],
    )


def _export(fixture, paper):
    case = fixture.case
    return case.tax.csv(
        case.actors["accountant"],
        case.book["id"],
        paper["id"],
        expected_version=paper["version"],
        source_digest=paper["source_digest"],
    )


def test_explicit_custom_account_role_and_full_formal_snapshot(formal_tax):
    posted = _post(formal_tax, "n02-voucher-snapshot-custom")
    declared = _mapping(formal_tax) + [
        {
            "account_id": formal_tax.opening["expense"]["id"],
            "account_version": 1,
            "role": "excluded",
        }
    ]
    paper = _generate(formal_tax, declared)
    case = formal_tax.case

    assert paper["ledger"] == "30.00", "Explicit VAT role must include custom code 2202"
    assert paper["variance"] == "0.00"
    snapshot = paper["source_snapshot"]
    assert snapshot["schema"] == "n02-tax-v2"
    assert snapshot["scope"] == {
        "firm_id": case.manager.firm_id,
        "company_id": case.company["id"],
        "ledger_id": formal_tax.opening["ledger_id"],
        "book_id": case.book["id"],
        "period": case.book["period"],
    }
    assert snapshot["profile"]["id"] == paper["profile_id"]
    actual_refs = {
        (entry["account_id"], entry["account_version"], entry["role"])
        for entry in snapshot["vat_accounts"]
    }
    assert actual_refs == {
        (entry["account_id"], entry["account_version"], entry["role"]) for entry in declared
    }
    assert snapshot["vat_accounts"] == sorted(
        snapshot["vat_accounts"], key=lambda entry: (entry["account_id"], entry["account_version"])
    )
    assert {"invoices", "ledger", "totals"} <= snapshot.keys()
    assert fingerprint(snapshot) == paper["source_digest"]

    with case.db.transaction(actor=case.manager) as conn:
        voucher = dict(
            conn.execute("SELECT * FROM vouchers WHERE id=?", (posted["voucher"]["id"],)).fetchone()
        )
        lines = [
            dict(line)
            for line in conn.execute(
                "SELECT * FROM voucher_lines WHERE voucher_id=? ORDER BY line_no", (voucher["id"],)
            )
        ]
    assert snapshot["ledger"]["vouchers"] == [voucher]
    assert snapshot["ledger"]["lines"] == lines, "Freeze excluded bank facts and VAT facts equally"
    assert case.tax.get(case.manager, case.book["id"], paper["id"])["source_snapshot"] == snapshot
    reordered = _generate(formal_tax, list(reversed(declared)))
    assert reordered["source_digest"] == paper["source_digest"]
    assert reordered["source_snapshot"] == snapshot


def test_explicit_exclusion_is_not_overridden_by_an_account_code(formal_tax):
    _post(formal_tax, "n02-voucher-snapshot-excluded")
    mappings = [{**entry, "role": "excluded"} for entry in _mapping(formal_tax)]
    paper = _generate(formal_tax, mappings)
    assert paper["ledger"] == "0.00"
    assert paper["variance"] == "30.00"


@pytest.mark.parametrize("missing", ["argument", "empty", "bank", "payable"])
def test_all_posted_account_versions_require_an_explicit_role(formal_tax, missing):
    _post(formal_tax, "n02-voucher-snapshot-missing")
    case = formal_tax.case
    with pytest.raises(AccountingError) as failure:
        if missing == "argument":
            case.tax.generate(case.actors["accountant"], case.book["id"], case.book["period"])
        else:
            mappings = (
                []
                if missing == "empty"
                else [
                    entry
                    for entry in _mapping(formal_tax)
                    if entry["account_id"] != formal_tax.opening[missing]["id"]
                ]
            )
            _generate(formal_tax, mappings)
    assert failure.value.status == 409
    assert "科目" in failure.value.detail and "用途" in failure.value.detail


def test_unmapped_account_cannot_disappear_when_its_movements_net_to_zero(formal_tax):
    _post(formal_tax, "n02-unmapped-net-zero-plus")
    _post(formal_tax, "n02-unmapped-net-zero-minus", opposite=True)
    only_bank = [entry for entry in _mapping(formal_tax) if entry["role"] == "excluded"]
    with pytest.raises(AccountingError) as failure:
        _generate(formal_tax, only_bank)
    assert failure.value.status == 409
    assert "2202" in failure.value.detail


def test_declared_account_from_another_ledger_is_rejected(formal_tax):
    _post(formal_tax, "n02-voucher-snapshot-foreign")
    case = formal_tax.case
    with case.db.transaction(actor=case.manager) as conn:
        foreign_ledger = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (case.other_book["id"],)
        ).fetchone()["ledger_id"]
    foreign = OpeningService(case.db).create_account(
        case.manager,
        AccountCreate(
            ledger_id=foreign_ledger,
            code="N02FOREIGN",
            name="另一企业明确不得混入的科目",
            category="liability",
            normal_balance="credit",
            effective_from="2026-01",
            reason="隔离测试外账套科目",
        ),
    )
    mappings = _mapping(formal_tax) + [
        {"account_id": foreign["id"], "account_version": 1, "role": "excluded"}
    ]
    with pytest.raises(AccountingError) as failure:
        _generate(formal_tax, mappings)
    assert failure.value.status in (403, 404, 409)


def test_wrong_declared_account_version_is_rejected(formal_tax):
    _post(formal_tax, "n02-voucher-snapshot-version")
    mappings = _mapping(formal_tax)
    mappings[0] = {**mappings[0], "account_version": 999}
    with pytest.raises(AccountingError) as failure:
        _generate(formal_tax, mappings)
    assert failure.value.status in (404, 409)


@pytest.mark.parametrize("reviewed", [False, True])
def test_new_offsetting_vouchers_invalidate_same_total_review_and_export(formal_tax, reviewed):
    _post(formal_tax, "n02-voucher-snapshot-before")
    paper = _generate(formal_tax, _mapping(formal_tax))
    if reviewed:
        paper = _review(formal_tax, paper)
    positive = _post(formal_tax, "n02-voucher-snapshot-plus")
    negative = _post(formal_tax, "n02-voucher-snapshot-minus", opposite=True)
    assert positive["voucher"]["id"] != negative["voucher"]["id"]
    with pytest.raises(AccountingError) as failure:
        _export(formal_tax, paper) if reviewed else _review(formal_tax, paper)
    assert failure.value.status == 409
    current = _generate(formal_tax, _mapping(formal_tax))
    assert (current["tax"], current["ledger"]) == (paper["tax"], paper["ledger"])
    assert current["source_digest"] != paper["source_digest"]
    historical = formal_tax.case.tax.get(
        formal_tax.case.manager, formal_tax.case.book["id"], paper["id"]
    )
    assert historical["source_snapshot"] == paper["source_snapshot"]
    assert historical["status"] == paper["status"]


def test_unposted_draft_is_not_a_formal_source_fact(formal_tax):
    _post(formal_tax, "n02-voucher-snapshot-only-posted")
    before = _generate(formal_tax, _mapping(formal_tax))
    case = formal_tax.case
    case.ledger.import_sources(
        case.manager,
        case.book["id"],
        ImportRequest(
            source_name="N02 尚未记账虚构来源",
            rows=[
                SourceRow(
                    external_id="n02-unposted-source",
                    date="2026-10-16",
                    description="尚未记账的办公费",
                    counterparty="N02 虚构供方",
                    amount="0.30",
                    direction="expense",
                    evidence_ref="N02 合成原件",
                )
            ],
        ),
    )
    case.ledger.suggestions(case.manager, case.book["id"])
    assert case.ledger.get_book(case.manager, case.book["id"])["drafts"]
    after = _generate(formal_tax, _mapping(formal_tax))
    assert after["ledger"] == before["ledger"] == "30.00"
    assert after["source_digest"] == before["source_digest"]
    assert after["source_snapshot"]["ledger"] == before["source_snapshot"]["ledger"]
