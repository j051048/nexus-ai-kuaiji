"""A–D boundary acceptance through real restricted PostgreSQL identities."""
import pytest
from uuid import uuid4
from app.accounting.service import Actor
from app.tax.fees import TaxFeeService
from test_tax06_fees import POLICY
from test_tax06_fees_postgres import pg, case, ADMIN_URL  # noqa: F401
import os

pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="需要显式 AK05 四角色隔离 PostgreSQL 运行器",
)


def test_viewer_cannot_forge_fee_review_with_direct_sql(case):
    import psycopg
    business, source = case
    service = TaxFeeService(business.db)
    batch = service.generate(business.manager, business.book["id"], period="2026-10",
                             source_file_ids=[source["id"]], policy=POLICY)
    user_id = "taxi-viewer-" + uuid4().hex
    viewer = Actor(user_id=user_id, firm_id=business.manager.firm_id)
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute("INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
                     (user_id, "只读验收", user_id + "@example.test"))
        conn.execute("INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'viewer')",
                     (viewer.firm_id, user_id))
        conn.execute("INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,'viewer')",
                     (business.book["id"], viewer.firm_id, user_id))
    with business.db.transaction(write=True, actor=viewer) as conn:
        try:
            result = conn.execute("UPDATE tax_fee_batches SET status='reviewed',reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?", (user_id, batch["id"]))
        except psycopg.Error as error:
            assert error.sqlstate in {"42501", "23514"}
        else:
            assert result.rowcount == 0


def test_manager_cannot_forge_independent_review_evidence(case):
    import psycopg
    business, source = case
    batch = TaxFeeService(business.db).generate(business.manager, business.book["id"],
        period="2026-10", source_file_ids=[source["id"]], policy=POLICY)
    with pytest.raises(psycopg.Error) as rejected:
        with business.db.transaction(write=True, actor=business.manager) as conn:
            conn.execute("UPDATE tax_fee_batches SET status='reviewed',reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?",
                         (business.reviewer.user_id, batch["id"]))
    assert rejected.value.sqlstate == "42501"


def test_concurrent_generation_reuses_one_draft_and_revocation_blocks_review(case):
    from concurrent.futures import ThreadPoolExecutor
    import psycopg
    from app.accounting.domain import AccountingError
    business, source = case
    def generate():
        return TaxFeeService(business.db).generate(business.manager, business.book["id"],
            period="2026-10", source_file_ids=[source["id"]], policy=POLICY)
    with ThreadPoolExecutor(max_workers=2) as executor:
        batches = list(executor.map(lambda _: generate(), range(2)))
    assert batches[0]["id"] == batches[1]["id"]
    assert batches[0]["draft_id"] == batches[1]["draft_id"]
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute("DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
                     (business.book["id"], business.reviewer.user_id))
    with pytest.raises(AccountingError):
        TaxFeeService(business.db).review(business.reviewer, business.book["id"], batches[0]["id"],
                                        expected_version=1, reason="已撤权不可复核")


def test_source_change_racing_review_never_allows_stale_export(case):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.accounting.domain import AccountingError
    from app.accounting.models import ExportRequest
    from app.accounting.service import AccountingService
    from app.tax.sources import TaxSourceAdapter
    from test_tax06_fees import ROWS, COLS
    from test_tax04_sources import xlsx
    business, source = case
    fees = TaxFeeService(business.db)
    batch = fees.generate(business.manager, business.book["id"], period="2026-10",
                          source_file_ids=[source["id"]], policy=POLICY)
    barrier = Barrier(2)
    def upgrade():
        barrier.wait(timeout=5)
        return TaxSourceAdapter(business.db).import_source(business.manager, business.book["id"],
            filename="规费.xlsx", data=xlsx({"运输明细": ROWS}), source_system="客户运输台账",
            external_id="S-FEE-PG", version=2, sheet_name="运输明细", column_map=COLS,
            rule_version="tax-source-v1")
    def review():
        barrier.wait(timeout=5)
        try:
            return fees.review(business.reviewer, business.book["id"], batch["id"], expected_version=1, reason="并发复核")
        except AccountingError as error:
            assert error.status in {409, 422}
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        changed, reviewed = executor.submit(upgrade), executor.submit(review)
        changed.result(timeout=30)
        reviewed.result(timeout=30)
    with pytest.raises(AccountingError):
        AccountingService(business.db).export(business.reviewer, business.book["id"],
            ExportRequest(draft_ids=[batch["draft_id"]], idempotency_key="stale-race-export"))
