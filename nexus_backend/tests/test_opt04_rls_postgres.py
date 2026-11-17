"""OPT04-A: tenant reads and write capabilities are enforced by PostgreSQL RLS."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from scripts.pg_suite_target import load_suite_target, verify_suite_target

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = os.getenv("ACCOUNTING_TEST_DATABASE_URL")


@pytest.fixture(scope="module")
def pg():
    import psycopg
    from scripts.migrate_accounting import migrate

    target = load_suite_target()
    if target is None:
        pytest.skip("OPT04 PG requires an explicit isolated PostgreSQL runner")
    target = verify_suite_target(target)
    assert (ADMIN_URL, APP_URL) == (target["admin"], target["app"])
    migrate(target["admin"])
    yield psycopg


@pytest.fixture
def tenants(pg):
    psycopg = pg
    suffix = uuid4().hex[:10]
    firm_a, firm_b = f"opt04-a-{suffix}", f"opt04-b-{suffix}"
    owner_a, editor_a, owner_b = (
        f"opt04-owner-a-{suffix}",
        f"opt04-editor-a-{suffix}",
        f"opt04-owner-b-{suffix}",
    )
    company_a, company_b = f"opt04-co-a-{suffix}", f"opt04-co-b-{suffix}"
    book_a, book_b = f"opt04-book-a-{suffix}", f"opt04-book-b-{suffix}"
    with psycopg.connect(ADMIN_URL) as conn:
        for user, role in ((owner_a, "owner"), (editor_a, "viewer"), (owner_b, "owner")):
            conn.execute(
                "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
                (user, role, f"{user}@example.test"),
            )
        for firm, owner in ((firm_a, owner_a), (firm_b, owner_b)):
            conn.execute(
                "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
                (firm, f"虚构OPT04事务所{firm}", owner),
            )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
            (firm_a, owner_a),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'viewer')",
            (firm_a, editor_a),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
            (firm_b, owner_b),
        )
        for company in (company_a, company_b):
            conn.execute(
                "INSERT INTO accounting.companies(id,name,tax_id) VALUES(%s,%s,%s)",
                (company, f"虚构OPT04企业{company}", ""),
            )
        conn.execute(
            "INSERT INTO accounting.firm_clients(firm_id,company_id) VALUES(%s,%s),(%s,%s)",
            (firm_a, company_a, firm_b, company_b),
        )
        for book, firm, company, name in (
            (book_a, firm_a, company_a, "OPT04甲账套"),
            (book_b, firm_b, company_b, "OPT04乙账套"),
        ):
            conn.execute(
                "INSERT INTO accounting.books(id,firm_id,company_id,name,period) VALUES(%s,%s,%s,%s,'2026-10')",
                (book, firm, company, name),
            )
        conn.execute(
            "INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,'editor')",
            (book_a, firm_a, editor_a),
        )
        for book, amount in ((book_a, 1000), (book_b, 2000)):
            conn.execute(
                "INSERT INTO accounting.fee_receivable_items"
                "(id,book_id,batch_id,batch_row_id,period,driver_id,driver_name,vehicle_name,amount_cents,status,created_at)"
                " VALUES(%s,%s,%s,%s,'2026-10',%s,'虚构驾驶员','虚构车辆',%s,'open',now())",
                (
                    f"opt04-fee-{book}",
                    book,
                    f"batch-{book}",
                    f"row-{book}",
                    f"driver-{book}",
                    amount,
                ),
            )
        for i, (firm, company, book) in enumerate(
            ((firm_a, company_a, book_a), (firm_b, company_b, book_b))
        ):
            conn.execute(
                "INSERT INTO accounting.fsc_company_profiles"
                "(id,firm_id,company_id,version,status,industry,payload_json,confirmations_json,fingerprint,created_by,created_at)"
                " VALUES(%s,%s,%s,1,'draft','taxi',%s::jsonb,'{}'::jsonb,%s,%s,now()::text)",
                (
                    f"opt04-profile-{book}",
                    firm,
                    company,
                    "{}",
                    f"fp-{i}",
                    owner_a if i == 0 else owner_b,
                ),
            )
    return {
        "firm_a": firm_a,
        "firm_b": firm_b,
        "owner_a": owner_a,
        "editor_a": editor_a,
        "owner_b": owner_b,
        "company_a": company_a,
        "company_b": company_b,
        "book_a": book_a,
        "book_b": book_b,
    }


def _as(psycopg, firm, user):
    conn = psycopg.connect(APP_URL)
    conn.execute("BEGIN")
    conn.execute("SET LOCAL ROLE accounting_app")
    conn.execute("SET LOCAL search_path TO accounting,pg_catalog")
    conn.execute(
        "SELECT set_config('app.user_id',%s,true),set_config('app.firm_id',%s,true)",
        (user, firm),
    )
    return conn


def test_fee_and_profile_rls_reject_cross_tenant_and_read_only_writes(pg, tenants):
    psycopg = pg

    conn = _as(psycopg, tenants["firm_a"], tenants["owner_a"])
    try:
        assert (
            conn.execute("SELECT count(*) FROM accounting.fee_receivable_items").fetchone()[0] == 1
        )
        assert (
            conn.execute("SELECT count(*) FROM accounting.fsc_company_profiles").fetchone()[0] == 1
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO accounting.fee_receivable_items"
                "(id,book_id,batch_id,batch_row_id,period,driver_name,vehicle_name,amount_cents,status,created_at)"
                " VALUES('cross-fee',%s,'cross-batch','cross-row','2026-10','x','y',1,'open',now())",
                (tenants["book_b"],),
            )
    finally:
        conn.rollback()
        conn.close()

    conn = _as(psycopg, tenants["firm_a"], tenants["owner_a"])
    try:
        conn.execute(
            "INSERT INTO accounting.fsc_company_profiles"
            "(id,firm_id,company_id,version,status,industry,payload_json,confirmations_json,fingerprint,created_by,created_at)"
            " VALUES('opt04-profile-a2',%s,%s,2,'draft','taxi','{}'::jsonb,'{}'::jsonb,'fp-a2',%s,now()::text)",
            (tenants["firm_a"], tenants["company_a"], tenants["owner_a"]),
        )
    finally:
        conn.rollback()
        conn.close()

    conn = _as(psycopg, tenants["firm_a"], tenants["editor_a"])
    try:
        assert (
            conn.execute("SELECT count(*) FROM accounting.fee_receivable_items").fetchone()[0] == 1
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO accounting.fee_receivable_items"
                "(id,book_id,batch_id,batch_row_id,period,driver_name,vehicle_name,amount_cents,status,created_at)"
                " VALUES('editor-fee',%s,'editor-batch','editor-row','2026-10','x','y',1,'open',now())",
                (tenants["book_a"],),
            )
    finally:
        conn.rollback()
        conn.close()

    conn = _as(psycopg, tenants["firm_a"], tenants["editor_a"])
    try:
        assert (
            conn.execute(
                "UPDATE accounting.fee_receivable_items SET status='settled' WHERE book_id=%s",
                (tenants["book_a"],),
            ).rowcount
            == 0
        )
    finally:
        conn.rollback()
        conn.close()

    conn = _as(psycopg, tenants["firm_a"], tenants["editor_a"])
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO accounting.fsc_company_profiles"
                "(id,firm_id,company_id,version,status,industry,payload_json,confirmations_json,fingerprint,created_by,created_at)"
                " VALUES('opt04-profile-editor',%s,%s,3,'draft','taxi','{}'::jsonb,'{}'::jsonb,'fp-e',%s,now()::text)",
                (tenants["firm_a"], tenants["company_a"], tenants["editor_a"]),
            )
    finally:
        conn.rollback()
        conn.close()

    conn = _as(psycopg, tenants["firm_b"], tenants["owner_b"])
    try:
        assert (
            conn.execute("SELECT count(*) FROM accounting.fee_receivable_items").fetchone()[0] == 1
        )
        assert (
            conn.execute("SELECT count(*) FROM accounting.fsc_company_profiles").fetchone()[0] == 1
        )
    finally:
        conn.rollback()
        conn.close()


def test_shared_company_keeps_profile_versions_per_firm(pg, tenants):
    """OPT04-B: one company served by two firms keeps independent version streams."""

    psycopg = pg
    suffix = uuid4().hex[:8]
    company = f"opt04-shared-{suffix}"
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.companies(id,name,tax_id) VALUES(%s,%s,'')",
            (company, f"虚构共享企业{suffix}"),
        )
        conn.execute(
            "INSERT INTO accounting.firm_clients(firm_id,company_id) VALUES(%s,%s),(%s,%s)",
            (tenants["firm_a"], company, tenants["firm_b"], company),
        )
        conn.execute(
            "INSERT INTO accounting.fsc_company_profiles"
            "(id,firm_id,company_id,version,status,industry,payload_json,confirmations_json,fingerprint,created_by,created_at)"
            " VALUES(%s,%s,%s,1,'draft','taxi','{}'::jsonb,'{}'::jsonb,%s,%s,now()::text)",
            (f"opt04-shared-a-{suffix}", tenants["firm_a"], company, "fp-a", tenants["owner_a"]),
        )
        conn.execute(
            "INSERT INTO accounting.fsc_company_profiles"
            "(id,firm_id,company_id,version,status,industry,payload_json,confirmations_json,fingerprint,created_by,created_at)"
            " VALUES(%s,%s,%s,1,'draft','taxi','{}'::jsonb,'{}'::jsonb,%s,%s,now()::text)",
            (f"opt04-shared-b-{suffix}", tenants["firm_b"], company, "fp-b", tenants["owner_b"]),
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM accounting.fsc_company_profiles WHERE company_id=%s",
                (company,),
            ).fetchone()[0]
            == 2
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO accounting.fsc_company_profiles"
                "(id,firm_id,company_id,version,status,industry,payload_json,confirmations_json,fingerprint,created_by,created_at)"
                " VALUES(%s,%s,%s,1,'draft','taxi','{}'::jsonb,'{}'::jsonb,%s,%s,now()::text)",
                (f"opt04-shared-dupe-{suffix}", tenants["firm_a"], company, "fp-dupe", tenants["owner_a"]),
            )
        conn.rollback()
