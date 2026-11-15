"""TAX13–15: PostgreSQL proof for leases, tax basis and profit mapping."""

from __future__ import annotations

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import Actor
from app.accounting.models import BookCreate
from app.accounting.service import AccountingService
from app.reports.profit import ProfitDrilldownService, ProfitMappingItem, ProfitMappingWrite
from app.tax.basis import TaxBasisGenerate, TaxBasisPolicy, TaxBasisReview, TaxBasisAdjustment, TaxBasisService
from app.tax.leases import LeaseConfirmation, LeaseCreate, LeasePolicy, LeaseRecognition, LeaseService
from scripts.pg_suite_target import load_suite_target, verify_suite_target

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = os.getenv("ACCOUNTING_TEST_DATABASE_URL")
pytestmark = []

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
    book = AccountingService(db).create_book(maker, BookCreate(name="虚构TAXF账套", period="2026-10"))
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,'reviewer')",
            (book["id"], firm, reviewer.user_id),
        )
    return SimpleNamespace(db=db, manager=maker, reviewer=reviewer, book=book)


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
