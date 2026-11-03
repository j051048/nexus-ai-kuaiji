"""Default SQLite collection and real-PG acceptance cannot silently replace each other."""

import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import pytest

from scripts.p02_browser_gate import validate_result

ROOT = Path(__file__).resolve().parents[2]
NAMES = [
    "P02 initial 403 clears loading and permits explicit reread",
    "P02 revoked late write cannot restore data or success",
    "P02 foreign company book and reports stay invisible in the browser",
    "P02 client identity cannot review, lock or batch export",
    "P02 real PG signed identity month-close desktop",
    "P02 real PG signed identity month-close mobile",
    "P02 real PG signed identity delivery and acknowledgement desktop",
    "P02 real PG signed identity delivery and acknowledgement mobile",
]


@pytest.mark.parametrize("fault", ["missing", "duplicate", "skipped", "failure", "error"])
def test_browser_gate_rejects_incomplete_or_failed_evidence(tmp_path, fault):
    root = ET.Element("testsuite")
    for name in NAMES[:-1] if fault == "missing" else NAMES:
        ET.SubElement(root, "testcase", name=name)
    if fault == "duplicate":
        root[-1].set("name", NAMES[0])
    elif fault in {"skipped", "failure", "error"}:
        ET.SubElement(root[-1], fault)
    path = tmp_path / "junit.xml"
    ET.ElementTree(root).write(path)
    with pytest.raises(ValueError):
        validate_result(path)


def test_default_collection_needs_no_private_fixture_and_pg_suite_remains_required(tmp_path):
    env = dict(os.environ, P02_BROWSER_FIXTURE=str(tmp_path / "missing-private.json"))
    command = ["node", "node_modules/@playwright/test/cli.js", "test", "--list", "--reporter=json"]
    default = subprocess.run(
        command, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    assert default.returncode == 0, default.stderr[-1000:]
    data = json.loads(default.stdout)
    names = {Path(row["file"]).name for row in data["suites"]}
    assert "next02-isolation.spec.ts" not in names
    assert "p02-r1-business-flow.spec.ts" not in names
    fixture = tmp_path / "fixture-private.json"
    fixture.write_text(
        json.dumps(
            {
                "auth_origin": "https://c01-real-auth-25511.example.test",
                "auth_local_origin": "http://127.0.0.1:25511",
                "cases": {"desktop": {}, "mobile": {}},
            }
        )
    )
    env["P02_BROWSER_FIXTURE"] = str(fixture)
    special = subprocess.run(
        command + ["--config=playwright.p02.config.ts"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert special.returncode == 0, special.stderr[-1000:]
    data = json.loads(special.stdout)
    assert {Path(row["file"]).name for row in data["suites"]} == {
        "next02-isolation.spec.ts",
        "p02-r1-business-flow.spec.ts",
        "p02-close-boundary.spec.ts",
    }
