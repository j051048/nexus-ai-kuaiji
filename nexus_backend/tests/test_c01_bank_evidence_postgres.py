"""Real PG identities, native SQL and concurrent bank evidence review."""

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
from types import SimpleNamespace

import pytest

from app.accounting.domain import AccountingError
from app.subledger.models import BankCoverageReview
import test_c01_bank_evidence as checks
import test_posting_service
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres

pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="requires explicit disposable C01 PostgreSQL target",
)


@pytest.fixture
def coverage_pg(business_case, tmp_path, monkeypatch):
    p = business_case
    opening_reviewer = p.person(firm_role="admin")
    monkeypatch.setattr(test_posting_service, "admin", lambda _: opening_reviewer)

    def ledger_of(case):
        with case.db.transaction(actor=case.manager) as conn:
            return conn.execute(
                "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (case.book["id"],)
            ).fetchone()[0]

    monkeypatch.setattr(test_posting_service, "ledger_of", ledger_of)
    adapted = SimpleNamespace(
        db=p.db,
        manager=p.owner,
        ledger=p.service,
        book={"id": p.book, "company_id": p.company},
        other_book={"id": p.other_book, "company_id": p.other_company},
        actors={"accountant": p.reviewer, "employee": p.employee},
    )
    fixture = checks.coverage_case.__wrapped__(adapted, tmp_path)
    fixture.pg = p
    return fixture


def test_pg_confirmed_original_independent_review_and_replay(coverage_pg):
    checks.test_pending_independent_review_idempotency_and_terminal_immutability(coverage_pg)


@pytest.mark.parametrize(
    "change",
    [
        {"source_sha256": "f" * 64},
        {"source_preview_id": "missing"},
        {"source_ref": "forged"},
        {"closing": "9999.69"},
        {"period": "2026-09"},
    ],
)
def test_pg_source_binding_is_not_only_a_sqlite_contract(coverage_pg, change):
    checks.test_invalid_source_or_amount_is_rejected(coverage_pg, change)


def test_pg_bank_scope_and_original_permissions(coverage_pg):
    checks.test_other_company_original_and_wrong_bank_batch_are_rejected(coverage_pg)


def test_pg_real_blob_missing_at_review(coverage_pg):
    checks.test_corrupt_or_missing_original_rechecked_at_review(coverage_pg, True)


def test_pg_inactive_account_cannot_skip_pending_coverage(coverage_pg):
    checks.test_inactive_account_cannot_hide_pending_coverage(coverage_pg)


def test_pg_signed_balances_remain_reviewable(coverage_pg):
    checks.test_signed_bank_balances_have_the_same_api_and_sql_contract(coverage_pg)


@pytest.mark.parametrize(
    "field,value",
    [
        (None, None),
        ("source_sha256", "f" * 64),
        ("opening_cents", 1),
        ("state", "verified"),
        ("created_by", "forged"),
    ],
)
def test_pg_native_sql_cannot_forge_evidence(coverage_pg, field, value):
    c = coverage_pg
    row = c.sub.declare_bank_coverage(c.c.manager, checks.declaration(c))
    with c.c.db.transaction(actor=c.c.manager) as conn:
        raw = dict(
            conn.execute(
                "SELECT * FROM bank_statement_coverages WHERE id=?", (row["id"],)
            ).fetchone()
        )
    checks.review(c, c.pg.reviewer, row, decision="rejected")
    raw["id"] = __import__("uuid").uuid4().hex
    if field:
        raw[field] = value
    # Identifier names come only from the known table columns above.
    keys = list(raw)

    def insert():
        with c.c.db.transaction(write=True, actor=c.c.manager) as conn:
            conn.execute(
                "INSERT INTO bank_statement_coverages("
                + ",".join(keys)
                + ") VALUES("
                + ",".join("?" for _ in keys)
                + ")",
                tuple(raw[k] for k in keys),
            )

    if field:
        with pytest.raises(c.pg.psycopg.Error) as denied:
            insert()
        assert denied.value.sqlstate in {"42501", "23514"}
    else:
        insert()


def test_pg_native_review_cannot_use_creator_or_editor(coverage_pg):
    c = coverage_pg
    row = c.sub.declare_bank_coverage(c.c.manager, checks.declaration(c))
    for actor in [c.c.manager, c.pg.finance]:
        with pytest.raises(c.pg.psycopg.errors.InsufficientPrivilege):
            with c.c.db.transaction(write=True, actor=actor) as conn:
                conn.execute(
                    "UPDATE bank_statement_coverages SET state='verified',version=2,verified_by=?,verified_at='2026-10-23',review_reason='伪造复核必须拒绝' WHERE id=?",
                    (actor.user_id, row["id"]),
                )


def test_pg_two_reviewers_only_one_wins(coverage_pg):
    c = coverage_pg
    row = c.sub.declare_bank_coverage(c.c.manager, checks.declaration(c))
    other = c.pg.person(firm_role="reviewer", book_role="reviewer")
    barrier = Barrier(2)

    def finish(actor):
        barrier.wait(5)
        try:
            return c.sub.review_bank_coverage(
                actor,
                row["id"],
                BankCoverageReview(
                    expected_version=1, decision="verified", reason="并发复核银行原件"
                ),
            )["state"]
        except AccountingError as error:
            return error.status

    with ThreadPoolExecutor(2) as pool:
        result = list(pool.map(finish, [c.pg.reviewer, other]))
    assert sorted(map(str, result)) == ["409", "verified"]
    with c.c.db.transaction(actor=c.c.manager) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM audit_events WHERE object_id=? AND action='bank.coverage.review'",
                (row["id"],),
            ).fetchone()[0]
            == 1
        )
