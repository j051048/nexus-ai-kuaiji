"""TAX13–15: PostgreSQL proof for leases, tax basis and profit mapping."""

from __future__ import annotations

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import AccountingError, Actor
from app.accounting.models import ApprovalRequest, BookCreate
from app.accounting.service import AccountingService
from app.reports.profit import ProfitDrilldownService, ProfitMappingItem, ProfitMappingWrite
from app.tax.basis import TaxBasisGenerate, TaxBasisPolicy, TaxBasisReview, TaxBasisAdjustment, TaxBasisService
from app.tax.leases import LeaseAmend, LeaseConfirmation, LeaseCreate, LeaseInvoiceRed, LeasePolicy, LeaseRecognition, LeaseService
from scripts.pg_suite_target import load_suite_target, verify_suite_target

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = os.getenv("ACCOUNTING_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="需要显式 AK05 四角色隔离 PostgreSQL 运行器",
)

LEASE_POLICY = LeasePolicy(
    version="lease-v1", advance_account="2202", revenue_account="6001", refund_account="6001",
)
BASIS_POLICY = TaxBasisPolicy(
    version="basis-v1", taxpayer_type="small_scale", rate_bp=300, surtax_bp=200,
    effective_from="2026-01", authority="虚构税务局", source_ref="政策文件-1",
)


@pytest.fixture(scope="module")
def pg():
    from scripts.migrate_accounting import migrate

    target = load_suite_target()
    if target is None:
        pytest.skip("TAX13 PG requires an explicit isolated PostgreSQL runner")
    target = verify_suite_target(target)
    assert (ADMIN_URL, APP_URL) == (target["admin"], target["app"])
    migrate(target["admin"])
    database = Database(target["app"])
    yield database
    database.close()


@pytest.fixture
def case(pg):
    import psycopg

    db = pg
    suffix = uuid4().hex[:12]
    firm = "taxf-firm-" + suffix
    maker = Actor(user_id=f"taxf-maker-{suffix}", firm_id=firm)
    reviewer = Actor(user_id=f"taxf-reviewer-{suffix}", firm_id=firm)
    with psycopg.connect(ADMIN_URL) as conn:
        for actor, role in ((maker, "owner"), (reviewer, "reviewer")):
            conn.execute(
                "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
                (actor.user_id, role, f"taxf-{role}-{suffix}@example.test"),
            )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
            (firm, "虚构TAXF事务所", maker.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
            (firm, maker.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'reviewer')",
            (firm, reviewer.user_id),
        )
    accounting = AccountingService(db)
    book = accounting.create_book(maker, BookCreate(name="虚构TAXF账套", period="2026-10"))
    baseline_book = accounting.create_book(
        maker,
        BookCreate(name="虚构TAXF对比账套", period="2026-10", company_id=book["company_id"]),
    )
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,'reviewer')",
            (book["id"], firm, reviewer.user_id),
        )
    return SimpleNamespace(db=db, manager=maker, reviewer=reviewer, book=book, baseline_book=baseline_book)


def test_lease_recognizes_prepaid_income_after_business_confirmation(case):
    service = LeaseService(case.db)
    contract = service.create(
        case.manager, case.book["id"],
        LeaseCreate(
            customer_name="虚构租赁客户", customer_external_id="C-1", vehicle_external_id="V-1",
            start_period="2026-10", end_period="2026-11", monthly="1200.00",
        ),
    )
    assert contract["version"] == 1
    service.confirm(
        case.manager, case.book["id"], contract["id"],
        LeaseConfirmation(kind="business", period="2026-10", amount="1200.00", reference="业务确认-1"),
    )
    service.confirm(
        case.manager, case.book["id"], contract["id"],
        LeaseConfirmation(kind="receipt", period="2026-10", amount="1200.00", reference="收款-1"),
    )
    view = service.recognize(
        case.manager, case.book["id"], contract["id"],
        LeaseRecognition(period="2026-10", policy=LEASE_POLICY, reason="确认十月租赁收入"),
    )
    plan = [row for row in view["plan"] if row["period"] == "2026-10" and row["kind"] == "revenue"][0]
    assert plan["status"] == "recognized"
    draft = AccountingService(case.db).get_draft(case.manager, case.book["id"], plan["draft_id"])
    assert draft["status"] == "draft"
    assert draft["lines"][0]["debit"] == "1200.00"


def test_tax_basis_is_versioned_and_independently_reviewed(case):
    service = TaxBasisService(case.db)
    created = service.generate(
        case.manager, case.book["id"],
        TaxBasisGenerate(
            period="2026-10", policy=BASIS_POLICY, base="100000.00",
            adjustments=[TaxBasisAdjustment(code="免税调整", amount="-1000.00", reason="核定调减")],
            manual=False, manual_evidence="", source_refs=["申报表-1"],
        ),
    )
    assert created["version"] == 1
    assert created["tax_cents"] == 206000
    reviewed = service.review(
        case.reviewer, case.book["id"], created["id"],
        TaxBasisReview(expected_version=1, reason="独立复核税费底稿"),
    )
    assert reviewed["status"] == "reviewed"


def test_profit_mapping_is_firm_scoped_and_unknown_tax_stays_unknown(case):
    service = ProfitDrilldownService(case.db)
    mapping = service.mapping(case.manager, case.book["id"])
    assert mapping["version"] == 1
    changed = service.save_mapping(
        case.manager, case.book["id"],
        ProfitMappingWrite(items=[
            ProfitMappingItem(
                line_code="revenue", line_name="营业收入", line_kind="revenue",
                account_prefix="6001", sign=1,
            )
        ]),
    )
    assert changed["version"] == 1
    view = service.overview(case.manager, case.book["id"], "2026-10")
    assert view["totals"]["income_tax_cents"] is None
    assert view["totals"]["net_profit_cents"] is None
    with pytest.raises(Exception):
        service.overview(case.reviewer, "not-a-book", "2026-10")


def test_sync_stub_pg_signed_event_and_cursor(case, monkeypatch):
    import time
    from app.integrations.sync_stub import SyncEventWrite, SyncStubService, _payload_hash, _signature

    monkeypatch.setenv("ACCOUNTING_SYNC_STUB_SECRET", "pg-secret-0123456789")
    payload = {"amount": "10.00", "vehicle": "V1"}
    timestamp = int(time.time())
    digest = _payload_hash(payload)
    service = SyncStubService(case.db)
    first = service.push(
        case.manager, case.book["id"],
        SyncEventWrite(
            source_id="pg-file-link", external_id="PG-1", event_type="applied", version=1,
            occurred_at="2026-11-12T01:02:03Z", timestamp=timestamp,
            signature=_signature("pg-secret-0123456789", timestamp, "PG-1", 1, digest),
            payload=payload,
        ),
    )
    assert first["accepted"] is True and first["cursor"] == 1
    assert service.list_events(
        case.manager, case.book["id"], source_id="pg-file-link"
    )["items"][0]["external_id"] == "PG-1"


def test_value_baseline_is_empty_and_never_claims_labour_time(case):
    from app.observability.value import ValueBaselineService

    result = ValueBaselineService(case.db).overview(
        case.reviewer, case.book["company_id"], "2026-10"
    )
    assert result["counts"]["approved_drafts"] == 0
    assert result["rates"]["first_pass_rate"] is None
    assert result["elapsed"]["labour_time_verified"] is False
    assert result["cost"]["scope"] == "ai_model_only"


def test_closing_radar_is_read_only_and_lists_blockers(case):
    from app.observability.closing import ClosingRadarService

    result = ClosingRadarService(case.db).radar(case.manager, "2026-10")
    assert result["counts"]["books"] >= 1
    assert result["counts"]["blocked"] == sum(
        1 for item in result["books"] if item["blockers"]
    )
    assert result["note"].startswith("只读雷达")
    assert all(item["period_status"] in {"open", "closed"} for item in result["books"])


def test_profit_variance_compares_two_books_without_claiming_a_cause(case):
    service = ProfitDrilldownService(case.db)
    value = service.variance(case.manager, case.book["id"], case.baseline_book["id"])
    assert value["current_book_id"] == case.book["id"]
    assert value["baseline_book_id"] == case.baseline_book["id"]
    assert value["checks"]["causal_claim"] is False
    assert value["checks"]["line_count"] >= 1
    assert all(item["delta_cents"] == 0 for item in value["lines"])


def test_lease_invoice_red_reversal_is_immutable_and_scoped(case):
    service = LeaseService(case.db)
    contract = service.create(
        case.manager, case.book["id"],
        LeaseCreate(
            customer_name="虚构红冲客户", customer_external_id="C-RED", vehicle_external_id="V-RED",
            start_period="2026-10", end_period="2026-10", monthly="1200.00",
        ),
    )
    service.confirm(
        case.manager, case.book["id"], contract["id"],
        LeaseConfirmation(kind="invoice", period="2026-10", amount="1200.00", reference="INV-RED-1"),
    )
    view = service.red_invoice(
        case.manager, case.book["id"], contract["id"],
        LeaseInvoiceRed(
            original_reference="INV-RED-1", red_reference="RED-1", period="2026-10",
            amount="1200.00", reason="客户退租，原发票全额红冲",
        ),
    )
    assert len(view["invoice_reds"]) == 1
    assert view["invoice_reds"][0]["red_reference"] == "RED-1"
    with pytest.raises(AccountingError) as exc:
        service.red_invoice(
            case.manager, case.book["id"], contract["id"],
            LeaseInvoiceRed(
                original_reference="INV-RED-1", red_reference="RED-2", period="2026-10",
                amount="1200.00", reason="重复红冲",
            ),
        )
    assert exc.value.status == 409


def test_fsc_company_profile_confirm_is_immutable_and_scope_bound(case):
    import psycopg
    from app.fsc.profiles import FscProfileService, ProfileConfirm, ProfileWrite, REQUIRED, SECTIONS

    service = FscProfileService(case.db)
    confirmations = {
        key: {
            "status": "confirmed",
            "owner": "虚构客户负责人",
            "evidence_ref": f"FSC01-{key}",
            "confirmed_at": "2026-11-16T10:00:00+08:00",
        }
        for key in REQUIRED
    }
    payload = {key: {"status": "unknown", "note": "待客户输入"} for key in REQUIRED}
    payload["_confirmations"] = confirmations
    draft = service.create_draft(
        case.manager, case.book["company_id"],
        ProfileWrite(industry="taxi", payload=payload),
    )
    assert draft["status"] == "draft" and draft["version"] >= 1
    confirmed = service.confirm(case.manager, draft["id"], ProfileConfirm(expected_version=draft["version"]))
    assert confirmed["status"] == "confirmed"
    assert set(confirmed["confirmations"]) == set(REQUIRED)

    with pytest.raises(Exception) as immutable:
        with psycopg.connect(ADMIN_URL) as conn:
            conn.execute(
                "UPDATE accounting.fsc_company_profiles SET industry='tampered' WHERE id=%s",
                (draft["id"],),
            )
    assert "immutable" in str(immutable.value).lower() or "不可变" in str(immutable.value)

    suffix = uuid4().hex[:12]
    outsider = Actor(user_id=f"taxf-outsider-{suffix}", firm_id=f"taxf-outsider-firm-{suffix}")
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.users(id,name,email) VALUES(%s,'outsider',%s)",
            (outsider.user_id, f"taxf-outsider-{suffix}@example.test"),
        )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,'虚构外部所',%s)",
            (outsider.firm_id, outsider.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
            (outsider.firm_id, outsider.user_id),
        )
    with pytest.raises(AccountingError) as denied:
        service.active(outsider, case.book["company_id"])
    assert denied.value.status in {403, 404}


def test_stale_lease_draft_is_rejected_after_amendment_on_postgres(case):
    service = LeaseService(case.db)
    contract = service.create(
        case.manager, case.book["id"],
        LeaseCreate(
            customer_name="虚构租赁客户-STALE", customer_external_id="C-STALE",
            vehicle_external_id="V-STALE", start_period="2026-10", end_period="2026-11",
            monthly="1000.00",
        ),
    )
    service.confirm(
        case.manager, case.book["id"], contract["id"],
        LeaseConfirmation(kind="business", period="2026-11", amount="1000.00", reference="业务-STALE"),
    )
    view = service.recognize(
        case.manager, case.book["id"], contract["id"],
        LeaseRecognition(period="2026-11", policy=LEASE_POLICY, reason="旧版本收入草稿"),
    )
    draft_id = [row for row in view["plan"] if row["period"] == "2026-11" and row["kind"] == "revenue"][0]["draft_id"]
    service.amend(
        case.manager, case.book["id"], contract["id"],
        LeaseAmend(expected_version=1, end_period="2027-01", monthly="1100.00", reason="续签改版"),
    )
    service.approve(case.manager, case.book["id"], contract["id"], 2)
    with pytest.raises(AccountingError) as exc:
        AccountingService(case.db).approve(
            case.reviewer, case.book["id"], draft_id, ApprovalRequest(expected_version=1)
        )
    assert exc.value.status == 409


def test_two_companies_cross_year_lease_chain_keeps_drafts_isolated(case):
    accounting = AccountingService(case.db)
    second = accounting.create_book(
        case.manager, BookCreate(name="虚构TAXF第二企业账套", period="2026-10")
    )
    service = LeaseService(case.db)

    def recognize_two_periods(book_id: str, label: str) -> list[dict]:
        contract = service.create(
            case.manager,
            book_id,
            LeaseCreate(
                customer_name=f"虚构跨年客户-{label}",
                customer_external_id=f"CROSS-{label}",
                vehicle_external_id=f"V-CROSS-{label}",
                start_period="2026-12",
                end_period="2027-02",
                monthly="1000.00",
            ),
        )
        for period in ("2026-12", "2027-01"):
            service.confirm(
                case.manager,
                book_id,
                contract["id"],
                LeaseConfirmation(
                    kind="business",
                    period=period,
                    amount="1000.00",
                    reference=f"业务确认-{label}-{period}",
                ),
            )
            service.confirm(
                case.manager,
                book_id,
                contract["id"],
                LeaseConfirmation(
                    kind="receipt",
                    period=period,
                    amount="1000.00",
                    reference=f"收款-{label}-{period}",
                ),
            )
        drafts = []
        for period in ("2026-12", "2027-01"):
            view = service.recognize(
                case.manager,
                book_id,
                contract["id"],
                LeaseRecognition(
                    period=period,
                    policy=LEASE_POLICY,
                    reason=f"跨年确认-{label}-{period}",
                ),
            )
            plan = [
                row
                for row in view["plan"]
                if row["period"] == period and row["kind"] == "revenue"
            ][0]
            assert plan["status"] == "recognized"
            draft = accounting.get_draft(case.manager, book_id, plan["draft_id"])
            assert draft["status"] == "draft"
            assert draft["lines"][0]["debit"] == "1000.00"
            drafts.append((period, draft))
        return drafts

    first_drafts = recognize_two_periods(case.book["id"], "A")
    second_drafts = recognize_two_periods(second["id"], "B")
    assert {draft["id"] for _, draft in first_drafts}.isdisjoint(
        {draft["id"] for _, draft in second_drafts}
    )
    assert [period for period, _ in first_drafts] == ["2026-12", "2027-01"]
    assert [period for period, _ in second_drafts] == ["2026-12", "2027-01"]
