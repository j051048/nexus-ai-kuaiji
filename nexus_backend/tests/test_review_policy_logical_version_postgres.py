"""真实 PG 逻辑版本与逐叶责任历史；独立于冻结的 28 项复核门槛。"""

import os

import pytest

from app.accounting.models import DraftUpdate
from test_business_postgres import business_case, business_postgres  # noqa: F401
from test_review_policy_postgres import _draft


pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="仅通过本地 PostgreSQL 隔离运行器执行",
)


def test_pg_logical_version_counts_commands_but_history_counts_each_fact_change(business_case):
    c = business_case
    initial = _draft(c)
    assert initial["version"] == 1
    edited = c.service.update_draft(
        c.finance,
        c.book,
        initial["id"],
        DraftUpdate(
            expected_version=initial["version"],
            description="完整人工编辑只消耗一个逻辑版本",
            lines=initial["lines"],
        ),
    )
    assert edited["version"] == initial["version"] + 1
    with c.db.transaction(actor=c.owner) as conn:
        before = dict(
            conn.execute(
                "SELECT * FROM draft_preparations WHERE draft_id=?", (initial["id"],)
            ).fetchone()
        )
        assert (
            conn.execute(
                "SELECT last_manual_edit_version FROM drafts WHERE id=?", (initial["id"],)
            ).fetchone()[0]
            == edited["version"]
        )
    for previous in (edited["version"], edited["version"] + 1):
        with c.db.transaction(write=True, actor=c.owner) as conn:
            # Two changed leaves in one independent transaction: one logical
            # version, two provenance revisions. No application marker supplied.
            conn.execute(
                "UPDATE draft_lines SET debit_cents=debit_cents+1 "
                "WHERE draft_id=? AND debit_cents>0",
                (initial["id"],),
            )
            conn.execute(
                "UPDATE draft_lines SET credit_cents=credit_cents+1 "
                "WHERE draft_id=? AND credit_cents>0",
                (initial["id"],),
            )
        assert c.service.get_draft(c.owner, c.book, initial["id"])["version"] == previous + 1
    with c.db.transaction(actor=c.owner) as conn:
        after = dict(
            conn.execute(
                "SELECT * FROM draft_preparations WHERE draft_id=?", (initial["id"],)
            ).fetchone()
        )
        assert after["content_revision"] >= before["content_revision"] + 4
        assert after["maker_user_id"] == c.owner.user_id
        assert after["version"] == edited["version"] + 2
        assert after["version_transaction"] != before["version_transaction"]


def test_pg_noop_header_cannot_consume_next_native_fact_version(business_case):
    c = business_case
    initial = _draft(c)
    with c.db.transaction(write=True, actor=c.finance) as conn:
        conn.execute("UPDATE drafts SET description=description WHERE id=?", (initial["id"],))
        conn.execute(
            "UPDATE draft_lines SET account_code='6602.99' WHERE draft_id=? AND debit_cents>0",
            (initial["id"],),
        )
    current = c.service.get_draft(c.owner, c.book, initial["id"])
    assert current["version"] == initial["version"] + 1
    assert current["review_responsibility"]["maker_user_id"] == c.finance.user_id
