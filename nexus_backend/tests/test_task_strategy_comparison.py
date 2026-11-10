"""Independent task gold, actual domain execution, never model-voted scores."""

from copy import deepcopy
import os
import pytest
from app.accounting.domain import Actor
from app.accounting.models import BookMemberGrant
from app.assistant import investigation
from test_delivery_followups import business, case, close_case, ready_case  # noqa: F401
from test_dcp_notification_investigation import unknown


def test_fixed_serial_parallel_comparison_and_holdout(case, monkeypatch, tmp_path):
    from tools.assistant_replay import strategies as replay

    monkeypatch.setenv(investigation.ENV_FLAG, "1")
    dataset, candidate = replay.load_dataset(), replay.candidate_identity()
    observations = {}
    unknown(case)
    for strategy in ("serial", "parallel2"):
        monkeypatch.setenv(
            "ACCOUNTING_ASSISTANT_READ_PARALLELISM", "1" if strategy == "serial" else "2"
        )
        rows = []

        def observe(sid, actor, book, **kwargs):
            return replay.observe_strategy(
                dataset,
                candidate,
                strategy,
                sid,
                case.service,
                actor,
                book["id"],
                book["period"],
                **kwargs,
            )

        rows.append(observe("dev_complete", case.manager, case.book))
        rows.append(observe("dev_foreign", Actor(case.manager.user_id, "foreign-firm"), case.book))
        with monkeypatch.context() as m:

            def failed(*args):
                raise RuntimeError("synthetic")

            m.setitem(investigation.READERS, "voucher_status", failed)
            rows.append(observe("dev_failed", case.manager, case.other_book))
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
        case.directory.grant_book_member(
            case.manager, case.book["id"], actor.user_id, BookMemberGrant(role="reviewer")
        )

        def revoke(name):
            if name == "close_blockers":
                case.directory.revoke_book_member(case.manager, case.book["id"], actor.user_id)

        rows.append(observe("holdout_revoked", actor, case.book, on_step=revoke))
        rows.append(observe("holdout_unknown", case.manager, case.book))
        observations[strategy] = rows
    report = replay.compare(dataset, observations, candidate)
    assert report["status"] == "equivalent_offline"
    assert report["denominator"] == 6 and report["selected_strategy"] is None
    assert report["strategies"]["parallel2"]["splits"]["holdout"] == {"passed": 3, "denominator": 3}
    assert report["model_cost_cny"] is None
    missing = deepcopy(observations)
    missing["parallel2"].pop()
    assert replay.compare(dataset, missing, candidate)["status"] == "regression"
    assert replay.compare(dataset, missing, candidate)["denominator"] == 6
    bad = deepcopy(observations)
    bad["parallel2"][1]["result"] = deepcopy(bad["parallel2"][0]["result"])
    bad["parallel2"][1]["result_sha256"] = replay.digest(bad["parallel2"][1]["result"])
    assert replay.compare(dataset, bad, candidate)["status"] == "regression"
    bad = deepcopy(observations)
    bad["parallel2"][0]["strategy"] = "serial"
    with pytest.raises(ValueError):
        replay.compare(dataset, bad, candidate)
    replay.save_comparison(report, observations, tmp_path / "comparison")
    assert (tmp_path / "comparison/report.md").exists()
    if os.getenv("TASK_STRATEGY_REPORT"):
        replay.save_comparison(report, observations, os.environ["TASK_STRATEGY_REPORT"])
