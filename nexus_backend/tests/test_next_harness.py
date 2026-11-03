"""NEXT01: candidate-bound runs, owned resources and crash recovery.

These tests never touch a real database, network or the user's working books.
They exercise the harness that every later NEXT package binds its evidence to.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from scripts import next_harness as nh


REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable


@pytest.fixture()
def base(tmp_path, monkeypatch):
    root = tmp_path / "next"
    root.mkdir()
    monkeypatch.setattr(nh, "NEXT_ROOT", root)
    return root


def _phases(*items):
    return [nh.Phase(**item) for item in items]


def _pytest_phase(name, *targets, required=True):
    return nh.Phase(name=name, command=[PYTHON, "-m", "pytest", *targets], kind="pytest", required=required)


def _manifest(run_dir):
    return json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))


def test_run_binds_candidate_and_keeps_evidence(base):
    run_dir = base / "next01-happy"
    nh.create_run("next01", run_dir)
    nh.register_resource(run_dir, "temp_dir", path=run_dir / "tmp-scratch")
    (run_dir / "tmp-scratch" / "junk.bin").write_bytes(b"x" * 32)

    manifest = nh.run_phases(
        run_dir,
        _phases(
            {
                "name": "environment",
                "command": [PYTHON, "-m", "pytest", "nexus_backend/tests/test_p01_local_recovery_env.py"],
                "kind": "pytest",
            }
        ),
    )

    assert manifest["status"] == "passed"
    assert manifest["candidate"]["before"]["source_sha256"] == manifest["candidate"]["after"]["source_sha256"]
    assert manifest["phases"]["environment"]["tests"] >= 3
    assert manifest["phases"]["environment"]["skipped"] == 0
    assert manifest["cleanup"]["status"] == "passed"
    assert not (run_dir / "tmp-scratch").exists()
    assert (run_dir / "evidence" / "environment.xml").is_file()
    assert (run_dir / "manifest.json").is_file()

    listed = {item["run_id"]: item for item in nh.status()}
    assert listed["next01-happy"]["state"] == "completed"


def test_zero_cases_is_failure_not_green(base):
    run_dir = base / "next01-zero"
    nh.create_run("next01", run_dir)
    manifest = nh.run_phases(
        run_dir,
        _phases(
            {
                "name": "empty",
                "command": [PYTHON, "-m", "pytest", "nexus_backend/tests/test_p01_local_recovery_env.py", "-k", "no_such_case_xyz"],
                "kind": "pytest",
            }
        ),
    )
    assert manifest["status"] == "failed"
    assert manifest["phases"]["empty"]["status"] == "failed"
    assert manifest["phases"]["empty"]["tests"] == 0
    assert manifest["phases"]["empty"]["diagnostic"]["code"] == "no_test_cases"
    assert manifest["cleanup"]["status"] == "passed"


def test_skip_is_failure(base):
    run_dir = base / "next01-skip"
    nh.create_run("next01", run_dir)
    fixture = base / "shared-skip-test.py"
    fixture.write_text(
        "import pytest\n\n"
        "def test_will_skip():\n"
        "    pytest.skip('explicit skip must not be green')\n",
        encoding="utf-8",
    )
    manifest = nh.run_phases(
        run_dir,
        _phases({"name": "skipping", "command": [PYTHON, "-m", "pytest", str(fixture)], "kind": "pytest"}),
    )
    assert manifest["status"] == "failed"
    assert manifest["phases"]["skipping"]["status"] == "failed"
    assert manifest["phases"]["skipping"]["skipped"] == 1
    assert manifest["phases"]["skipping"]["diagnostic"]["code"] == "skipped_cases"


def test_candidate_change_during_run_invalidates_result(base, monkeypatch):
    run_dir = base / "next01-drift"
    nh.create_run("next01", run_dir)
    original = nh.candidate_fingerprint
    calls = {"n": 0}

    def drifting(root=None):
        calls["n"] += 1
        value = dict(original(root))
        if calls["n"] > 1:
            value["source_sha256"] = "0" * 64
        return value

    monkeypatch.setattr(nh, "candidate_fingerprint", drifting)
    manifest = nh.run_phases(
        run_dir,
        _phases({"name": "trivial", "command": [PYTHON, "-c", "print('ok')"], "kind": "command"}),
    )
    assert manifest["status"] == "failed"
    assert manifest["candidate"]["stable"] is False
    assert any(item["code"] == "candidate_changed" for item in manifest["diagnostics"])


def test_config_change_invalidates_verification(base, tmp_path):
    run_dir = base / "next01-config"
    config = tmp_path / "gate-config.json"
    config.write_text(json.dumps({"dsn": "postgresql://local/private", "window": 3}), encoding="utf-8")
    nh.create_run("next01", run_dir, config_paths=[config])
    manifest = nh.run_phases(
        run_dir,
        _phases({"name": "trivial", "command": [PYTHON, "-c", "print('ok')"], "kind": "command"}),
    )
    assert manifest["status"] == "passed"
    assert nh.verify(run_dir)["valid"] is True

    config.write_text(json.dumps({"dsn": "postgresql://local/private", "window": 4}), encoding="utf-8")
    result = nh.verify(run_dir)
    assert result["valid"] is False
    assert any(reason["code"] == "config_changed" for reason in result["reasons"])


def test_cleanup_refusal_is_visible(base, monkeypatch):
    run_dir = base / "next01-cleanup"
    nh.create_run("next01", run_dir)
    blocked = run_dir / "tmp-blocked"
    blocked.mkdir()
    nh.register_resource(run_dir, "temp_dir", path=blocked)

    real_remove = nh._remove_tree

    def refusing(path):
        if Path(path) == blocked:
            raise PermissionError("file is in use by another process")
        return real_remove(path)

    monkeypatch.setattr(nh, "_remove_tree", refusing)
    manifest = nh.run_phases(
        run_dir,
        _phases({"name": "trivial", "command": [PYTHON, "-c", "print('ok')"], "kind": "command"}),
    )
    assert manifest["status"] == "failed_cleanup"
    assert manifest["cleanup"]["status"] == "failed"
    assert manifest["cleanup"]["failures"][0]["code"] == "cleanup_denied"
    assert "in use" in manifest["cleanup"]["failures"][0]["reason"]
    assert blocked.exists()
    listed = {item["run_id"]: item for item in nh.status()}
    assert listed["next01-cleanup"]["state"] == "failed_cleanup"


def test_recover_stale_removes_only_owned_temp_resources(base):
    run_dir = base / "next01-stale"
    nh.create_run("next01", run_dir, pid=2**31 - 1)
    scratch = run_dir / "tmp-orphan"
    scratch.mkdir()
    nh.register_resource(run_dir, "temp_dir", path=scratch)
    nh.register_resource(run_dir, "database", identity="ak_next01_deadbeef")

    result = nh.recover(run_dir)

    assert result["status"] == "recovered"
    assert not scratch.exists()
    state = json.loads((run_dir / "ownership.json").read_text(encoding="utf-8"))
    assert state["state"] == "recovered"
    # A database identity is reported, never dropped or killed by the harness.
    assert any(item["kind"] == "database" for item in result["untouched"])


def test_recover_refuses_alive_or_foreign_run(base):
    alive = base / "next01-alive"
    nh.create_run("next01", alive)
    with pytest.raises(nh.HarnessError) as alive_error:
        nh.recover(alive)
    assert alive_error.value.code == "run_active"

    foreign = base / "other-task-run"
    (foreign / "evidence").mkdir(parents=True)
    (foreign / "keep.txt").write_text("do not touch", encoding="utf-8")
    with pytest.raises(nh.HarnessError) as foreign_error:
        nh.recover(foreign)
    assert foreign_error.value.code == "not_owned"
    assert (foreign / "keep.txt").is_file()

    listed = {item["run_id"]: item for item in nh.status()}
    assert listed["other-task-run"]["state"] == "foreign"


def test_paths_outside_harness_root_are_refused(base, tmp_path):
    for target in (tmp_path / "escape", base / ".." / "escape", REPO_ROOT / "tmp_test"):
        with pytest.raises(nh.HarnessError) as error:
            nh.create_run("next01", target)
        assert error.value.code in {"path_outside_root", "invalid_run_dir"}

    run_dir = base / "next01-paths"
    nh.create_run("next01", run_dir)
    with pytest.raises(nh.HarnessError) as error:
        nh.register_resource(run_dir, "temp_dir", path=tmp_path / "outside")
    assert error.value.code == "path_outside_run"


def test_secrets_are_removed_from_environment_and_logs(base, tmp_path):
    run_dir = base / "next01-secrets"
    secret = "sup3r-s3cret-dsn-value"
    config = tmp_path / "pg.json"
    config.write_text(json.dumps({"admin": f"postgresql://user:{secret}@127.0.0.1:25496/db"}), encoding="utf-8")
    nh.create_run("next01", run_dir, config_paths=[config])
    code = (
        "import os;"
        "print('DATABASE_URL=' + os.environ.get('DATABASE_URL', '<unset>'));"
        "print('PGPASSWORD=' + os.environ.get('PGPASSWORD', '<unset>'))"
    )
    manifest = nh.run_phases(
        run_dir,
        _phases({"name": "env", "command": [PYTHON, "-c", code], "kind": "command"}),
    )
    log = (run_dir / "evidence" / "env.log").read_text(encoding="utf-8")
    assert manifest["status"] == "passed"
    assert secret not in log
    assert "<unset>" in log
    assert secret not in (run_dir / "manifest.json").read_text(encoding="utf-8")
    private = list((run_dir / "config-private").iterdir())
    assert len(private) == 1


def test_initialisation_failure_leaves_no_directory(base, monkeypatch):
    run_dir = base / "next01-init-fail"

    def broken(path, payload):
        raise OSError("disk full")

    monkeypatch.setattr(nh, "_write_private_json", broken)
    with pytest.raises(OSError):
        nh.create_run("next01", run_dir)
    assert not run_dir.exists()


def test_force_killed_run_is_detected_and_recovered(base):
    run_dir = base / "next01-killed"
    process = subprocess.Popen(
        [PYTHON, str(Path(nh.__file__).resolve()), "hold", "--run-dir", str(run_dir)],
        cwd=str(REPO_ROOT),
        env={**os.environ, "NEXT_HARNESS_ROOT": str(base)},
    )
    try:
        ready = run_dir / "ready.json"
        deadline = time.time() + 30
        while not ready.is_file():
            if process.poll() is not None:
                raise AssertionError("hold process exited before it was ready")
            if time.time() > deadline:
                raise AssertionError("hold process did not become ready")
            time.sleep(0.1)
        process.kill()
        process.wait(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)

    listed = {item["run_id"]: item for item in nh.status()}
    assert listed["next01-killed"]["state"] == "stale"
    result = nh.recover(run_dir)
    assert result["status"] == "recovered"
    assert not (run_dir / "scratch").exists()
    assert (run_dir / "recovery.json").is_file()


def test_xfail_is_failure(base):
    run_dir = base / "next01-xfail"
    nh.create_run("next01", run_dir)
    fixture = base / "shared-xfail-test.py"
    fixture.write_text(
        """import pytest

@pytest.mark.xfail(reason='expected failure must not be green')
def test_will_xfail():
    assert False
""",
        encoding="utf-8",
    )
    manifest = nh.run_phases(
        run_dir,
        _phases({"name": "xfailing", "command": [PYTHON, "-m", "pytest", str(fixture)], "kind": "pytest"}),
    )
    assert manifest["status"] == "failed"
    assert manifest["phases"]["xfailing"]["status"] == "failed"
    assert manifest["phases"]["xfailing"]["skipped"] == 1


def test_collection_error_is_failure(base):
    run_dir = base / "next01-collect"
    nh.create_run("next01", run_dir)
    fixture = base / "shared-broken-test.py"
    fixture.write_text("""raise RuntimeError('collection exploded')
""", encoding="utf-8")
    manifest = nh.run_phases(
        run_dir,
        _phases({"name": "collecting", "command": [PYTHON, "-m", "pytest", str(fixture)], "kind": "pytest"}),
    )
    assert manifest["status"] == "failed"
    assert manifest["phases"]["collecting"]["status"] == "failed"
    assert manifest["phases"]["collecting"]["errors"] >= 1
    assert manifest["phases"]["collecting"]["diagnostic"]["code"] in {"failing_cases", "no_test_cases"}
