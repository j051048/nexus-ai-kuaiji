from app.accounting.domain import AccountingError
from app.tax.workpaper import TaxWorkpaperService

from test_business import business  # noqa: F401

from app.tax.policy_items import TaxPolicyItemService, TaxPolicyItemWrite


def test_two_vat_rates_are_itemized_and_missing_policy_blocks(business):
    case = business
    service = TaxWorkpaperService(case.db)
    profile = service.set_profile(
        case.manager,
        case.book["id"],
        {
            "taxpayer_type": "small_scale",
            "filing_cycle": "monthly",
            "vat_rate_bp": 300,
            "surtax_rate_bp": 0,
            "effective_from": "2026-10",
            "reason": "合成测试政策，仅用于 OPT07-E 验证",
        },
    )
    items = TaxPolicyItemService(case.db)
    created = items.add(
        case.manager,
        case.book["id"],
        [
            TaxPolicyItemWrite(
                kind="vat_output",
                code="VAT-6",
                name="合成 6% 销项",
                basis_kind="output_standard",
                rate_bp=600,
                effective_from="2026-10",
                authority="合成政策条目，仅测试",
                approved_by=case.manager.user_id,
            ),
            TaxPolicyItemWrite(
                kind="vat_output",
                code="VAT-13",
                name="合成 13% 销项",
                basis_kind="output_reduced",
                rate_bp=1300,
                effective_from="2026-10",
                authority="合成政策条目，仅测试",
                approved_by=case.manager.user_id,
            ),
            TaxPolicyItemWrite(
                kind="culture_fee",
                code="CULTURE-3",
                name="合成文化事业建设费",
                basis_kind="culture_base",
                rate_bp=300,
                effective_from="2026-10",
                authority="合成政策条目，仅测试",
                approved_by=case.manager.user_id,
            ),
            TaxPolicyItemWrite(
                kind="agriculture_deduction",
                code="AGRI-9",
                name="合成农产品扣除",
                basis_kind="agriculture_base",
                rate_bp=900,
                effective_from="2026-10",
                authority="合成政策条目，仅测试",
                approved_by=case.manager.user_id,
            ),
        ],
    )
    assert created["profile_id"] == profile["id"]
    assert len(created["items"]) == 4
    result = items.calculate(
        case.manager,
        case.book["id"],
        "2026-10",
        {
            "output_standard": 10_000,
            "output_reduced": 5_000,
            "culture_base": 20_000,
            "agriculture_base": 4_000,
        },
    )
    by_code = {line["code"]: line["amount_cents"] for line in result["lines"]}
    assert by_code == {
        "VAT-6": 600,
        "VAT-13": 650,
        "CULTURE-3": 600,
        "AGRI-9": 360,
    }
    assert result["total_cents"] == 2_210
    try:
        items.calculate(
            case.manager,
            case.book["id"],
            "2026-10",
            {"output_standard": 1_000, "missing_basis": 1},
        )
    except AccountingError as exc:
        assert exc.status == 409
        assert "missing_basis" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing tax policy must block")


def test_workpaper_uses_policy_items_and_freezes_their_fingerprints(business):
    from test_tax_workpaper import invoices, row

    case = business
    workpapers = TaxWorkpaperService(case.db)
    workpapers.set_profile(
        case.manager,
        case.book["id"],
        {
            "taxpayer_type": "small_scale",
            "filing_cycle": "monthly",
            "vat_rate_bp": 300,
            "surtax_rate_bp": 0,
            "effective_from": "2026-10",
            "reason": "合成测试政策，仅用于 OPT07-E 底稿集成",
        },
    )
    TaxPolicyItemService(case.db).add(
        case.manager,
        case.book["id"],
        [
            TaxPolicyItemWrite(
                kind="vat_output",
                code="VAT-6",
                name="合成 6% 销项",
                basis_kind="output_net",
                rate_bp=600,
                effective_from="2026-10",
                authority="合成政策条目，仅测试",
                approved_by=case.manager.user_id,
            )
        ],
    )
    invoices(case, [row("output", 10_000, 600)])
    paper = workpapers.generate(
        case.manager,
        case.book["id"],
        reason="OPT07-E 底稿集成验证",
        vat_accounts=[],
    )
    assert paper["tax"] == "6.00"
    policy = paper["source_snapshot"]["tax_policy"]
    assert policy["vat_cents"] == 600
    assert policy["lines"][0]["code"] == "VAT-6"
    assert len(policy["lines"][0]["item_fingerprint"]) == 64


def test_reviewed_workpaper_is_not_rewritten_when_policy_items_change(business):
    from test_tax_workpaper import invoices, row

    case = business
    workpapers = TaxWorkpaperService(case.db)
    workpapers.set_profile(
        case.manager,
        case.book["id"],
        {
            "taxpayer_type": "small_scale",
            "filing_cycle": "monthly",
            "vat_rate_bp": 300,
            "surtax_rate_bp": 0,
            "effective_from": "2026-10",
            "reason": "合成测试政策，仅用于 OPT07-E 冻结验证",
        },
    )
    items = TaxPolicyItemService(case.db)
    items.add(
        case.manager,
        case.book["id"],
        [
            TaxPolicyItemWrite(
                kind="vat_output",
                code="VAT-6",
                name="合成 6% 销项",
                basis_kind="output_net",
                rate_bp=600,
                effective_from="2026-10",
                authority="合成政策条目，仅测试",
                approved_by=case.manager.user_id,
            )
        ],
    )
    invoices(case, [row("output", 10_000, 600)])
    paper = workpapers.generate(
        case.manager,
        case.book["id"],
        reason="OPT07-E 冻结验证",
        vat_accounts=[],
    )
    reviewed = workpapers.review(
        case.actors["accountant"],
        case.book["id"],
        paper["id"],
        "条目与底稿已核对",
        paper["version"],
    )
    items.add(
        case.manager,
        case.book["id"],
        [
            TaxPolicyItemWrite(
                kind="culture_fee",
                code="CULTURE-3",
                name="合成文化事业建设费",
                basis_kind="output_net",
                rate_bp=300,
                effective_from="2026-10",
                authority="合成政策条目，仅测试",
                approved_by=case.manager.user_id,
            )
        ],
    )
    current = workpapers.get(case.manager, case.book["id"], paper["id"])
    assert current["tax"] == "6.00"
    assert [line["code"] for line in current["source_snapshot"]["tax_policy"]["lines"]] == ["VAT-6"]
    try:
        workpapers.csv(
            case.actors["accountant"],
            case.book["id"],
            reviewed["id"],
            reviewed["version"],
            reviewed["source_digest"],
        )
    except AccountingError as exc:
        assert exc.status == 409
        assert "来源" in str(exc) or "摘要" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("changed policy must block export of the frozen workpaper")
