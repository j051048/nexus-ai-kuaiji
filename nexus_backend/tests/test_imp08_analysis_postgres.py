"""Restricted runtime, two real PG sessions; no models or accounting bypass."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.assistant import analysis
from app.assistant.observation import AnalysisObservation, ObservationLedger, ObservationError
from test_monthly_delivery_postgres import p0_pg, p0_case
from test_imp08_analysis import _voucher
from test_b04_pilot import setup_b03
import test_posting_service as posting_fixture


class PrivateCase(SimpleNamespace):
    def __repr__(self):
        return "PrivateCase(local isolated PostgreSQL; connection details omitted)"


@pytest.fixture
def analyzed(p0_case, monkeypatch):
    c = PrivateCase(**vars(p0_case))
    c.company = {"id": c.book["company_id"]}

    def ledger_of(case):
        with case.db.transaction(actor=case.manager) as conn:
            return conn.execute(
                "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (case.book["id"],)
            ).fetchone()["ledger_id"]

    monkeypatch.setattr(posting_fixture, "ledger_of", ledger_of)
    monkeypatch.setattr(posting_fixture, "admin", lambda _: c.reviewer)
    opening, posting = setup_b03(c)
    voucher = _voucher(
        c,
        opening,
        posting,
        "imp-pg-" + uuid4().hex,
        [("expense", "100.00", "0.00"), ("bank", "0.00", "100.00")],
    )
    c.voucher = voucher["voucher"]["id"]
    with c.db.transaction(
        actor=c.manager, write=True, write_scope="company", company_id=c.book["company_id"]
    ) as conn:
        for amount in (6000, 3000):
            conn.execute(
                "INSERT INTO voucher_sources(id,voucher_id,source_kind,source_id,source_version,amount_cents,evidence_ref) VALUES(?,?,?,?,?,?,?)",
                (uuid4().hex, c.voucher, "source", uuid4().hex, 1, amount, "synthetic-only"),
            )
    return c


def read(c, actor=None, **kwargs):
    return analysis.analyze(
        c.db,
        actor or c.manager,
        c.book["id"],
        "2026-10",
        "invoice_voucher_diff",
        target_voucher_id=c.voucher,
        **kwargs,
    )


def test_pg_analysis_success_scope_and_exact_fact(analyzed):
    c = analyzed
    result = read(c)
    assert result["facts"]["difference"] == "10.00"
    assert result["model_calls"] == 0
    assert result["coverage"]["detail_complete"] is True
    for actor, book in ((c.foreign, c.book), (c.manager, c.other_book)):
        with pytest.raises(AccountingError) as err:
            analysis.analyze(
                c.db,
                actor,
                book["id"],
                "2026-10",
                "invoice_voucher_diff",
                target_voucher_id=c.voucher,
            )
        assert err.value.status == 404
    claim = {k: result[k] for k in ("company_id", "book_id", "period", "fingerprint")}
    claim.update(
        amount_cents=10000, currency="CNY", fact_ids=["invoice_voucher_diff:difference_cents"]
    )
    with pytest.raises(AccountingError):
        analysis.validate_claim(result, claim)


@pytest.mark.parametrize("mutation", ["revoke", "source"])
def test_pg_two_session_publication_fence(analyzed, monkeypatch, mutation):
    c = analyzed
    read_done, changed = Event(), Event()
    original = analysis._dependencies
    count = 0

    def pause(conn, books):
        nonlocal count
        value = original(conn, books)
        count += 1
        if count == 1:
            read_done.set()
            assert changed.wait(15), "second session did not commit"
        return value

    monkeypatch.setattr(analysis, "_dependencies", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(read, c, actor=c.reviewer)
        assert read_done.wait(15)
        try:
            with c.psycopg.connect(c.admin) as conn:
                if mutation == "revoke":
                    conn.execute(
                        "DELETE FROM accounting.memberships WHERE user_id=%s AND firm_id=%s",
                        (c.reviewer.user_id, c.reviewer.firm_id),
                    )
                else:
                    conn.execute(
                        "UPDATE accounting.voucher_sources SET amount_cents=amount_cents+1 WHERE voucher_id=%s",
                        (c.voucher,),
                    )
        finally:
            changed.set()
        with pytest.raises(AccountingError) as err:
            future.result(timeout=15)
        assert err.value.status in ({403, 404} if mutation == "revoke" else {409})


def test_pg_page_version_and_observation_use_same_reader(analyzed, tmp_path):
    c = analyzed
    first = read(c, limit=1)
    assert not first["coverage"]["detail_complete"]
    last = read(c, limit=1, offset=2, expected_source_version=first["source_version"])
    assert last["coverage"]["detail_rows"] == 0 and not last["coverage"]["detail_complete"]
    scope = {k: first[k] for k in ("company_id", "book_id", "period")}
    # Freeze the same parameters as the observed page.
    event = {
        **scope,
        "event_id": "real-page",
        "event_version": 1,
        "source_version": first["source_version"],
        "kind": "invoice_voucher_diff",
        "params": {"target_voucher_id": c.voucher, "limit": 1},
    }
    resolver = AnalysisObservation(c.db, c.manager, scope)
    ledger = ObservationLedger(
        directory=tmp_path / "observe", scope=scope, principal=c.manager.user_id
    )
    assert ledger.record(event, resolver=resolver)["would_handoff"]
    with c.psycopg.connect(c.admin) as conn:
        conn.execute(
            "UPDATE accounting.voucher_sources SET amount_cents=amount_cents+1 WHERE voucher_id=%s",
            (c.voucher,),
        )
    with pytest.raises(AccountingError) as error:
        read(c, limit=1, offset=1, expected_source_version=first["source_version"])
    assert error.value.status == 409
    with pytest.raises(AccountingError):
        ObservationLedger(
            directory=tmp_path / "observe", scope=scope, principal=c.manager.user_id
        ).record(event, resolver=resolver)


def test_pg_observation_runner_uses_trusted_current_database(analyzed, tmp_path):
    import os
    import json
    from scripts.imp08_observe import run

    c = analyzed
    first = read(c, limit=1)
    scope = {k: first[k] for k in ("company_id", "book_id", "period")}
    event = {
        **scope,
        "event_id": "runner-page",
        "event_version": 1,
        "source_version": first["source_version"],
        "kind": "invoice_voucher_diff",
        "params": {"target_voucher_id": c.voucher, "limit": 1},
    }
    config = tmp_path / "local-private.json"
    config.write_text(
        json.dumps(
            {
                "admin": c.admin,
                "app": os.environ["ACCOUNTING_TEST_DATABASE_URL"],
                "worker": os.environ["ACCOUNTING_TEST_WORKER_DATABASE_URL"],
                "projection": os.environ["ACCOUNTING_PROJECTION_DATABASE_URL"],
            }
        ),
        encoding="utf-8",
    )
    with c.psycopg.connect(c.admin) as conn:
        major = int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000
    principal = {"user_id": c.manager.user_id, "firm_id": c.manager.firm_id}
    report = run(config, major, [event], scope, principal, tmp_path / "runner")
    again = run(config, major, [event], scope, principal, tmp_path / "runner")
    assert report["records"] == again["records"] and report["business_writes"] == 0
    assert report["model_calls"] == 0
    assert len(list((tmp_path / "runner/checkpoints").glob("step-*.json"))) == 1
