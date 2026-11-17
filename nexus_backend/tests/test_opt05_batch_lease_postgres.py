"""OPT05-B: lease renewal is token-bound and only extends live work."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from test_review_policy_postgres import business_case, business_postgres  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="仅通过本地 PostgreSQL 隔离运行器执行",
)

STAMP = "2026-10-01T00:00:00.000000+00:00"
FUTURE = "2099-01-01T00:00:00.000000+00:00"
PAST = "2000-01-01T00:00:00.000000+00:00"


def _item(case, status="running", token=None, lease_until=FUTURE):
    parent = "opt05-parent-" + uuid4().hex
    item = "opt05-item-" + uuid4().hex
    with case.db.transaction(write=True, actor=case.owner) as conn:
        conn.execute(
            "INSERT INTO batch_operations(id,firm_id,kind,status,idempotency_key,"
            "request_json,total,created_by,created_at,updated_at) "
            "VALUES(?,?,'monthly_open','running',?,'{}',0,?,?,?)",
            (parent, case.owner.firm_id, uuid4().hex, case.owner.user_id, STAMP, STAMP),
        )
        conn.execute(
            "INSERT INTO batch_operation_items(id,batch_id,firm_id,company_id,"
            "sequence_no,status,available_at,lease_token,lease_until,created_at,updated_at) "
            "VALUES(?,?,?,?,1,?,?,?,?,?,?)",
            (
                item,
                parent,
                case.owner.firm_id,
                case.company,
                status,
                STAMP,
                token,
                lease_until,
                STAMP,
                STAMP,
            ),
        )
    return item


def _renew(case, item, token, seconds=120):
    import psycopg

    with psycopg.connect(os.environ["ACCOUNTING_TEST_POSTGRES_URL"]) as conn:
        result = conn.execute(
            "SELECT accounting.worker_renew_batch_item(%s,%s,%s)",
            (item, token, seconds),
        ).fetchone()[0]
        conn.commit()
    return bool(result)


def test_pg_lease_renewal_requires_live_matching_token(business_case):
    c = business_case
    token = uuid4().hex
    initial = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    item = _item(c, token=token, lease_until=initial)
    assert _renew(c, item, token) is True
    assert _renew(c, item, uuid4().hex) is False
    with c.db.transaction(write=True, actor=c.owner) as conn:
        row = conn.execute(
            "SELECT lease_token,lease_until FROM batch_operation_items WHERE id=?",
            (item,),
        ).fetchone()
    assert row["lease_token"] == token
    assert row["lease_until"] > initial


def test_pg_lease_renewal_rejects_expired_or_finished_work(business_case):
    c = business_case
    expired_token = uuid4().hex
    expired = _item(c, token=expired_token, lease_until=PAST)
    assert _renew(c, expired, expired_token) is False
    token = uuid4().hex
    finished = _item(c, status="succeeded", token=token)
    assert _renew(c, finished, token) is False


def test_pg_worker_role_can_execute_renewal_function(case=None):
    import psycopg

    with psycopg.connect(os.environ["ACCOUNTING_TEST_POSTGRES_URL"]) as conn:
        granted = conn.execute(
            "SELECT has_schema_privilege('accounting_worker','accounting','USAGE'),"
            "has_function_privilege('accounting_worker',"
            "'accounting.worker_renew_batch_item(text,text,integer)','EXECUTE')"
        ).fetchone()
    assert granted == (True, True)
