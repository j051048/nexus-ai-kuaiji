"""Machine evidence for the P01 runner, with semantic PG identities.

This plugin never records parameters other than the integer PG major, traceback
text, request data, credentials, or stdout. Missing teardown is a failed gate.
"""

import json
import os
from pathlib import Path

import pytest


def pytest_configure(config):
    config._p01_cases = []
    config._p01_events = []
    config._p01_deselected = 0


def pytest_deselected(items):
    if items:
        items[0].config._p01_deselected += len(items)


def pytest_collection_finish(session):
    for item in session.items:
        major = getattr(getattr(item, "callspec", None), "params", {}).get("major")
        session.config._p01_cases.append(
            {
                "nodeid": item.nodeid,
                "function": getattr(item, "originalname", None) or item.name.split("[")[0],
                "module": Path(item.path).stem,
                "major": major if type(major) is int else None,
            }
        )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    item.config._p01_events.append(
        {
            "nodeid": report.nodeid,
            "when": report.when,
            "outcome": report.outcome,
            "wasxfail": hasattr(report, "wasxfail"),
        }
    )


def pytest_sessionfinish(session, exitstatus):
    path = Path(os.environ["P01_PHASE_EVIDENCE"])
    # Each gate phase owns a fresh path. Never accept a preexisting result.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "schema": "p01-pytest-v1",
                "exitstatus": int(exitstatus),
                "collected": session.config._p01_cases,
                "events": session.config._p01_events,
                "deselected": session.config._p01_deselected,
            },
            stream,
        )
