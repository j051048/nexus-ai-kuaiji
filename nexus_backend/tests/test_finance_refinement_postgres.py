"""PG16/17 real roles, native writes and competing asset confirmations."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from app.accounting.domain import AccountingError
from test_business_postgres import ADMIN_URL, APP_URL
from test_intelligence_workflow_postgres import pg_ai, business_case, business_postgres  # noqa: F401
from test_finance_agent_reuse_postgres import finance  # noqa: F401
import test_finance_refinement as flows

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需显式本地隔离 PG 配置")


@pytest.mark.parametrize(
    "case",
    [
        flows.test_reconciliation_preview_and_draft_preserve_history,
        flows.test_reconciliation_wrong_scope_and_revoked_actor,
        flows.test_account_revision_replaces_unposted_draft_without_editing_it,
        flows.test_reconciliation_native_forged_source_is_rejected,
        flows.test_reconciliation_source_is_immutable,
        flows.test_workspace_uses_corrected_net_not_original_gross,
        flows.test_closed_period_rejects_native_review,
    ],
    ids=[
        "history",
        "scope",
        "revision_resume",
        "native_source",
        "immutable",
        "net",
        "closed_review",
    ],
)
def test_reconciliation(finance, case):
    case(finance)


def test_reconciliation_competition_is_one_draft(finance):
    c = finance
    service, asset = flows.asset_case(c, start="2026-10")
    view = service.asset_reconciliation(c.manager, asset["id"], c.book["id"])
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(lambda _: flows.reconcile(c, service, asset, c.book["id"], view), range(2))
        )
    assert len({r["draft"]["id"] for r in results}) == 1


def test_reconciliation_closed_period_rejects_native_run(finance):
    from period_fixtures import restore_period_state

    c = finance
    service, asset = flows.asset_case(c, start="2026-10")
    view = service.asset_reconciliation(c.manager, asset["id"], c.book["id"])
    assert view["can_generate"]
    with c.db.transaction(actor=c.manager) as conn:
        basis = conn.execute(
            "SELECT source_json FROM asset_reconciliation_sources WHERE asset_id=? AND book_id=?",
            (asset["id"], c.book["id"]),
        ).fetchone()[0]
    restore_period_state(c.db, c.book["id"])
    with pytest.raises(AccountingError):
        flows.reconcile(c, service, asset, c.book["id"], view)
    with pytest.raises(Exception) as denied:
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "INSERT INTO periodic_runs(id,book_id,period,kind,run_key,asset_id,input_fingerprint,status,created_by,created_at,reconciliation_json) VALUES(?,?,?,'fixed',?,?,?,'generated',?,?,?)",
                (
                    uuid4().hex,
                    c.book["id"],
                    "2026-10",
                    uuid4().hex,
                    asset["id"],
                    view["fingerprint"],
                    c.manager.user_id,
                    "2026-11-09",
                    basis,
                ),
            )
    assert getattr(denied.value, "sqlstate", None) in {"23514", "42501"}


def test_reconciliation_revoked_reviewer_and_native_line_tamper(finance):
    c = finance
    service, asset = flows.asset_case(c, start="2026-10")
    view = service.asset_reconciliation(c.manager, asset["id"], c.book["id"])
    result = flows.reconcile(c, service, asset, c.book["id"], view)
    with pytest.raises(Exception) as immutable:
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "UPDATE periodic_draft_lines SET debit_cents=debit_cents+1 WHERE draft_id=? AND line_no=1",
                (result["draft"]["id"],),
            )
    assert getattr(immutable.value, "sqlstate", None) == "42501"
    reviewer = c.actors["accountant"]
    # A new extra balanced pair must also fail at the authoritative review edge.
    # No grant is widened and no superuser is used to alter protected lines.
    with c.db.transaction(write=True, actor=c.manager) as conn:
        for line_no, account, debit, credit in [
            (3, asset["expense_account_id"], 1, 0),
            (4, asset["accum_account_id"], 0, 1),
        ]:
            conn.execute(
                "INSERT INTO periodic_draft_lines(id,draft_id,line_no,account_id,account_version,debit_cents,credit_cents,aux_json) VALUES(?,?,?,?,1,?,?,'{}')",
                (uuid4().hex, result["draft"]["id"], line_no, account, debit, credit),
            )
    with pytest.raises(Exception) as denied:
        with c.db.transaction(write=True, actor=reviewer) as conn:
            conn.execute(
                "UPDATE periodic_drafts SET status='approved',version=version+1,approved_by=?,approved_at='2026-11-09' WHERE id=?",
                (reviewer.user_id, result["draft"]["id"]),
            )
    assert getattr(denied.value, "sqlstate", None) in {"23514", "42501"}
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
            (c.book["id"], reviewer.user_id),
        )
    with pytest.raises(AccountingError):
        service.asset_reconciliation(reviewer, asset["id"], c.book["id"])
