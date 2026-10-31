"""A current human review must never manufacture historical firm authority."""

import sqlite3

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, ExportRequest
from test_business import business  # noqa: F401
from test_review_policy_service import pending


def remove_old_authority(c, kind, identifier):
    # Isolated privileged restore fixture only; production guards remain intact.
    with sqlite3.connect(c.db.path) as restore:
        ddl = restore.execute(
            "SELECT sql FROM sqlite_master WHERE name='authority_no_delete'"
        ).fetchone()[0]
        restore.execute("DROP TRIGGER authority_no_delete")
        restore.execute(
            "DELETE FROM review_authority_snapshots WHERE subject_kind=? AND subject_id=?",
            (kind, identifier),
        )
        restore.execute(ddl)


def legacy(c):
    draft = pending(c)
    approved = c.ledger.approve(
        c.actors["accountant"],
        c.book["id"],
        draft["id"],
        ApprovalRequest(expected_version=draft["version"]),
    )
    remove_old_authority(c, "draft", draft["id"])
    return c.ledger.get_draft(c.manager, c.book["id"], draft["id"])


def prepare(c, draft):
    from app.accounting.legacy_review import LegacyReviewService, RecheckPrepare

    svc = LegacyReviewService(c.db)
    scope = svc.inspect(c.manager, c.book["id"], "draft", draft["id"])
    return svc, svc.prepare(
        c.manager,
        c.book["id"],
        "draft",
        draft["id"],
        RecheckPrepare(
            expected_version=draft["version"],
            content_fingerprint=scope["content_fingerprint"],
            expected_policy_version=scope["policy_version"],
            reason="现在重新核对原始资料并承担制单责任",
        ),
    )


def test_current_recheck_preserves_history_and_unblocks_export(business):
    from app.accounting.legacy_review import RecheckConfirm

    c = business
    draft = legacy(c)
    with c.db.transaction(actor=c.manager) as conn:
        history = [dict(r) for r in conn.execute("SELECT * FROM draft_review_receipts")]
    with pytest.raises(AccountingError):
        c.ledger.export(
            c.manager,
            c.book["id"],
            ExportRequest(draft_ids=[draft["id"]], idempotency_key="blocked-old"),
        )
    svc, request = prepare(c, draft)
    with pytest.raises(AccountingError):
        svc.confirm(c.manager, request["id"], RecheckConfirm(reason="不能自己独立复核"))
    confirmation = svc.confirm(
        c.actors["accountant"], request["id"], RecheckConfirm(reason="今天重新逐项核对原件与分录")
    )
    assert confirmation["proof_kind"] == "current_recheck"
    assert confirmation["independent"] is True
    assert (
        svc.confirm(
            c.actors["accountant"],
            request["id"],
            RecheckConfirm(reason="今天重新逐项核对原件与分录"),
        )
        == confirmation
    )
    c.ledger.export(
        c.manager,
        c.book["id"],
        ExportRequest(draft_ids=[draft["id"]], idempotency_key="rechecked-old"),
    )
    with c.db.transaction(actor=c.manager) as conn:
        assert [dict(r) for r in conn.execute("SELECT * FROM draft_review_receipts")] == history
        assert not conn.execute(
            "SELECT 1 FROM review_authority_snapshots WHERE subject_id=?", (draft["id"],)
        ).fetchone()


def test_recheck_rejects_wrong_scope_stale_hash_and_client(business):
    from app.accounting.legacy_review import LegacyReviewService, RecheckPrepare, RecheckConfirm

    c = business
    draft = legacy(c)
    svc = LegacyReviewService(c.db)
    with pytest.raises(AccountingError):
        svc.inspect(c.manager, c.other_book["id"], "draft", draft["id"])
    with pytest.raises(AccountingError):
        svc.prepare(
            c.manager,
            c.book["id"],
            "draft",
            draft["id"],
            RecheckPrepare(
                expected_version=draft["version"],
                content_fingerprint="0" * 64,
                expected_policy_version=0,
                reason="旧指纹不得采纳",
            ),
        )
    svc, request = prepare(c, draft)
    with pytest.raises(AccountingError):
        svc.confirm(
            c.actors["finance"], request["id"], RecheckConfirm(reason="客户财务不能事务所复核")
        )
    with c.db.transaction(write=True, actor=c.manager) as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(
                "UPDATE legacy_review_preparations SET maker_user_id=? WHERE id=?",
                (c.actors["accountant"].user_id, request["id"]),
            )


def test_restore_inventory_requires_permission_and_review_data():
    from p01_recovery_business import require_latest_business_inventory, BUSINESS_TABLES

    inventory = {"tables": {t: ["populated"] for t in BUSINESS_TABLES}}
    inventory["tables"].pop("staff_access_assignments", None)
    with pytest.raises(AssertionError, match="staff_access_assignments"):
        require_latest_business_inventory(inventory)


@pytest.mark.parametrize("kind", ["manual", "periodic"])
def test_manual_and_periodic_current_recheck_posts_without_rewriting_old_review(business, kind):
    from test_review_policy_service import manual
    from app.accounting.legacy_review import LegacyReviewService, RecheckPrepare, RecheckConfirm
    from app.accounting.review_policy_models import ManualReviewApproval, ManualReviewPost
    from app.posting.manual_review import ManualReviewService
    from app.periodic.service import PeriodicService
    from app.periodic.models import AccrualRule, DraftCommand, DraftPost

    c = business
    _, payload = manual(c)
    if kind == "manual":
        flow = ManualReviewService(c.db)
        row = flow.prepare(c.manager, payload)["request"]
        row = flow.approve(
            c.actors["accountant"],
            row["id"],
            ManualReviewApproval(
                expected_version=row["version"],
                expected_policy_version=0,
                reason="原始人工复核记录保留",
            ),
        )["request"]
    else:
        flow = PeriodicService(c.db)
        rule = flow.create_accrual_rule(
            c.manager,
            AccrualRule(
                book_id=c.book["id"],
                name="本地合成计提",
                amount="0.30",
                debit_account_id=payload.lines[0].account_id,
                debit_account_version=1,
                credit_account_id=payload.lines[1].account_id,
                credit_account_version=1,
                reason="本地期末恢复夹具",
            ),
        )
        row = flow.generate_accrual(c.manager, rule["id"])["draft"]
        row = flow.approve(
            c.actors["accountant"],
            row["id"],
            DraftCommand(expected_version=row["version"], reason="原始期末复核记录"),
        )["draft"]
    with c.db.transaction(actor=c.manager) as conn:
        original = dict(
            conn.execute(
                "SELECT * FROM "
                + ("manual_review_requests" if kind == "manual" else "periodic_drafts")
                + " WHERE id=?",
                (row["id"],),
            ).fetchone()
        )
    remove_old_authority(c, kind, row["id"])
    svc = LegacyReviewService(c.db)
    source = svc.inspect(c.manager, c.book["id"], kind, row["id"])
    request = svc.prepare(
        c.manager,
        c.book["id"],
        kind,
        row["id"],
        RecheckPrepare(
            expected_version=source["source_version"],
            content_fingerprint=source["content_fingerprint"],
            expected_policy_version=0,
            reason="现在承担旧稿制单责任",
        ),
    )
    svc.confirm(
        c.actors["accountant"], request["id"], RecheckConfirm(reason="现在核对该版本，不是历史补签")
    )
    if kind == "manual":
        result = flow.post(
            c.manager,
            row["id"],
            ManualReviewPost(expected_version=row["version"], command_key=payload.command_key),
        )
        assert result["voucher"]["review_mode"] == "independent"
    else:
        result = flow.post(
            c.manager,
            row["id"],
            DraftPost(
                expected_version=row["version"],
                command_key="legacy-periodic-post",
                voucher_date="2026-10-31",
                reason="当前复核后正式记账",
            ),
        )
        assert result["draft"]["status"] == "posted"
    with c.db.transaction(actor=c.manager) as conn:
        table = "manual_review_requests" if kind == "manual" else "periodic_drafts"
        current = dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (row["id"],)).fetchone())
        assert (
            current["reviewed_at" if kind == "manual" else "approved_at"]
            == original["reviewed_at" if kind == "manual" else "approved_at"]
        )


def test_policy_change_requires_fresh_current_preparation(business):
    from app.accounting.legacy_review import RecheckPrepare, RecheckConfirm
    from app.accounting.review_policy import ReviewPolicyService
    from app.accounting.review_policy_models import ReviewPolicyChange

    c = business
    draft = legacy(c)
    svc, request = prepare(c, draft)
    ReviewPolicyService(c.db).configure(
        c.manager,
        c.book["id"],
        ReviewPolicyChange(
            expected_version=0, mode="single_operator", reason="明确单人办理，不属于独立复核"
        ),
    )
    with pytest.raises(AccountingError):
        svc.confirm(
            c.actors["accountant"], request["id"], RecheckConfirm(reason="旧政策申请不能确认")
        )
    current = svc.inspect(c.manager, c.book["id"], "draft", draft["id"])
    new = svc.prepare(
        c.manager,
        c.book["id"],
        "draft",
        draft["id"],
        RecheckPrepare(
            expected_version=current["source_version"],
            content_fingerprint=current["content_fingerprint"],
            expected_policy_version=1,
            reason="按当前单人政策重新认领",
        ),
    )
    result = svc.confirm(c.manager, new["id"], RecheckConfirm(reason="确认单人办理，非独立复核"))
    assert result["independent"] is False


def test_exported_legacy_recheck_preserves_frozen_export_and_posts_once(business):
    from test_review_policy_service import manual
    from app.accounting.legacy_review import LegacyReviewService, RecheckPrepare, RecheckConfirm
    from app.posting.models import PostDraft

    c = business
    posting, _ = manual(c)
    d = pending(c)
    c.ledger.approve(
        c.actors["accountant"],
        c.book["id"],
        d["id"],
        ApprovalRequest(expected_version=d["version"]),
    )
    exported = c.ledger.export(
        c.manager,
        c.book["id"],
        ExportRequest(draft_ids=[d["id"]], idempotency_key="original-frozen-export"),
    )
    remove_old_authority(c, "draft", d["id"])
    svc = LegacyReviewService(c.db)
    source = svc.inspect(c.manager, c.book["id"], "draft", d["id"])
    request = svc.prepare(
        c.manager,
        c.book["id"],
        "draft",
        d["id"],
        RecheckPrepare(
            expected_version=source["source_version"],
            content_fingerprint=source["content_fingerprint"],
            expected_policy_version=0,
            reason="当前承担旧稿制单责任，不改已导出文件",
        ),
    )
    svc.confirm(
        c.actors["accountant"],
        request["id"],
        RecheckConfirm(reason="今天核对该版本，仅用于后续正式记账"),
    )
    replay = c.ledger.export(
        c.manager,
        c.book["id"],
        ExportRequest(draft_ids=[d["id"]], idempotency_key="original-frozen-export"),
    )
    assert replay == exported
    command = PostDraft(
        command_key="current-rechecked-post",
        expected_version=source["source_version"],
        voucher_date="2026-10-15",
    )
    posted = posting.post_draft(c.manager, c.book["id"], d["id"], command)
    again = posting.post_draft(c.manager, c.book["id"], d["id"], command)
    assert again["voucher"] == posted["voucher"] and again["replayed"] is True
    assert svc.inspect(c.manager, c.book["id"], "draft", d["id"])["can_prepare"] is False


def test_recovery_release_cli_imports_without_repository_pythonpath(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[2] / "scripts/release_recovery_hold.py"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "--allow-local-drill" in result.stdout
