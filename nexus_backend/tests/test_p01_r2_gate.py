"""Failures first: portable, immutable and complete P01 acceptance contracts."""

import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from scripts import p01_local_recovery_env as env
from scripts import p01_recovery_gate as gate


def configs(directory):
    directory.mkdir()
    for name in gate.configuration_names():
        (directory / name).write_text(json.dumps({"synthetic": name}))
    return directory


def test_run_snapshot_is_private_independent_and_never_overwritten(tmp_path):
    source = configs(tmp_path / "source")
    destination = tmp_path / "evidence" / "config-private"
    before = gate.freeze_configs(source, destination)
    (source / "pg16-recovery_source.json").write_text('{"changed":true}')
    assert gate.configuration_fingerprint(destination) == before
    with pytest.raises(gate.GateError):
        gate.freeze_configs(source, destination)
    assert "synthetic" not in str(before)


def test_snapshot_rejects_missing_or_linked_config(tmp_path):
    source = configs(tmp_path / "source")
    (source / "wsl-bridge-private.json").unlink()
    with pytest.raises(gate.GateError):
        gate.freeze_configs(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_pipeline_requires_all_three_phases_same_candidate_and_config():
    candidate = {"source_sha256": "a" * 64}
    binding = {"x": "b" * 64}
    phases = {
        name: {"passed": 2, "candidate": candidate, "configuration_sha256": binding}
        for name in ("regression", "joint", "identity")
    }
    gate.validate_phase_bindings(phases, candidate, binding)
    for name in phases:
        with pytest.raises(gate.GateError):
            gate.validate_phase_bindings(
                {k: v for k, v in phases.items() if k != name}, candidate, binding
            )
    with pytest.raises(gate.GateError):
        gate.validate_phase_bindings(
            {**phases, "regression": {**phases["regression"], "candidate": {}}}, candidate, binding
        )


def test_timeout_and_error_diagnostics_are_bounded_and_secret_free():
    assert gate.validate_timeout(3600) == 3600
    for bad in (True, 0, -1, 86401, "3600"):
        with pytest.raises(gate.GateError):
            gate.validate_timeout(bad)
    diagnostic = gate.error_diagnostic(RuntimeError("postgresql://a:secret@host/db"), "joint")
    assert diagnostic["phase"] == "joint" and diagnostic["code"]
    assert "secret" not in json.dumps(diagnostic)


def test_minimum_schema_is_explicit_not_a_missing_column_traceback():
    with pytest.raises(gate.GateError, match="190"):
        gate.require_migration_floor([("170_old.sql", "已应用")])
    gate.require_migration_floor([("173_tax.sql", "已应用"), ("190_review.sql", "已应用")])


def test_environment_paths_are_user_relative_and_plan_is_immutable(tmp_path):
    plan = env.make_plan(tmp_path / "runtime", tmp_path / "pg", tmp_path / "auth")
    assert Path(plan["root"]).parent == (tmp_path / "runtime").resolve()
    assert "/home/flydao" not in json.dumps(plan)
    assert plan["schema"] == "p01-environment-v2"
    assert set(plan["clusters"]) == set(env.LAYOUT)
    path = tmp_path / "private.json"
    env.write_new_private(path, plan)
    with pytest.raises(env.RecoveryEnvironmentError):
        env.write_new_private(path, {"replacement": True})
    assert json.loads(path.read_text()) == plan


def test_owned_cluster_rejects_wrong_marker_version_and_port_before_process(tmp_path):
    plan = env.make_plan(tmp_path, tmp_path / "pg", tmp_path / "auth")
    root = Path(plan["root"])
    data = root / "source16" / "data"
    data.mkdir(parents=True)
    (root / "owner.json").write_text(json.dumps({"environment_id": plan["environment_id"]}))
    (data / "PG_VERSION").write_text("16")
    (data / "postgresql.conf").write_text("port=25496\nlisten_addresses='127.0.0.1'\n")
    env.validate_owned_cluster(plan, "source16")
    (data / "PG_VERSION").write_text("17")
    with pytest.raises(env.RecoveryEnvironmentError):
        env.validate_owned_cluster(plan, "source16")
    (data / "PG_VERSION").write_text("16")
    (root / "owner.json").write_text('{"environment_id":"another-task"}')
    with pytest.raises(env.RecoveryEnvironmentError):
        env.validate_owned_cluster(plan, "source16")


def test_ci_requires_full_recovery_and_only_uploads_public_reports():
    workflow = Path(".github/workflows/p01-recovery.yml").read_text(encoding="utf-8")
    assert "p01_recovery_gate.py --all" in workflow
    assert "new-pair" in workflow and "stop" in workflow
    assert "if: always()" in workflow
    assert "reports/" in workflow
    assert "path: tmp_test/**" not in workflow


def phase_evidence(tmp_path):
    function = "test_fresh_cluster_database_objects_signed_identity_and_http_recovery"
    module = "test_c01_full_recovery_postgres"
    cases = [
        {
            "nodeid": f"nexus_backend/tests/{module}.py::{function}[postgres-{major}]",
            "function": function,
            "module": module,
            "major": major,
        }
        for major in (16, 17)
    ]
    value = {
        "schema": "p01-pytest-v1",
        "exitstatus": 0,
        "deselected": 0,
        "collected": cases,
        "events": [
            {"nodeid": c["nodeid"], "when": stage, "outcome": "passed", "wasxfail": False}
            for c in cases
            for stage in ("setup", "call", "teardown")
        ],
    }
    execution, junit = tmp_path / "execution.json", tmp_path / "junit.xml"
    execution.write_text(json.dumps(value))
    tree = ET.Element("testsuite", tests="2")
    for case in cases:
        ET.SubElement(
            tree,
            "testcase",
            classname=f"nexus_backend.tests.{module}",
            name=case["nodeid"].split("::")[1],
        )
    ET.ElementTree(tree).write(junit)
    return execution, junit, value, tree


def test_semantic_version_identity_accepts_new_parameter_labels(tmp_path):
    execution, junit, _, _ = phase_evidence(tmp_path)
    assert gate.validate_phase_evidence(execution, junit, "joint")["passed"] == 2


@pytest.mark.parametrize(
    "defect",
    [
        "skip",
        "xfail",
        "teardown",
        "duplicate",
        "deselected",
        "wrong_major",
        "wrong_module",
        "junit_counter",
    ],
)
def test_phase_cannot_pass_with_missing_execution_or_forged_evidence(tmp_path, defect):
    execution, junit, value, tree = phase_evidence(tmp_path)
    if defect == "skip":
        value["events"][0]["outcome"] = "skipped"
    elif defect == "xfail":
        value["events"][1]["wasxfail"] = True
    elif defect == "teardown":
        value["events"].pop()
    elif defect == "duplicate":
        value["events"].append(value["events"][0])
    elif defect == "deselected":
        value["deselected"] = 1
    elif defect == "wrong_major":
        value["collected"][0]["major"] = 14
    elif defect == "wrong_module":
        value["collected"][0]["module"] = "forged"
    else:
        tree.set("errors", "1")
        ET.ElementTree(tree).write(junit)
    execution.write_text(json.dumps(value))
    with pytest.raises(gate.GateError):
        gate.validate_phase_evidence(execution, junit, "joint")


def test_bridge_rejects_config_drift_before_any_operation(tmp_path):
    from scripts.c01_local_wsl_bridge import Bridge, BridgeError

    path = tmp_path / "bridge.json"
    path.write_text(json.dumps({"token": "a" * 64}))
    bridge = Bridge(path)
    assert bridge.execute({"op": "status"})["configuration_sha256"]
    path.write_text(json.dumps({"token": "a" * 64, "targets": "replaced"}))
    with pytest.raises(BridgeError, match="configuration changed"):
        bridge.execute({"op": "status"})


def test_gate_configuration_can_only_be_claimed_once(tmp_path):
    directory = tmp_path / "run"
    directory.mkdir()
    with gate.claim_run(directory):
        assert (directory / "gate-active.json").exists()
        with pytest.raises(gate.GateError):
            with gate.claim_run(directory):
                pytest.fail("Two gates claimed the same restore target")
    assert not (directory / "gate-active.json").exists()
    assert (directory / "gate-claim.json").exists()
    with pytest.raises(gate.GateError):
        with gate.claim_run(directory):
            pytest.fail("Old restore target reused")


def test_long_home_paths_do_not_prevent_postgres_starting():
    # Linux Unix socket names are limited to 107 bytes. All fixtures use TCP;
    # immutable UUID roots must not accidentally require a short user's home.
    settings = env.postgres_settings(25496)
    assert "unix_socket_directories=''" in settings
    assert "listen_addresses='127.0.0.1'" in settings
    assert "fsync=on" in settings and "synchronous_commit=on" in settings


def test_lifecycle_refuses_active_gate_before_any_process(tmp_path, monkeypatch):
    directory = tmp_path / "env"
    run = directory / "runs/first"
    run.mkdir(parents=True)
    (run / "gate-active.json").write_text("{}")
    monkeypatch.setattr(env, "_native", lambda: None)
    monkeypatch.setattr(env, "read_plan", lambda _: {})
    with pytest.raises(env.RecoveryEnvironmentError, match="gate_active"):
        env.lifecycle(directory, "stop")


def test_second_pair_reuses_complete_login_set_without_reprovisioning():
    assert env.needs_login_provision(set()) is True
    assert env.needs_login_provision({"ak05_runtime", "ak05_worker", "ak05_projection"}) is False
    for names in ({"ak05_runtime"}, {"ak05_runtime", "unexpected_admin"}):
        with pytest.raises(env.RecoveryEnvironmentError, match="role_inventory"):
            env.needs_login_provision(names)


def test_failed_regression_cannot_be_combined_with_prior_recovery(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from scripts import migrate_accounting

    sys.path.insert(0, str(gate.ROOT / "nexus_backend/tests"))
    import c01_wsl_bridge

    (tmp_path / "tmp_test").mkdir()
    source = configs(tmp_path / "tmp_test" / "source")
    binding = gate.configuration_fingerprint(source)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "candidate_fingerprint", lambda: {"source_sha256": "f" * 64})
    monkeypatch.setattr(gate, "validate_config", lambda _: {"admin": "synthetic-local-only"})
    monkeypatch.setattr(gate, "assert_server_and_roles", lambda *_: None)
    monkeypatch.setattr(migrate_accounting, "status", lambda _: [(190, "已应用")])
    monkeypatch.setattr(
        c01_wsl_bridge,
        "call",
        lambda *_: {"configuration_sha256": binding["wsl-bridge-private.json"]},
    )
    attempts = []

    def fail_process(*args, **kwargs):
        attempts.append(args)
        return SimpleNamespace(returncode=1, stdout="synthetic failure", stderr="")

    monkeypatch.setattr(gate.subprocess, "run", fail_process)
    # Mocking subprocess must not replace the OS ACL helper for the test itself.
    import c01_identity_fixture

    monkeypatch.setattr(c01_identity_fixture, "_secure", lambda _: None)
    monkeypatch.setattr(env, "secure", lambda _: None)
    out = tmp_path / "tmp_test" / "result"
    assert not gate.run_gate(out, config_dir=source)
    result = json.loads((out / "reports/summary.json").read_text())
    assert result["status"] == "failed" and result["phases"] == {}
    assert result["diagnostic"]["phase"] == "regression"
    assert len(attempts) == 1
    assert "local_regression" not in result
