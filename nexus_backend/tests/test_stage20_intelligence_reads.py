"""Real SQLite regressions for bounded reads and short atomic handoff writes."""

from contextlib import closing, contextmanager

import pytest

from app.accounting.domain import AccountingError
from app.intelligence.checks import invoice_key
from test_business import approved, business, handoff as business_handoff  # noqa: F401
from test_stage8_workflow_integration import replacement  # noqa: F401
from test_intelligence_workflow import (  # noqa: F401
    ai_case,
    create_event,
    extract,
    invoice_document,
)


def counted_assessment(case, event, actor="finance", **kwargs):
    with case.db.transaction(actor=case.actors[actor]) as conn:
        current = case.business._event(conn, case.actors[actor], event["id"])
        statements = []
        conn.set_trace_callback(statements.append)
        result = case.intelligence._assessment(conn, case.actors[actor], current, **kwargs)
        conn.set_trace_callback(None)
    return result, statements


def test_unrelated_company_events_do_not_add_assessment_roundtrips(ai_case):
    case = ai_case
    event = create_event(case)
    extract(case, event)
    before, initial = counted_assessment(case, event)
    # All are visible to finance; the previous implementation read membership,
    # latest OCR and supersessions separately for every unrelated event.
    for index in range(12):
        create_event(case, description=f"Unrelated draft {index}")
    after, expanded = counted_assessment(case, event)
    assert after == before
    assert len(expanded) == len(initial)
    assert sum("JOIN business_events e ON e.id=r.event_id" in sql for sql in expanded) == 1


def test_duplicate_invoice_scope_is_live_and_does_not_disclose_peer_sources(ai_case):
    case = ai_case
    event, peer = create_event(case), create_event(case, actor="peer")
    extract(case, event)
    peer_run = extract(case, peer, actor="peer")
    keys = {invoice_key(invoice_document())}

    def matching(actor):
        with case.db.transaction(actor=case.actors[actor]) as conn:
            current = case.business._event(conn, case.actors[actor], event["id"])
            return case.intelligence._duplicate_invoice_runs(
                conn, case.actors[actor], current, keys
            )

    assert matching("employee") == []
    assert matching("finance") == [peer_run["id"]]
    assert matching("approver") == [peer_run["id"]]
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute("DELETE FROM department_approvers WHERE user_id=?", ("approver",))
    with pytest.raises(AccountingError) as revoked:
        matching("approver")
    assert revoked.value.status == 404
    # Employee still cannot learn that an otherwise matching peer invoice exists.
    own, _ = counted_assessment(case, event, actor="employee")
    assert not any(row["code"] == "duplicate_invoice_other_event" for row in own["findings"])


def test_duplicate_detection_does_not_fall_back_to_older_success(ai_case):
    from app.intelligence.provider import ProviderError
    from app.intelligence.requests import OCRRequest

    case = ai_case
    event, peer = create_event(case), create_event(case, actor="peer")
    extract(case, event)
    extract(case, peer, actor="peer")
    original, _ = counted_assessment(case, event)
    assert any(row["code"] == "duplicate_invoice_other_event" for row in original["findings"])
    case.provider.failure = ProviderError("provider_failed", "Synthetic provider failure")
    failed = case.intelligence.extract(
        case.actors["peer"],
        peer["id"],
        OCRRequest(
            attachment_id=peer["attachments"][0]["id"],
            expected_version=peer["version"],
            idempotency_key="newer-failed-ocr",
            force_refresh=True,
        ),
    )
    assert failed["status"] == "failed"
    current, _ = counted_assessment(case, event)
    assert not any(row["code"] == "duplicate_invoice_other_event" for row in current["findings"])


def test_persisting_assessment_ignores_supplied_stale_evidence(ai_case):
    case = ai_case
    event = create_event(case)
    run = extract(case, event)
    with case.db.transaction(write=True, actor=case.actors["finance"]) as conn:
        current = case.business._event(conn, case.actors["finance"], event["id"])
        assessment = case.intelligence._assessment(
            conn, case.actors["finance"], current, persist=True, latest={}, attachments=[]
        )
    assert assessment["references"][0]["extraction_id"] == run["id"]
    assert assessment["documents"][0]["gross_amount"] == "0.30"


def test_handoff_presentation_runs_after_atomic_commit_without_writer_lock(business, monkeypatch):
    case = business
    event = approved(case)
    original = case.service._event_json
    observed = []

    def inspect(conn, actor, current):
        # A second writer must acquire its lock while response history is being
        # serialized. It must also see all three pieces of the committed handoff.
        with closing(case.db.connect()) as concurrent:
            concurrent.execute("PRAGMA busy_timeout=10")
            concurrent.execute("BEGIN IMMEDIATE")
            mapping = concurrent.execute(
                "SELECT * FROM business_mappings WHERE event_id=?", (event["id"],)
            ).fetchone()
            assert mapping
            assert concurrent.execute(
                "SELECT id FROM sources WHERE id=?", (mapping["source_id"],)
            ).fetchone()
            assert (
                concurrent.execute(
                    "SELECT status FROM drafts WHERE id=?", (mapping["draft_id"],)
                ).fetchone()[0]
                == "draft"
            )
            concurrent.rollback()
        observed.append(current["id"])
        return original(conn, actor, current)

    monkeypatch.setattr(case.service, "_event_json", inspect)
    receipt = business_handoff(case, event)
    repeated = business_handoff(case, event)
    assert receipt == repeated
    assert observed == [event["id"], event["id"]]


@pytest.mark.parametrize("revoke", ["user", "book"])
def test_handoff_response_reauthorizes_after_commit_and_retry_recovers_receipt(
    business, monkeypatch, revoke
):
    case = business
    event = approved(case)
    original_transaction = case.db.transaction
    revoked_once = False
    wrote = False

    @contextmanager
    def revoke_before_response(*args, **kwargs):
        nonlocal revoked_once, wrote
        # 企业写事务开始前会有一次公司预读；撤权必须注入在「写入已提交、
        # 响应读取尚未开始」之间，才能验证回执先提交、响应再授权的语义。
        if kwargs.get("write", False):
            wrote = True
        elif wrote and not revoked_once:
            revoked_once = True
            with closing(case.db.connect()) as conn:
                if revoke == "user":
                    conn.execute("UPDATE users SET active=0 WHERE id=?", ("finance",))
                else:
                    conn.execute(
                        "DELETE FROM book_access WHERE book_id=? AND user_id=?",
                        (case.book["id"], "finance"),
                    )
                conn.commit()
        with original_transaction(*args, **kwargs) as conn:
            yield conn

    monkeypatch.setattr(case.db, "transaction", revoke_before_response)
    with pytest.raises(AccountingError) as revoked:
        business_handoff(case, event)
    assert revoked.value.status in {403, 404}
    monkeypatch.setattr(case.db, "transaction", original_transaction)
    if revoke == "book":
        # Company finance still sees its event. That weaker grant must not be
        # enough to publish an edit-command receipt for the revoked book.
        assert case.service.get_event(case.actors["finance"], event["id"])["status"] == "handed_off"
    with closing(case.db.connect()) as conn:
        mapping = conn.execute(
            "SELECT * FROM business_mappings WHERE event_id=?", (event["id"],)
        ).fetchone()
        assert mapping is not None
        if revoke == "user":
            conn.execute("UPDATE users SET active=1 WHERE id=?", ("finance",))
        else:
            conn.execute(
                "INSERT INTO book_access(firm_id,book_id,user_id,role) VALUES(?,?,?,'editor')",
                (case.manager.firm_id, case.book["id"], "finance"),
            )
        conn.commit()
    receipt = business_handoff(case, event)
    assert receipt["draft_id"] == mapping["draft_id"]
    with closing(case.db.connect()) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM business_mappings WHERE event_id=?", (event["id"],)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute("SELECT status FROM drafts WHERE id=?", (mapping["draft_id"],)).fetchone()[
                0
            ]
            == "draft"
        )


def test_duplicate_scan_excludes_accepted_superseded_original(replacement):
    from test_stage8_workflow_integration import identify_current

    case = replacement
    other = create_event(case)
    extract(case, other, key="second-event-invoice")
    keys = {invoice_key(invoice_document())}
    with case.db.transaction(actor=case.actors["finance"]) as conn:
        current = case.business._event(conn, case.actors["finance"], other["id"])
        assert (
            case.intelligence._duplicate_invoice_runs(conn, case.actors["finance"], current, keys)
            == []
        )
    new_run = identify_current(case)
    with case.db.transaction(actor=case.actors["finance"]) as conn:
        current = case.business._event(conn, case.actors["finance"], other["id"])
        matches = case.intelligence._duplicate_invoice_runs(
            conn, case.actors["finance"], current, keys
        )
    assert matches == [new_run["id"]]
    assert case.original_run["id"] not in matches


def test_batched_findings_preserve_latest_review_and_current_fingerprint(ai_case):
    case = ai_case
    event = create_event(case, amount="0.40")
    extract(case, event)
    detail = case.intelligence.reconcile(case.actors["finance"], event["id"])
    mismatch = next(row for row in detail["findings"] if row["code"] == "document_business_amount")
    case.intelligence.resolve(
        case.actors["finance"], mismatch["id"], "First independent explanation"
    )
    case.intelligence.resolve(
        case.actors["finance"], mismatch["id"], "Updated independent explanation"
    )
    assessment, statements = counted_assessment(case, event)
    actual = next(row for row in assessment["findings"] if row["id"] == mismatch["id"])
    with case.db.transaction(actor=case.actors["finance"]) as conn:
        stored = conn.execute("SELECT * FROM ai_findings WHERE id=?", (mismatch["id"],)).fetchone()
        expected = case.intelligence._finding_json(conn, stored)
    assert actual == expected
    assert actual["resolved"] and actual["resolution_reason"] == "Updated independent explanation"
    assert sum("latest_review_reason" in sql for sql in statements) == 1
    assert not any("WHERE finding_id=" in sql for sql in statements)
