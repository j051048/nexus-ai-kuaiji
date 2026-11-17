"""OPT04-D: an original voucher can be bound to a logical source only once."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from test_review_policy_postgres import (  # noqa: F401
    _prepared_manual,
    _review_manual,
    business_case,
    business_postgres,
)

pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="仅通过本地 PostgreSQL 隔离运行器执行",
)


def test_pg_same_logical_source_cannot_create_two_original_vouchers(business_case):
    from app.accounting.review_policy_models import ManualReviewPost

    c = business_case
    service, request, prepared = _prepared_manual(c)
    reviewed = _review_manual(c, service, prepared)
    posted = service.post(
        c.owner,
        prepared["id"],
        ManualReviewPost(expected_version=reviewed["version"], command_key=request.command_key),
    )
    voucher_id = posted["voucher"]["id"]
    logical_source = f"opt04-draft-{uuid4().hex}"
    with c.db.transaction(write=True, actor=c.owner) as conn:
        conn.execute(
            "INSERT INTO accounting.voucher_sources(id,voucher_id,source_kind,source_id,source_version,amount_cents,evidence_ref) VALUES(?,?,?,?,?,?,?)",
            (
                f"opt04-vs-one-{uuid4().hex[:10]}",
                voucher_id,
                "draft",
                logical_source,
                1,
                100,
                "draft-1",
            ),
        )
    with pytest.raises(c.psycopg.errors.UniqueViolation):
        with c.db.transaction(write=True, actor=c.owner) as conn:
            conn.execute(
                "INSERT INTO accounting.voucher_sources(id,voucher_id,source_kind,source_id,source_version,amount_cents,evidence_ref) VALUES(?,?,?,?,?,?,?)",
                (
                    f"opt04-vs-two-{uuid4().hex[:10]}",
                    voucher_id,
                    "draft",
                    logical_source,
                    2,
                    100,
                    "draft-2",
                ),
            )


def test_pg_concurrent_original_sources_only_one_row_wins(business_case):
    from app.accounting.review_policy_models import ManualReviewPost

    c = business_case
    service, request, prepared = _prepared_manual(c)
    reviewed = _review_manual(c, service, prepared)
    posted = service.post(
        c.owner,
        prepared["id"],
        ManualReviewPost(expected_version=reviewed["version"], command_key=request.command_key),
    )
    voucher_id = posted["voucher"]["id"]
    logical_source = f"opt04-concurrent-{uuid4().hex}"

    def insert(row_id: str):
        try:
            with c.db.transaction(write=True, actor=c.owner) as conn:
                conn.execute(
                    "INSERT INTO accounting.voucher_sources(id,voucher_id,source_kind,source_id,source_version,amount_cents,evidence_ref) VALUES(?,?,?,?,?,?,?)",
                    (row_id, voucher_id, "draft", logical_source, 1, 100, row_id),
                )
            return "ok"
        except c.psycopg.errors.UniqueViolation:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(
            pool.map(
                insert,
                [f"opt04-concurrent-a-{uuid4().hex[:8]}", f"opt04-concurrent-b-{uuid4().hex[:8]}"],
            )
        )
    assert outcomes == ["conflict", "ok"]
    with c.db.transaction(actor=c.owner) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM accounting.voucher_sources WHERE source_kind='draft' AND source_id=?",
                (logical_source,),
            ).fetchone()[0]
            == 1
        )
