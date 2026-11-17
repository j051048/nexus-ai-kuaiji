"""OPT04-B: PostgreSQL profile versions are independent per firm."""

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


def test_profile_versions_are_independent_for_two_firms_sharing_a_company(pg):
    psycopg = pg
    suffix = uuid4().hex[:10]
    firm_a, firm_b = f"opt04-pa-{suffix}", f"opt04-pb-{suffix}"
    owner_a, owner_b = f"opt04-oa-{suffix}", f"opt04-ob-{suffix}"
    company = f"opt04-shared-{suffix}"
    with psycopg.connect(ADMIN_URL) as conn:
        for user in (owner_a, owner_b):
            conn.execute(
                "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
                (user, user, f"{user}@example.test"),
            )
        for firm, owner in ((firm_a, owner_a), (firm_b, owner_b)):
            conn.execute(
                "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
                (firm, firm, owner),
            )
            conn.execute(
                "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
                (firm, owner),
            )
        conn.execute(
            "INSERT INTO accounting.companies(id,name,tax_id) VALUES(%s,%s,'')",
            (company, company),
        )
        conn.execute(
            "INSERT INTO accounting.firm_clients(firm_id,company_id) VALUES(%s,%s),(%s,%s)",
            (firm_a, company, firm_b, company),
        )
        for firm, owner, version, status in (
            (firm_a, owner_a, 1, "superseded"),
            (firm_a, owner_a, 2, "draft"),
            (firm_b, owner_b, 1, "draft"),
        ):
            conn.execute(
                "INSERT INTO accounting.fsc_company_profiles"
                "(id,firm_id,company_id,version,status,industry,payload_json,confirmations_json,"
                "fingerprint,created_by,created_at) VALUES(%s,%s,%s,%s,%s,'taxi','{}'::jsonb,"
                "'{}'::jsonb,%s,%s,now()::text)",
                (
                    f"opt04-profile-{firm}-{version}",
                    firm,
                    company,
                    version,
                    status,
                    f"fp-{firm}-{version}",
                    owner,
                ),
            )
    with psycopg.connect(ADMIN_URL) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO accounting.fsc_company_profiles"
                "(id,firm_id,company_id,version,status,industry,payload_json,confirmations_json,"
                "fingerprint,created_by,created_at) VALUES(%s,%s,%s,1,'draft','taxi','{}'::jsonb,"
                "'{}'::jsonb,'dup',%s,now()::text)",
                (f"opt04-duplicate-{suffix}", firm_b, company, owner_b),
            )

    def versions(firm, user):
        conn = psycopg.connect(APP_URL)
        try:
            conn.execute("BEGIN")
            conn.execute("SET LOCAL ROLE accounting_app")
            conn.execute("SET LOCAL search_path TO accounting,pg_catalog")
            conn.execute(
                "SELECT set_config('app.user_id',%s,true),set_config('app.firm_id',%s,true)",
                (user, firm),
            )
            return conn.execute(
                "SELECT version,status FROM accounting.fsc_company_profiles "
                "WHERE firm_id=%s AND company_id=%s ORDER BY version",
                (firm, company),
            ).fetchall()
        finally:
            conn.rollback()
            conn.close()

    assert versions(firm_a, owner_a) == [(1, "superseded"), (2, "draft")]
    assert versions(firm_b, owner_b) == [(1, "draft")]


def test_company_write_lock_does_not_block_a_different_company(pg):
    from threading import Thread

    from app.accounting.database import Database
    from app.fsc.profiles import REQUIRED, FscProfileService, ProfileWrite

    psycopg = pg
    suffix = uuid4().hex[:10]
    firm_a, firm_b = f"opt04-wa-{suffix}", f"opt04-wb-{suffix}"
    owner_a, owner_b = f"opt04-wao-{suffix}", f"opt04-wbo-{suffix}"
    company_a, company_b = f"opt04-wca-{suffix}", f"opt04-wcb-{suffix}"
    with psycopg.connect(ADMIN_URL) as conn:
        for user in (owner_a, owner_b):
            conn.execute(
                "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
                (user, user, f"{user}@example.test"),
            )
        for firm, owner, company in (
            (firm_a, owner_a, company_a),
            (firm_b, owner_b, company_b),
        ):
            conn.execute(
                "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
                (firm, firm, owner),
            )
            conn.execute(
                "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
                (firm, owner),
            )
            conn.execute(
                "INSERT INTO accounting.companies(id,name,tax_id) VALUES(%s,%s,'')",
                (company, company),
            )
            conn.execute(
                "INSERT INTO accounting.firm_clients(firm_id,company_id) VALUES(%s,%s)",
                (firm, company),
            )

    holder = psycopg.connect(APP_URL)
    try:
        holder.execute("BEGIN")
        holder.execute("SET LOCAL ROLE accounting_app")
        holder.execute("SET LOCAL search_path TO accounting,pg_catalog")
        holder.execute(
            "SELECT set_config('app.user_id',%s,true),set_config('app.firm_id',%s,true)",
            (owner_a, firm_a),
        )
        holder.execute("SELECT accounting.begin_company_write(%s,%s)", (firm_a, company_a))

        from app.accounting.service import Actor

        service = FscProfileService(Database(APP_URL))
        outcome: dict[str, object] = {}

        def write_other_company():
            service.create_draft(
                Actor(user_id=owner_b, firm_id=firm_b),
                company_b,
                ProfileWrite(
                    industry="taxi",
                    payload={
                        key: None
                        for key in __import__("app.fsc.profiles", fromlist=["REQUIRED"]).REQUIRED
                    },
                ),
            )
            outcome["done"] = True

        worker = Thread(target=write_other_company, daemon=True)
        worker.start()
        worker.join(3)
        assert outcome.get("done") is True, "company B write was blocked by company A lock"
    finally:
        holder.rollback()
        holder.close()
