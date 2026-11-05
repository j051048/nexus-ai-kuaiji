"""Current authorization, page boundaries and honest month summaries."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.accounting.models import (
    ApprovalRequest,
    BookCreate,
    ExportRequest,
    ImportRequest,
    SourceRow,
)
from app.accounting.domain import AccountingError
from app.accounting.directory import DirectoryService
from app.business.models import BusinessEventCreate
from app.business.service import BusinessService
from app.cases.service import CaseService
from app.closing.models import PreflightRequest
from app.closing.service import ClosingService
from app.delivery.models import ReceiptCreate
from app.delivery.service import DeliveryService
from app.main import create_app
from app.monthly.workbench import MonthlyWorkbenchService
from test_business import business, create, payload
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres


@pytest.fixture(
    params=[
        "sqlite",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(
                not ADMIN_URL or not APP_URL, reason="需要独立 PostgreSQL 测试账号"
            ),
        ),
    ]
)
def month_case(request):
    if request.param == "sqlite":
        c = request.getfixturevalue("business")
        result = SimpleNamespace(
            db=c.db,
            accounting=c.ledger,
            business=c.service,
            owner=c.manager,
            reviewer=c.actors["accountant"],
            employee=c.actors["employee"],
            finance=c.actors["finance"],
            book=c.book["id"],
            company=c.company["id"],
            other_book=c.other_book["id"],
            other_company=c.other["id"],
            raw=c,
        )
    else:
        c = request.getfixturevalue("business_case")
        result = SimpleNamespace(
            db=c.db,
            accounting=c.service,
            business=BusinessService(c.db),
            owner=c.owner,
            reviewer=c.reviewer,
            employee=c.employee,
            finance=c.finance,
            book=c.book,
            company=c.company,
            other_book=c.other_book,
            other_company=c.other_company,
            raw=c,
        )
    result.monthly = MonthlyWorkbenchService(
        result.db, result.accounting, result.business, CaseService(result.db, result.business)
    )
    return result


def find(case, actor=None, **filters):
    rows = case.monthly.list(actor or case.owner, "2026-10", **filters)["rows"]
    return next(row for row in rows if row["book_id"] == case.book)


def imported(case, evidence="合成原件引用"):
    case.accounting.import_sources(
        case.owner,
        case.book,
        ImportRequest(
            source_name="合成月度流水",
            rows=[
                SourceRow(
                    external_id="month-summary-1",
                    date="2026-10-03",
                    description="办公耗材",
                    counterparty="合成商店",
                    amount="0.30",
                    direction="expense",
                    evidence_ref=evidence,
                )
            ],
        ),
    )
    case.accounting.suggestions(case.owner, case.book)
    return case.accounting.get_book(case.owner, case.book)["drafts"][0]


def test_monthly_rows_use_current_book_permissions_and_hide_private_intake(month_case):
    c = month_case
    owned = c.monthly.list(c.owner, "2026-10")
    assert owned["pagination"]["total"] == 2
    assigned = c.monthly.list(c.reviewer, "2026-10")
    assert [row["book_id"] for row in assigned["rows"]] == [c.book]
    assert c.monthly.list(c.employee, "2026-10")["rows"] == []
    assert c.monthly.list(c.reviewer, "2026-10", search="not-a-client")["rows"] == []
    row = assigned["rows"][0]
    assert row["materials"]["intake_pending"] is None
    assert row["materials"]["business_pending"] is None
    assert row["efficiency"]["handling_seconds"] is None
    assert row["closing"]["state"] == "not_checked"
    assert "payload" not in json.dumps(row)


def test_monthly_authorization_precedes_pagination_and_same_company_books_stay_distinct(month_case):
    c = month_case
    created = []
    for index in range(22):
        book = c.accounting.create_book(
            c.owner, BookCreate(name=f"分页合成{index:02d}", period="2026-10", company_id=c.company)
        )
        created.append(book["id"])
    first = c.monthly.list(c.owner, "2026-10", limit=20)
    second = c.monthly.list(c.owner, "2026-10", limit=20, offset=20)
    assert first["pagination"]["total"] == second["pagination"]["total"] == 24
    assert len(first["rows"]) == 20 and len(second["rows"]) == 4
    assert not ({r["row_key"] for r in first["rows"]} & {r["row_key"] for r in second["rows"]})
    assert len({r["row_key"] for r in first["rows"] + second["rows"]}) == 24
    assert c.monthly.list(c.reviewer, "2026-10", offset=20)["pagination"]["total"] == 1
    assert c.monthly.list(c.reviewer, "2026-10", offset=20)["rows"] == []
    assert c.monthly.list(c.owner, "2026-11")["rows"][0]["status"] == "no_book"
    assert c.monthly.list(c.reviewer, "2026-11")["rows"] == []


def test_monthly_search_treats_percent_underscore_as_literals(month_case):
    c = month_case
    assert c.monthly.list(c.owner, "2026-10", search="%")["rows"] == []
    assert c.monthly.list(c.owner, "2026-10", search="_")["rows"] == []
    with pytest.raises(AccountingError):
        c.monthly.list(c.owner, "2026-13")
    with pytest.raises(AccountingError):
        c.monthly.list(c.owner, "2026-10", limit=101)


def test_monthly_revocation_removes_row_count_and_drilldown_immediately(month_case):
    c = month_case
    assert c.monthly.list(c.reviewer, "2026-10")["pagination"]["total"] == 1
    DirectoryService(c.db).revoke_book_member(c.owner, c.book, c.reviewer.user_id)
    result = c.monthly.list(c.reviewer, "2026-10")
    assert result["rows"] == [] and result["pagination"]["total"] == 0
    assert c.book not in json.dumps(result)
    with pytest.raises(AccountingError) as error:
        c.accounting.get_book(c.reviewer, c.book)
    assert error.value.status == 404


def test_monthly_pending_materials_match_finance_or_accounting_drilldown(month_case):
    c = month_case
    event = c.business.create(
        c.employee,
        BusinessEventCreate(company_id=c.company, template_id="reimbursement", payload=payload()),
    )
    with c.db.transaction(write=True, actor=c.owner) as conn:
        department = conn.execute(
            "SELECT department_id FROM company_memberships WHERE company_id=? AND user_id=?",
            (c.company, c.employee.user_id),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO company_memberships(company_id,user_id,role,department_id) VALUES(?,?,'finance',?)",
            (c.company, c.reviewer.user_id, department),
        )
    assert find(c, c.reviewer)["materials"]["business_pending"] == 1
    assert event["id"] in json.dumps(
        c.business.list_events(c.reviewer, scope="company", company_id=c.company, period="2026-10")
    )
    with c.db.transaction(write=True, actor=c.owner) as conn:
        conn.execute(
            "UPDATE company_memberships SET role='employee' WHERE company_id=? AND user_id=?",
            (c.company, c.reviewer.user_id),
        )
    own = c.business.create(
        c.reviewer,
        BusinessEventCreate(company_id=c.company, template_id="reimbursement", payload=payload()),
    )
    assert own["id"] in json.dumps(c.business.list_events(c.reviewer, scope="own"))
    assert find(c, c.reviewer)["materials"]["business_pending"] is None
    assert (
        c.business.list_events(
            c.reviewer, scope="accounting", company_id=c.company, period="2026-10"
        )["events"]
        == []
    )


def test_matrix_page_reads_stay_bounded_as_visible_book_count_grows(business, monkeypatch):
    c = business
    for index in range(22):
        c.ledger.create_book(
            c.manager,
            BookCreate(name=f"有界查询{index:02d}", period="2026-10", company_id=c.company["id"]),
        )
    service = MonthlyWorkbenchService(c.db, c.ledger, c.service, CaseService(c.db, c.service))
    original = c.db.transaction
    reads = []

    class CountedConnection:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, parameters=None):
            reads.append(sql)
            return self.conn.execute(sql, parameters or [])

        def __getattr__(self, name):
            return getattr(self.conn, name)

    @contextmanager
    def counted(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            yield CountedConnection(conn)

    monkeypatch.setattr(c.db, "transaction", counted)
    assert len(service.list(c.manager, "2026-10", limit=1)["rows"]) == 1
    one_book_reads = len(reads)
    reads.clear()
    assert len(service.list(c.manager, "2026-10", limit=20)["rows"]) == 20
    assert len(reads) == one_book_reads and len(reads) <= 20


def test_monthly_source_missing_evidence_and_draft_counts_are_actual(month_case):
    c = month_case
    imported(c, evidence="")
    row = find(c)
    assert row["materials"]["sources"] == row["materials"]["missing"] == 1
    assert row["drafts"]["pending"] == row["drafts"]["basic_exceptions"] == 1
    assert row["drafts"]["approved"] == row["drafts"]["exported"] == 0


def test_monthly_export_receipt_unknown_then_verified_is_batch_scoped(month_case):
    c = month_case
    draft = imported(c)
    draft = c.accounting.approve(
        c.reviewer, c.book, draft["id"], ApprovalRequest(expected_version=draft["version"])
    )
    assert find(c)["drafts"]["approved"] == 1
    exported = c.accounting.export(
        c.owner, c.book, ExportRequest(draft_ids=[draft["id"]], idempotency_key="matrix-export-001")
    )
    assert find(c)["delivery"]["unverified"] == 1
    bridge = DeliveryService(c.db)
    manifest = bridge.manifest(c.owner, c.book, exported["id"])
    item = {
        key: manifest["items"][0][key]
        for key in ("draft_id", "date", "amount", "description", "lines")
    }
    bridge.create_receipt(
        c.owner,
        c.book,
        exported["id"],
        ReceiptCreate(
            idempotency_key="matrix-unknown-001",
            reason="合成未知回执",
            items=[{"draft_id": draft["id"], "status": "unknown"}],
        ),
    )
    row = find(c)
    assert row["delivery"] == {"unverified": 0, "unknown": 1, "differences": 0, "verified": 0}
    bridge.create_receipt(
        c.owner,
        c.book,
        exported["id"],
        ReceiptCreate(
            idempotency_key="matrix-verified-001",
            reason="核对合成凭证内容",
            book_id=c.book,
            company_id=c.company,
            period="2026-10",
            currency="CNY",
            items=[{**item, "status": "accepted", "external_voucher_id": "SYNTHETIC-凭证1"}],
        ),
    )
    row = find(c)
    assert row["delivery"] == {"unverified": 0, "unknown": 0, "differences": 0, "verified": 1}
    assert row["drafts"]["exported"] == 1


def test_old_passing_close_snapshot_does_not_claim_current_ready(month_case):
    c = month_case
    snapshot = ClosingService(c.db, c.accounting).preflight(
        c.owner, c.book, PreflightRequest(idempotency_key="matrix-close-preflight")
    )
    imported(c)
    row = find(c)
    assert row["closing"]["snapshot_id"] == snapshot["id"]
    assert row["closing"]["last_check"]["passed"] is True
    assert row["closing"]["state"] == "recheck" and row["closing"]["blockers"] is None
    assert row["drafts"]["pending"] == 1


def test_business_period_and_review_book_filter_do_not_expand_authority(business):
    c = business
    october = create(c)
    november = create(c, occurred_on="2026-11-03")
    result = c.service.list_events(c.actors["employee"], period="2026-10")
    assert [row["id"] for row in result["events"]] == [october["id"]]
    assert november["id"] not in json.dumps(result)
    from app.accounting.review import ReviewService

    second = c.ledger.create_book(
        c.manager, BookCreate(name="同企业第二账套", period="2026-10", company_id=c.company["id"])
    )
    for book_id in [c.book["id"], second["id"]]:
        c.ledger.import_sources(
            c.manager,
            book_id,
            ImportRequest(
                source_name="合成核对",
                rows=[
                    SourceRow(
                        external_id="single",
                        date="2026-10-01",
                        description="办公",
                        amount="0.30",
                        direction="expense",
                    )
                ],
            ),
        )
        c.ledger.suggestions(c.manager, book_id)
    review = ReviewService(c.db, c.ledger)
    only = review.list_drafts(c.manager, book_id=second["id"])
    assert len(only["items"]) == 1 and only["items"][0]["book_id"] == second["id"]
    assert review.list_drafts(c.actors["accountant"], book_id=second["id"])["items"] == []


def test_matrix_http_auth_and_invalid_month(tmp_path):
    with TestClient(
        create_app(tmp_path / "matrix-api.db", seed=False),
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 45678),
    ) as client:
        assert client.get("/api/monthly/workbench?period=2026-10").status_code == 401
        token = client.get("/api/session?persona=accountant").json()["token"]
        headers = {"Authorization": "Bearer " + token}
        assert (
            client.get("/api/monthly/workbench?period=2026-13", headers=headers).status_code == 422
        )
        good = client.get("/api/monthly/workbench?period=2026-10", headers=headers)
        assert good.status_code == 200
        assert "rows" in good.json()
