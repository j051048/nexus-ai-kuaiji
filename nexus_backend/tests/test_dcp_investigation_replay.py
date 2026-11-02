"""Exercise the actual fixed readers, then score frozen task gold independently."""

from copy import deepcopy
from pathlib import Path
import pytest
from app.assistant import investigation
from app.accounting.domain import Actor
from test_delivery_followups import business, case, close_case, ready_case  # noqa: F401
from test_dcp_notification_investigation import unknown


def test_real_domain_readers_with_frozen_holdout(case, monkeypatch, tmp_path):
    from tools.assistant_replay import investigation as replay

    monkeypatch.setenv(investigation.ENV_FLAG, "1")
    dataset = replay.load_dataset()
    candidate = replay.candidate_identity()
    rows = []

    def observe(sid, actor, book, **kwargs):
        return replay.observe(
            dataset, candidate, sid, case.service, actor, book["id"], book["period"], **kwargs
        )

    rows.append(observe("dev_complete", case.manager, case.book))
    rows.append(observe("dev_foreign", Actor(case.manager.user_id, "foreign-firm"), case.book))
    with monkeypatch.context() as m:

        def failed(*args):
            raise RuntimeError("synthetic reader failure")

        m.setitem(investigation.READERS, "voucher_status", failed)
        rows.append(observe("dev_failed", case.manager, case.other_book))
    # Acceptance IDs are never input to candidate selection (this runner has no selection API).
    with monkeypatch.context() as m:
        original = investigation.READERS["voucher_status"]
        count = 0

        def changed(*args):
            nonlocal count
            count += 1
            return {**original(*args), "draft_rows": count}

        m.setitem(investigation.READERS, "voucher_status", changed)
        rows.append(observe("holdout_changed", case.manager, case.other_book))
    actor = case.actors["accountant"]

    def revoke(name):
        if name == "close_blockers":
            with case.db.transaction(write=True, actor=case.manager) as conn:
                conn.execute(
                    "DELETE FROM book_access WHERE book_id=? AND user_id=?",
                    (case.book["id"], actor.user_id),
                )

    rows.append(observe("holdout_revoked", actor, case.book, on_step=revoke))
    unknown(case)
    rows.append(observe("holdout_unknown", case.manager, case.book))
    report = replay.evaluate(dataset, rows, candidate)
    assert report["denominator"] == 6 and report["passed"] == 6
    assert report["splits"]["holdout"] == {"passed": 3, "denominator": 3}
    assert report["model_calls"] == 0
    assert replay.evaluate(dataset, rows[:-1], candidate)["denominator"] == 6
    bad = deepcopy(rows)
    bad[-1]["result"]["findings"]["delivery_status"]["notifications"]["unknown"] = 0
    with pytest.raises(ValueError):
        replay.evaluate(dataset, bad, candidate)
    # Even rehashed fabricated complete/ready output must fail independent gold.
    bad = deepcopy(rows)
    bad[1] = {**bad[1], "result": deepcopy(rows[0]["result"])}
    bad[1]["result_sha256"] = replay.digest(bad[1]["result"])
    assert replay.evaluate(dataset, bad, candidate)["status"] == "regression"
    with pytest.raises(ValueError):
        replay.evaluate(dataset, rows + rows[:1], candidate)
    out = tmp_path / "report"
    replay.save(report, rows, out)
    assert (out / "report.json").exists() and "holdout" in (out / "report.md").read_text(
        encoding="utf-8"
    )
    with pytest.raises(ValueError):
        replay.save(report, rows, out)
    # Retain local replay evidence outside pytest temp for the combined acceptance report.
    import os

    if os.getenv("DCP_REPLAY_OUT"):
        replay.save(report, rows, Path(os.environ["DCP_REPLAY_OUT"]))
