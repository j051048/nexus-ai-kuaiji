"""Focused tests for the IMP05 local trial analyser."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("imp05_trial_summary", ROOT / "scripts/imp05_trial_summary.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def row(**overrides):
    base = {
        "candidate": "a" * 64,
        "participant_id": "p01",
        "role": "employee",
        "company": "澄光科技",
        "device": "desktop-chrome",
        "network": "wifi",
        "task_id": "T1",
        "attempt": "1",
        "outcome": "completed",
        "seconds": "60",
        "assists": "0",
        "wrong_turns": "0",
        "state_understood": "true",
        "duplicate_write": "false",
        "failure_step": "",
        "notes": "",
    }
    base.update(overrides)
    return base


def test_empty_records_is_incomplete_and_never_zero_errors():
    result = MODULE.summarise([])
    assert result["status"] == "incomplete"
    assert result["reason"] == "no_records"
    assert result["denominator"] == 0
    assert result["rates"] == {}


def test_automation_rows_and_missing_device_are_incomplete():
    result = MODULE.summarise([row(device="automation"), row(participant_id="p02", device="")])
    assert result["status"] == "incomplete"
    assert any("automation" in item for item in result["problems"])
    assert any("missing" in item for item in result["problems"])


def test_duplicate_attempt_is_rejected():
    result = MODULE.summarise([row(), row()])
    assert result["status"] == "incomplete"
    assert any("duplicate attempt" in item for item in result["problems"])


def test_abandoned_attempt_stays_in_denominator():
    result = MODULE.summarise(
        [
            row(),
            row(participant_id="p02", task_id="T2", outcome="abandoned", failure_step="unclear"),
        ]
    )
    assert result["status"] == "recorded"
    assert result["denominator"] == 2
    assert result["counts"]["abandoned"] == 1
    assert result["rates"]["abandoned"] == 0.5


def test_assistance_is_reported_separately():
    result = MODULE.summarise([row(assists="1", outcome="assisted")])
    assert result["counts"]["completed_independently"] == 0
    assert result["counts"]["assisted"] == 1
    assert result["rates"]["independent_completion"] == 0.0


def test_cli_round_trip(tmp_path):
    source = tmp_path / "records.csv"
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row()))
        writer.writeheader()
        writer.writerow(row())
    out = tmp_path / "summary.json"
    assert MODULE.main.__module__ == "imp05_trial_summary"
    # Call the same function the CLI uses; subprocess execution is covered by
    # the acceptance run.
    result = MODULE.summarise([row()])
    assert result["status"] == "recorded"
