from dataclasses import replace
from pathlib import Path

import pytest

from tools.ai_inference_lab import selective, trace
from tools.ocr_replay import replay

MANIFEST = Path(__file__).parent / "fixtures/ocr_replay/manifest.json"


def ctx(sample="A1", run="r1", company="company-a", employee="e1", firm="firm-a"):
    return trace.RunContext(run, firm, company, "ledger-a", "book-a", sample, employee, "input-v1", "policy-v1")


def ev(seq, type_, context, outcome="", event_id=None, **kw):
    return trace.Event(
        event_id=event_id or f"{context.run_id}-{seq}", run_id=context.run_id, seq=seq,
        event_type=type_, outcome=outcome, firm_id=context.firm_id, company_id=context.company_id,
        ledger_id=context.ledger_id, book_id=context.book_id, sample_id=context.sample_id,
        employee_id=context.employee_id, input_version=context.input_version,
        policy_version=context.policy_version, reason_code=kw.get("reason", ""),
        message=kw.get("message", ""), evidence=tuple(kw.get("evidence", ())),
        field_diff=tuple(kw.get("diff", ())), usage=kw.get("usage"), cost=kw.get("cost"),
        timestamp="2026-10-19T00:00:00Z",
    )


def base(context, finish="completed"):
    return [
        ev(1, "run_created", context),
        ev(2, "extraction_started", context),
        ev(3, "extraction_completed", context),
        ev(4, "validation_completed", context, "review_required"),
        ev(5, "run_finished", context, finish),
    ]


def test_c01_requires_run_created():
    c = ctx()
    events = [ev(1, "review_completed", c, "review_required")]
    assert "ILLEGAL_TRANSITION" in trace.validate_events(events, {"r1": c})


def test_c02_skips_required_steps():
    c = ctx()
    events = [ev(1, "run_created", c), ev(2, "review_completed", c, "review_required")]
    assert "ILLEGAL_TRANSITION" in trace.validate_events(events, {"r1": c})


def test_c03_terminal_outcome_cannot_be_overwritten():
    c = ctx()
    events = base(c, "completed")[:4] + [ev(5, "failed", c, "failed"), ev(6, "run_finished", c, "completed")]
    assert "OUTCOME_OVERWRITE" in trace.validate_events(events, {"r1": c})


def test_c04_duplicate_event_id_is_idempotent_and_finish_is_unique():
    c = ctx()
    events = base(c)
    assert trace.validate_events(events + [events[0]], {"r1": c}) == []
    assert "DUPLICATE_FINISH" in trace.validate_events(events + [ev(6, "run_finished", c, "completed", event_id="other")], {"r1": c})


def test_c05_identity_drift_is_rejected():
    c = ctx()
    events = base(c)
    events[1] = replace(events[1], company_id="company-b")
    assert "IDENTITY_DRIFT" in trace.validate_events(events, {"r1": c})


def test_c06_cross_company_evidence_is_rejected():
    c = ctx()
    bad = trace.EvidenceRef("firm-b", "company-b", "A1", "input-v1", 1, "gross_amount", "x")
    events = [ev(1, "run_created", c), ev(2, "extraction_started", c), ev(3, "extraction_completed", c), ev(4, "validation_completed", c, "review_required", evidence=(bad,))]
    assert "EVIDENCE_SCOPE" in trace.validate_events(events, {"r1": c})


def test_c07_unknown_role_fails_closed():
    with pytest.raises(trace.TraceError, match="UNKNOWN_ROLE"):
        trace.project([], {}, trace.Principal("employe", frozenset({"firm-a"}), frozenset({"company-a"})))


def test_c08_employee_and_owner_projection():
    c1, c2 = ctx(employee="e1"), ctx(sample="A2", run="r2", employee="e2")
    events = base(c1) + base(c2)
    employee = trace.project(events, {"r1": c1, "r2": c2}, trace.Principal("employee", frozenset({"firm-a"}), frozenset({"company-a"}), "e1"))
    assert set(employee["runs"]) == {"r1"}
    owner = trace.project(events, {"r1": c1, "r2": c2}, trace.Principal("company_owner", frozenset({"firm-a"}), frozenset({"company-a"})))
    assert set(owner["runs"]) == {"r1", "r2"}


def test_c09_diagnostics_removed_without_permission():
    c = ctx()
    events = base(c)
    events[4] = ev(5, "run_finished", c, "completed", usage={"total_tokens": 1}, cost={"known": False})
    no = trace.project(events, {"r1": c}, trace.Principal("company_owner", frozenset({"firm-a"}), frozenset({"company-a"})))
    yes = trace.project(events, {"r1": c}, trace.Principal("admin", frozenset({"firm-a"}), frozenset({"company-a"}), can_diagnose=True))
    assert "usage" not in no["runs"]["r1"]["events"][-1]
    assert yes["runs"]["r1"]["events"][-1]["usage"]["total_tokens"] == 1


def test_c10_local_sequences_are_namespaced_by_run():
    c1, c2 = ctx(run="r1"), ctx(sample="A2", run="r2")
    events = base(c1) + base(c2)
    assert trace.validate_events(events, {"r1": c1, "r2": c2}) == []


def test_c11_other_company_corruption_does_not_leak():
    good = ctx()
    bad = ctx(run="r2", company="company-b")
    events = base(good) + [ev(1, "review_completed", bad, "review_required")]
    projection = trace.project(events, {"r1": good, "r2": bad}, trace.Principal("employee", frozenset({"firm-a"}), frozenset({"company-a"}), "e1"))
    assert set(projection["runs"]) == {"r1"}
    assert all("company-b" not in str(item) for item in projection["errors"])


def test_c12_actual_dl_r1b_product_converts_to_events():
    report = selective.run(MANIFEST)
    manifest = replay.load_manifest(MANIFEST)
    contexts = {
        s["sample_id"]: trace.RunContext(
            "run-" + s["sample_id"], s["firm_id"], s["company_id"], s["ledger_id"], s["book_id"],
            s["sample_id"], "e1", s["ocr_sha256"].lower(), "policy-v1",
        )
        for s in manifest["samples"]
    }
    by_run = {c.run_id: c for c in contexts.values()}
    events = trace.events_from_dl02(report, contexts)
    assert trace.validate_events(events, by_run) == []
    view = trace.project(events, by_run, trace.Principal("employee", frozenset({"firm-a"}), frozenset({"company-a"}), "e1"))
    assert view["runs"]
    assert any("review_required" in str(run["outcome"]) for run in view["runs"].values())


def test_viewer_contains_no_script_payload(tmp_path):
    c = ctx()
    events = base(c)
    projection = trace.project(events, {"r1": c}, trace.Principal("employee", frozenset({"firm-a"}), frozenset({"company-a"}), "e1"))
    path = trace.write_viewer(tmp_path / "index.html", projection)
    html = path.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "http://" not in html and "https://" not in html
