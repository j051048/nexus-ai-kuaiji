"""Q05: 导出不得给出未审计、内容与 hash 不一致或失权后的文件。

这些用例在旧实现上应当是红的：旧实现把「取数 → 序列化 → 写审计」整段放在同一个写事务里，
没有「序列化窗口外数据发生变化」的检测点，也没有可注入的序列化接缝。
新实现把序列化移到事务外，并在提交阶段重检授权与来源摘要，因此才可能出现下列 409/403 结果。
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.accounting.domain import AccountingError
from app.posting import presentation
from app.posting.presentation import export_formal_book, export_range_trial_balance
from period_fixtures import restore_period_state
from test_business import business
from test_posting_service import frozen_opening, ledger_of
from test_q03_continuous_books import bound, post

__all__ = ["business"]


def _voucher_count(case, book_id):
    with case.db.transaction() as conn:
        return conn.execute("SELECT COUNT(*) FROM vouchers WHERE book_id=?", (book_id,)).fetchone()[
            0
        ]


def _export_audits(case, action="formal_book.export"):
    with case.db.transaction() as conn:
        return [
            row["details"]
            for row in conn.execute(
                "SELECT details FROM audit_events WHERE action=? ORDER BY created_at", (action,)
            )
        ]


def test_data_change_between_snapshot_and_commit_aborts_export(business, monkeypatch):
    case = business
    posting, _opening = bound(case)
    post(case, posting, case.book["id"], "q05-change-001", "2026-10-05", "10.00", "10.00")
    before = _voucher_count(case, case.book["id"])
    original = presentation._render_csv

    def render_then_change(*args, **kwargs):
        content = original(*args, **kwargs)
        post(case, posting, case.book["id"], "q05-change-002", "2026-10-06", "20.00", "20.00")
        return content

    monkeypatch.setattr(presentation, "_render_csv", render_then_change)
    with pytest.raises(AccountingError) as error:
        export_formal_book(posting, case.manager, case.book["id"], "trial_balance")
    assert error.value.status == 409
    assert "变化" in error.value.detail
    assert _voucher_count(case, case.book["id"]) == before + 1
    assert _export_audits(case) == [], "已失败的导出不得留下审计或文件"


def test_revocation_between_snapshot_and_commit_aborts_export(business, monkeypatch):
    case = business
    posting, _opening = bound(case)
    post(case, posting, case.book["id"], "q05-revoke-001", "2026-10-05", "10.00", "10.00")
    accountant = case.actors["accountant"]
    # 先证明该账号本来可以导出，再证明撤权之后不行。
    assert export_formal_book(posting, accountant, case.book["id"], "trial_balance")
    original = presentation._render_csv

    def render_then_revoke(*args, **kwargs):
        content = original(*args, **kwargs)
        case.directory.revoke_book_member(case.manager, case.book["id"], accountant.user_id)
        return content

    monkeypatch.setattr(presentation, "_render_csv", render_then_revoke)
    with pytest.raises(AccountingError) as error:
        export_formal_book(posting, accountant, case.book["id"], "trial_balance")
    assert error.value.status in {403, 404}


def test_period_close_between_snapshot_and_commit_aborts_export(business, monkeypatch):
    case = business
    posting, _opening = bound(case)
    post(case, posting, case.book["id"], "q05-close-001", "2026-10-05", "10.00", "10.00")
    original = presentation._render_csv

    def render_then_close(*args, **kwargs):
        content = original(*args, **kwargs)
        restore_period_state(case.db, case.book["id"], "closed")
        return content

    monkeypatch.setattr(presentation, "_render_csv", render_then_close)
    with pytest.raises(AccountingError) as error:
        export_formal_book(posting, case.manager, case.book["id"], "trial_balance")
    assert error.value.status == 409


def test_oversize_is_rejected_before_serializing(business, monkeypatch):
    case = business
    posting, _opening = bound(case)

    def huge(conn, actor, book_id, as_of=None):
        row = {
            "code": "1002",
            "name": "银行存款",
            "opening_cents": 0,
            "debit_cents": 0,
            "credit_cents": 0,
            "closing_cents": 0,
        }
        return {"rows": [dict(row) for _ in range(50_001)], "totals": {}}

    monkeypatch.setattr(posting, "trial_balance_in_transaction", huge)
    monkeypatch.setattr(
        presentation, "_render_csv", lambda *a, **k: pytest.fail("超限后不应再序列化")
    )
    with pytest.raises(AccountingError) as error:
        export_formal_book(posting, case.manager, case.book["id"], "trial_balance")
    assert error.value.status == 413


def test_audit_failure_returns_no_file(business, monkeypatch):
    case = business
    posting, _opening = bound(case)
    post(case, posting, case.book["id"], "q05-audit-001", "2026-10-05", "10.00", "10.00")

    def boom(*args, **kwargs):
        raise RuntimeError("审计写入失败")

    monkeypatch.setattr(posting, "_audit", boom)
    with pytest.raises(RuntimeError):
        export_formal_book(posting, case.manager, case.book["id"], "trial_balance")
    assert _export_audits(case) == []


def test_audit_hash_matches_returned_bytes(business):
    case = business
    posting, _opening = bound(case)
    post(case, posting, case.book["id"], "q05-hash-001", "2026-10-05", "10.00", "10.00")
    content = export_formal_book(posting, case.manager, case.book["id"], "trial_balance")
    (audit,) = _export_audits(case)
    import hashlib
    import json

    details = json.loads(audit) if isinstance(audit, str) else audit
    assert details["sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_concurrent_posting_during_export_is_detected_or_consistent(business):
    case = business
    posting, _opening = bound(case)
    post(case, posting, case.book["id"], "q05-race-001", "2026-10-05", "10.00", "10.00")

    def writer():
        return post(case, posting, case.book["id"], "q05-race-002", "2026-10-07", "30.00", "30.00")

    with ThreadPoolExecutor(max_workers=2) as pool:
        exporting = pool.submit(
            export_formal_book, posting, case.manager, case.book["id"], "trial_balance"
        )
        write = pool.submit(writer)
        write.result()
        try:
            content = exporting.result()
        except AccountingError as error:
            assert error.status == 409
        else:
            import hashlib
            import json

            sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
            details = [json.loads(item) for item in _export_audits(case)]
            assert details and details[-1]["sha256"] == sha


def test_range_export_aborts_when_source_changes(business, monkeypatch):
    case = business
    posting, _opening = bound(case)
    post(case, posting, case.book["id"], "q05-range-001", "2026-10-05", "10.00", "10.00")
    ledger_id = ledger_of(case)
    original = presentation._render_csv

    def render_then_change(*args, **kwargs):
        content = original(*args, **kwargs)
        post(case, posting, case.book["id"], "q05-range-002", "2026-10-08", "40.00", "40.00")
        return content

    monkeypatch.setattr(presentation, "_render_csv", render_then_change)
    with pytest.raises(AccountingError) as error:
        export_range_trial_balance(posting, case.manager, ledger_id, "2026-10", "2026-10")
    assert error.value.status == 409
    assert _export_audits(case, "formal_book.range_export") == []


def test_serialization_never_holds_the_write_lock(business, monkeypatch):
    """旧实现里「序列化」在写事务内，此处第二个写事务会被锁住；新实现必须放行。"""

    case = business
    posting, _opening = bound(case)
    post(case, posting, case.book["id"], "q05-lock-001", "2026-10-05", "10.00", "10.00")
    rendering = threading.Event()
    release = threading.Event()
    original = presentation._render_csv

    def parked_render(*args, **kwargs):
        rendering.set()
        assert release.wait(10), "序列化阶段未被放行"
        return original(*args, **kwargs)

    monkeypatch.setattr(presentation, "_render_csv", parked_render)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            export_formal_book, posting, case.manager, case.book["id"], "trial_balance"
        )
        assert rendering.wait(10), "导出未进入序列化阶段"
        with case.db.transaction(write=True) as conn:
            conn.execute("UPDATE books SET name=name WHERE id=?", (case.book["id"],))
        release.set()
        content = future.result(timeout=10)
    assert "科目编码" in content


SimpleNamespace  # keep import explicit for readers scanning fixtures
