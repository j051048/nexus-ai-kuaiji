"""Human asset reconciliation reuses periodic review; no rewritten history."""

from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.periodic.models import DraftCommand, DraftPost
from app.posting.models import ReverseVoucher
from review_policy_fixtures import reverse_reviewed
from test_business import business  # noqa: F401
from test_finance_agent_reuse import asset_case
from test_finance_continuation import next_period
from test_posting_service import admin


def post_asset(c, service, draft):
    approved = service.approve(
        admin(c),
        draft["draft"]["id"],
        DraftCommand(expected_version=draft["draft"]["version"], reason="独立核对资产及差异"),
    )
    return service.post(
        c.manager,
        draft["draft"]["id"],
        DraftPost(
            expected_version=approved["draft"]["version"],
            command_key=uuid4().hex,
            voucher_date=draft["draft"]["period"] + "-28",
            reason="复核后正式记账",
        ),
    )


def corrected_asset(c):
    service, asset = asset_case(c, start="2026-10")
    posted = post_asset(c, service, service.generate_asset(c.manager, asset["id"]))
    reverse_reviewed(
        service.posting,
        c.manager,
        posted["draft"]["posted_voucher_id"],
        ReverseVoucher(command_key=uuid4().hex, voucher_date="2026-10-29", reason="纠正重复计提"),
    )
    return service, asset, posted


def reconcile(c, service, asset, target, preview):
    from app.periodic.models import AssetReconcile

    return service.reconcile_asset(
        c.manager,
        asset["id"],
        AssetReconcile(
            book_id=target,
            expected_fingerprint=preview["fingerprint"],
            reason="对照原计提及冲销核对差额",
        ),
    )


def periodic_close_check(c):
    from app.b04_close.models import ClosePreflight
    from app.b04_close.service import CloseService
    from app.reports.models import ReportCommand
    from app.reports.service import ReportService

    reports = ReportService(c.db)
    report = reports.create(c.manager, c.book["id"], "2026-10")
    report = reports.validate(c.manager, report["report"]["id"])
    reports.review(
        admin(c),
        report["report"]["id"],
        ReportCommand(expected_version=report["report"]["version"], reason="独立核对报表"),
    )
    result = CloseService(c.db).preflight(
        c.manager,
        ClosePreflight(
            book_id=c.book["id"],
            period="2026-10",
            report_id=report["report"]["id"],
            idempotency_key=uuid4().hex,
            reason="资产核对后重新检查月结",
        ),
    )
    return next(row for row in result["checks"] if row["code"] == "unposted_periodic")


def test_reconciliation_preview_and_draft_preserve_history(business):
    c = business
    service, asset, posted = corrected_asset(c)
    target = next_period(c, "2026-11")
    preview = service.asset_reconciliation(c.manager, asset["id"], target)
    assert preview["scope"]["book_id"] == target
    assert preview["prior_posted_cents"] == 3333
    assert preview["net_posted_cents"] == 0
    assert preview["amount_cents"] == 6666
    assert len(preview["history"]) == 2
    result = reconcile(c, service, asset, target, preview)
    assert sum(row["debit_cents"] for row in result["lines"]) == 6666
    assert result["draft"]["status"] == "draft"
    assert reconcile(c, service, asset, target, preview)["draft"]["id"] == result["draft"]["id"]
    with pytest.raises(AccountingError):
        service.approve(
            c.manager, result["draft"]["id"], DraftCommand(expected_version=1, reason="不能自审")
        )
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM vouchers WHERE book_id=?", (target,)).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT status FROM periodic_drafts WHERE id=?", (posted["draft"]["id"],)
            ).fetchone()[0]
            == "posted"
        )


def test_reconciliation_wrong_scope_and_revoked_actor(business):
    c = business
    service, asset = asset_case(c)
    for actor, target in [(c.manager, c.other_book["id"]), (c.actors["employee"], c.book["id"])]:
        with pytest.raises(AccountingError):
            service.asset_reconciliation(actor, asset["id"], target)


def test_account_revision_replaces_unposted_draft_without_editing_it(business):
    from app.opening.models import AccountRevision
    from app.opening.service import OpeningService

    c = business
    service, asset = asset_case(c, start="2026-10")
    old = service.generate_asset(c.manager, asset["id"])
    before = service.asset_reconciliation(c.manager, asset["id"], c.book["id"])
    with c.db.transaction(actor=c.manager) as conn:
        ledger = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (c.book["id"],)
        ).fetchone()[0]
        original = dict(
            conn.execute(
                "SELECT * FROM ledger_account_versions WHERE account_id=? AND version=1",
                (asset["expense_account_id"],),
            ).fetchone()
        )
    OpeningService(c.db).revise_account(
        c.manager,
        asset["expense_account_id"],
        AccountRevision(
            ledger_id=ledger,
            expected_version=1,
            code=original["code"],
            name="费用名称更正",
            category=original["category"],
            normal_balance=original["normal_balance"],
            effective_from="2026-01",
            reason="核对后更正名称",
        ),
    )
    with pytest.raises(AccountingError) as stale:
        reconcile(c, service, asset, c.book["id"], before)
    assert stale.value.status == 409
    current = service.asset_reconciliation(c.manager, asset["id"], c.book["id"])
    replacement = reconcile(c, service, asset, c.book["id"], current)
    assert replacement["draft"]["id"] != old["draft"]["id"]
    assert replacement["lines"][0]["account_version"] == 2
    pending = periodic_close_check(c)
    assert pending["status"] == "blocked"
    assert pending["detail"] == "1 张期末草稿未记账"
    with pytest.raises(AccountingError):
        service.approve(
            admin(c), old["draft"]["id"], DraftCommand(expected_version=1, reason="旧稿不得复活")
        )
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT account_version FROM periodic_draft_lines WHERE draft_id=? ORDER BY line_no",
                (old["draft"]["id"],),
            ).fetchone()[0]
            == 1
        )
    posted = post_asset(c, service, replacement)
    assert posted["draft"]["status"] == "posted"
    assert periodic_close_check(c)["status"] == "pass"
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT expense_account_version FROM periodic_assets WHERE id=?", (asset["id"],)
            ).fetchone()[0]
            == 1
        )
    target = next_period(c, "2026-11")
    resumed = service.asset_reconciliation(c.manager, asset["id"], target)
    assert resumed["net_posted_cents"] == 3333
    assert resumed["amount_cents"] == 3333
    assert reconcile(c, service, asset, target, resumed)["draft"]["status"] == "draft"


def test_reconciliation_native_forged_source_is_rejected(business):
    c = business
    service, asset, _ = corrected_asset(c)
    target = next_period(c, "2026-11")
    # Prove the column and preview work; a missing migration is not a negative success.
    assert service.asset_reconciliation(c.manager, asset["id"], target)["can_generate"]
    with pytest.raises(Exception, match="[Rr]econciliation"):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "INSERT INTO periodic_runs(id,book_id,period,kind,run_key,asset_id,input_fingerprint,status,created_by,created_at,reconciliation_json) VALUES(?,?,?,'fixed',?,?,?,'generated',?,?,?)",
                (
                    uuid4().hex,
                    target,
                    "2026-11",
                    uuid4().hex,
                    asset["id"],
                    "0" * 64,
                    c.manager.user_id,
                    "2026-11-09",
                    '{"forged":true}',
                ),
            )


def test_reconciliation_lines_cannot_change_the_reviewed_difference(business):
    c = business
    service, asset = asset_case(c, start="2026-10")
    preview = service.asset_reconciliation(c.manager, asset["id"], c.book["id"])
    draft = reconcile(c, service, asset, c.book["id"], preview)
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute(
            "UPDATE periodic_draft_lines SET debit_cents=debit_cents+1 WHERE draft_id=? AND line_no=1",
            (draft["draft"]["id"],),
        )
    current = service.list_drafts(c.manager, c.book["id"])[0]
    with pytest.raises(AccountingError, match="差额"):
        service.approve(
            admin(c),
            current["id"],
            DraftCommand(expected_version=current["version"], reason="不得核准篡改金额"),
        )


def test_reconciliation_source_is_immutable(business):
    c = business
    service, asset = asset_case(c, start="2026-10")
    draft = reconcile(
        c,
        service,
        asset,
        c.book["id"],
        service.asset_reconciliation(c.manager, asset["id"], c.book["id"]),
    )
    with pytest.raises(Exception, match="[Rr]econciliation"):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "UPDATE periodic_runs SET reconciliation_json=NULL WHERE id=?",
                (draft["draft"]["run_id"],),
            )


def test_workspace_uses_corrected_net_not_original_gross(business):
    from finance_opening_fixture import bind_next_opening

    c = business
    service, asset, _ = corrected_asset(c)
    assert service.workspace(c.manager, c.book["id"])["assets"][0]["posted_cents"] == 0
    target = next_period(c, "2026-11")
    bind_next_opening(c.db, c.manager, admin(c), c.book["id"], target, "2026-11")
    view = service.asset_reconciliation(c.manager, asset["id"], target)
    post_asset(c, service, reconcile(c, service, asset, target, view))
    assert service.workspace(c.manager, target)["assets"][0]["posted_cents"] == 6666


def test_native_review_rejects_balanced_extra_lines(business):
    c = business
    service, asset = asset_case(c, start="2026-10")
    result = reconcile(
        c,
        service,
        asset,
        c.book["id"],
        service.asset_reconciliation(c.manager, asset["id"], c.book["id"]),
    )
    with c.db.transaction(write=True, actor=c.manager) as conn:
        for n, account, debit, credit in [
            (3, asset["expense_account_id"], 1, 0),
            (4, asset["accum_account_id"], 0, 1),
        ]:
            conn.execute(
                "INSERT INTO periodic_draft_lines(id,draft_id,line_no,account_id,account_version,debit_cents,credit_cents) VALUES(?,?,?,?,1,?,?)",
                (uuid4().hex, result["draft"]["id"], n, account, debit, credit),
            )
    reviewer = admin(c)
    with pytest.raises(Exception, match="Reconciliation lines"):
        with c.db.transaction(write=True, actor=reviewer) as conn:
            conn.execute(
                "UPDATE periodic_drafts SET status='approved',version=version+1,approved_by=?,approved_at='2026-11-09' WHERE id=?",
                (reviewer.user_id, result["draft"]["id"]),
            )


def test_closed_period_rejects_native_review(business):
    from period_fixtures import restore_period_state

    c = business
    service, asset = asset_case(c, start="2026-10")
    result = reconcile(
        c,
        service,
        asset,
        c.book["id"],
        service.asset_reconciliation(c.manager, asset["id"], c.book["id"]),
    )
    restore_period_state(c.db, c.book["id"])
    reviewer = admin(c)
    with pytest.raises(Exception, match="Reconciliation requires an open"):
        with c.db.transaction(write=True, actor=reviewer) as conn:
            conn.execute(
                "UPDATE periodic_drafts SET status='approved',version=version+1,approved_by=?,approved_at='2026-11-09' WHERE id=?",
                (reviewer.user_id, result["draft"]["id"]),
            )
