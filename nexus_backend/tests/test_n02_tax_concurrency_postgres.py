"""N02 service publication races over two actual local PostgreSQL sessions.

All facts are synthetic imported invoice rows. No external supplier, model,
filing system or production database participates. Native-role attacks are in
``test_tax_workpaper_postgres``; these cases exercise the actual service.
"""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading
import time
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from test_tax_workpaper_postgres import APP_URL, case, native, pg, pytestmark  # noqa: F401


def _invoice(conn, case, *, cents=100000, tag=None):
    tag = tag or uuid4().hex
    conn.execute(
        "INSERT INTO invoice_import_batches(id,book_id,firm_id,company_id,filename,storage_key,sha256,"
        "size_bytes,row_count,direction,rows_json,notes_json,created_by,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            tag,
            case.book["id"],
            case.maker.firm_id,
            case.book["company_id"],
            tag + ".xlsx",
            "n02-synthetic/" + tag,
            "a" * 64,
            1024,
            1,
            "mixed",
            json.dumps(
                [
                    {
                        "date": "2026-10-05",
                        "direction": "output",
                        "amount_cents": cents,
                        "tax_cents": cents * 3 // 100,
                        "total_cents": cents + cents * 3 // 100,
                        "counterparty": "虚构N02客户",
                    }
                ],
                ensure_ascii=False,
            ),
            "[]",
            case.maker.user_id,
            "2026-10-28T00:00:00+00:00",
        ),
    )


@pytest.fixture
def prepared(case):
    with case.db.transaction(write=True, actor=case.maker) as conn:
        _invoice(conn, case)
    case.generated = case.tax.generate(case.editor, case.book["id"], "2026-10")
    return case


def review(case):
    return case.tax.review(
        case.reviewer,
        case.book["id"],
        case.generated["id"],
        "已核对虚构发票和正式账差异",
        case.generated["version"],
    )


def export(case, actor=None):
    return case.tax.csv(
        actor or case.reviewer,
        case.book["id"],
        case.generated["id"],
        case.generated["version"],
        case.generated["source_digest"],
    )


def audits(case, action):
    with case.db.transaction(actor=case.maker) as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM audit_events WHERE object_id=? AND action=? ORDER BY id",
                (case.generated["id"], "tax.workpaper." + action),
            )
        ]


def test_reviewed_export_uses_exact_source_and_audit(prepared):
    p = prepared.generated
    assert p["source_snapshot"]["schema"] == "n02-tax-v2"
    assert p["source_snapshot"]["scope"]["company_id"] == prepared.book["company_id"]
    assert p["source_snapshot"]["ledger"] == {"vouchers": [], "lines": []}
    assert review(prepared)["reviewed_by"] == prepared.reviewer.user_id
    body = export(prepared)
    assert body.startswith("\ufeff") and "1000.00" in body and "not_filed" in body
    audit = audits(prepared, "export")
    assert len(audit) == 1
    details = json.loads(audit[0]["details"])
    assert details["version"] == p["version"]
    assert details["source_digest"] == p["source_digest"]
    assert details["csv_sha256"] == hashlib.sha256(body.encode()).hexdigest()


def test_maker_self_review_has_exact_403(prepared):
    # The maker has current review capability: failure must be independence,
    # not the editor's missing review permission.
    prepared.generated = prepared.tax.generate(prepared.reviewer, prepared.book["id"], "2026-10")
    with pytest.raises(AccountingError) as failure:
        prepared.tax.review(
            prepared.reviewer,
            prepared.book["id"],
            prepared.generated["id"],
            "制单人尝试自行复核",
            prepared.generated["version"],
        )
    assert failure.value.status == 403
    assert "制单人不能复核" in failure.value.detail
    assert audits(prepared, "review") == []


@pytest.mark.parametrize("actor", ["editor", "viewer"])
def test_read_or_edit_permission_cannot_export(prepared, actor):
    review(prepared)
    with pytest.raises(AccountingError) as failure:
        export(prepared, getattr(prepared, actor))
    assert failure.value.status == 403
    assert audits(prepared, "export") == []


@pytest.mark.parametrize("field", ["version", "digest"])
def test_export_stale_client_binding_fails(prepared, field):
    review(prepared)
    p = prepared.generated
    with pytest.raises(AccountingError) as failure:
        prepared.tax.csv(
            prepared.reviewer,
            prepared.book["id"],
            p["id"],
            p["version"] + 1 if field == "version" else p["version"],
            "b" * 64 if field == "digest" else p["source_digest"],
        )
    assert failure.value.status == 409
    assert audits(prepared, "export") == []


@pytest.mark.parametrize("operation", ["review", "export"])
@pytest.mark.parametrize("mutation", ["source", "revoke"])
def test_committed_source_or_revocation_wins_before_publication(
    prepared, monkeypatch, operation, mutation
):
    if operation == "export":
        review(prepared)
    ready, release = threading.Event(), threading.Event()
    original = prepared.tax._scope
    pids = {}

    def scoped(actor, book_id, capability):
        result = original(actor, book_id, capability)
        with prepared.db.transaction(actor=actor) as conn:
            pids["publication"] = conn.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
        ready.set()
        assert release.wait(10), "publication barrier timed out"
        return result

    monkeypatch.setattr(prepared.tax, "_scope", scoped)
    # Keep a writer connection open while publication resolves its original grant.
    # The publication resumes only after this transaction commits its mutation.
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = None
        try:
            with prepared.db.transaction(write=True, actor=prepared.maker) as conn:
                pids["mutation"] = conn.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
                future = pool.submit(review if operation == "review" else export, prepared)
                assert ready.wait(10), "scope barrier timed out"
                if mutation == "source":
                    _invoice(conn, prepared, cents=20000)
                else:
                    conn.execute(
                        "DELETE FROM book_access WHERE book_id=? AND user_id=?",
                        (prepared.book["id"], prepared.reviewer.user_id),
                    )
            release.set()
            with pytest.raises(AccountingError) as failure:
                future.result(timeout=15)
            assert failure.value.status in ({409} if mutation == "source" else {403, 404})
        finally:
            release.set()
    assert pids["publication"] != pids["mutation"]
    assert audits(prepared, operation) == []
    if operation == "review":
        assert (
            prepared.tax.get(prepared.maker, prepared.book["id"], prepared.generated["id"])[
                "status"
            ]
            == "draft"
        )


def test_two_simultaneous_reviews_write_one_receipt(prepared, monkeypatch):
    barrier = threading.Barrier(2)
    original = prepared.tax._scope

    def scoped(actor, book_id, capability):
        result = original(actor, book_id, capability)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(prepared.tax, "_scope", scoped)

    def attempt():
        try:
            return ("ok", review(prepared)["status"])
        except AccountingError as failure:
            return ("rejected", failure.status)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(attempt)
        second = pool.submit(attempt)
        results = [first.result(timeout=20), second.result(timeout=20)]
    assert sorted(results) == [("ok", "reviewed"), ("rejected", 409)]
    assert len(audits(prepared, "review")) == 1


def test_amount_above_int32_within_accounting_limit_works(case):
    with case.db.transaction(write=True, actor=case.maker) as conn:
        _invoice(conn, case, cents=4_000_000_000)
    case.generated = case.tax.generate(case.editor, case.book["id"], "2026-10")
    assert case.generated["output_net"] == "40000000.00"
    assert review(case)["status"] == "reviewed"
    assert "40000000.00" in export(case)


def test_export_serializes_source_writer_until_snapshot_is_published(prepared, monkeypatch):
    review(prepared)
    ready, release, writer_ready = threading.Event(), threading.Event(), threading.Event()
    original = prepared.tax._assert_current
    pids = {}
    pause_once = True

    def paused(conn, book, row):
        nonlocal pause_once
        if pause_once:
            pause_once = False
            pids["publication"] = conn.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
            ready.set()
            assert release.wait(10), "in-lock publication barrier timed out"
        return original(conn, book, row)

    monkeypatch.setattr(prepared.tax, "_assert_current", paused)

    def mutate():
        with native(prepared, prepared.maker) as conn:
            pids["mutation"] = conn.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
            writer_ready.set()
            _invoice(conn, prepared, cents=20000)
        return "committed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        publication = pool.submit(export, prepared)
        writer = None
        try:
            assert ready.wait(10)
            writer = pool.submit(mutate)
            assert writer_ready.wait(10)
            # Observe a real PostgreSQL lock wait, rather than guessing from a
            # sleep or merely asserting that an unfinished Future exists.
            observed = False
            with prepared.psycopg.connect(APP_URL, autocommit=True) as observer:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    row = observer.execute(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s",
                        (pids["mutation"],),
                    ).fetchone()
                    if row and row[0] == "Lock":
                        observed = True
                        break
                    release.wait(0.02)
            assert observed, "source writer never entered the expected PostgreSQL lock wait"
            assert not writer.done()
            assert pids["publication"] != pids["mutation"]
            release.set()
            body = publication.result(timeout=15)
            assert writer.result(timeout=15) == "committed"
        finally:
            release.set()
    assert "1000.00" in body and "200.00" not in body
    assert prepared.generated["source_digest"] in body
    assert len(audits(prepared, "export")) == 1
    with pytest.raises(AccountingError) as failure:
        export(prepared)
    assert failure.value.status == 409
    assert len(audits(prepared, "export")) == 1
