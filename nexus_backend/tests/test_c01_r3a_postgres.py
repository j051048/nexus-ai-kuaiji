"""C01-R3A PostgreSQL-only identity guard.

The locked-period coverage guard is present in migration 133 but its dedicated
two-session proof is tracked as remaining C01-R3A work; it is not claimed here.
"""

import pytest

from test_postgres_integration import ADMIN_URL, case, postgres  # noqa: F401


def test_bank_coverage_rejects_forged_verifier(case):
    from uuid import uuid4

    bank_id = "c01-bank-" + uuid4().hex
    coverage_id = "c01-cov-" + uuid4().hex
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("SELECT set_config('app.user_id',%s,true)", (case.actor.user_id,))
        conn.execute(
            "INSERT INTO accounting.bank_accounts(id,firm_id,company_id,book_id,name,currency,"
            "created_by,created_at) VALUES(%s,%s,%s,%s,'C01 基本户','CNY',%s,%s)",
            (
                bank_id,
                case.actor.firm_id,
                case.company,
                case.book,
                case.actor.user_id,
                "2026-10-01T00:00:00+00:00",
            ),
        )
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=case.actor) as conn:
            conn.execute(
                "INSERT INTO bank_statement_coverages(id,account_id,book_id,period,opening_cents,"
                "closing_cents,source_ref,verified_by,verified_at,created_at) "
                "VALUES(? ,? ,?,'2026-10',0,0,'synthetic','forged-user',?,?)",
                (
                    coverage_id,
                    bank_id,
                    case.book,
                    "2026-10-01T00:00:00+00:00",
                    "2026-10-01T00:00:00+00:00",
                ),
            )
