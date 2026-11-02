"""The required QA selection may never silently disappear behind skips."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "body",
    [
        "import pytest\ndef test_case(): pytest.skip('missing env')",
        "import pytest\n@pytest.mark.xfail\ndef test_case(): assert False",
        "# no tests",
    ],
)
def test_skipped_xfail_or_empty_suite_cannot_pass(tmp_path, body):
    source = tmp_path / "test_case.py"
    source.write_text(body)
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "scripts.qa_no_skip",
            str(source),
            "--confcutdir=" + str(tmp_path),
            "-q",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        timeout=30,
    )
    # Exclude a missing-plugin/import failure: the plugin must actually load.
    assert b"QA requires" in result.stdout
    assert result.returncode != 0


def test_collection_skip_cannot_hide_beside_a_passing_case(tmp_path):
    (tmp_path / "test_present.py").write_text("def test_present(): pass")
    (tmp_path / "test_absent.py").write_text(
        "import pytest\npytest.skip('missing environment', allow_module_level=True)"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "scripts.qa_no_skip",
            str(tmp_path),
            "--confcutdir=" + str(tmp_path),
            "-q",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        timeout=30,
    )
    assert b"QA requires" in result.stdout
    assert result.returncode != 0
