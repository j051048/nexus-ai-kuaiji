"""Local test bridge must not become a generic process/DSN launcher."""

from pathlib import Path

import pytest

from scripts.c01_local_wsl_bridge import (
    Bridge,
    BridgeError,
    confined_path,
    database_target,
    validate_snapshot,
)


def test_only_explicit_local_cluster_and_auth_derived_database():
    base = {
        "url": "postgresql://local:secret@127.0.0.1:25498/ak01_local_ak05_test",
        "binary_directory": "/private/bin",
    }
    assert database_target(base, "ak01_local_ak05_auth_" + "a" * 32)["PGDATABASE"].endswith(
        "a" * 32
    )
    for change in (
        "postgresql://u:p@remote:25498/ak01_local_ak05_test",
        "postgresql://u:p@127.0.0.1:5432/ak01_local_ak05_test",
    ):
        with pytest.raises(BridgeError):
            database_target({**base, "url": change})
    with pytest.raises(BridgeError):
        database_target(base, "postgres")


def test_paths_cannot_escape_private_artifact_root(tmp_path):
    allowed = tmp_path / "private"
    allowed.mkdir()
    assert confined_path(str(allowed / "dump"), [str(allowed)]) == allowed / "dump"
    with pytest.raises(BridgeError):
        confined_path(str(allowed / ".." / "outside"), [str(allowed)])
    with pytest.raises(BridgeError):
        confined_path(str(tmp_path / "sibling"), [str(allowed)])


def test_snapshot_is_a_pg_identifier_not_an_extra_command():
    assert validate_snapshot("00000004-000000AF-1") == "00000004-000000AF-1"
    for value in ("--file=/etc/passwd", "a b", "", "1;drop", 3):
        with pytest.raises(BridgeError):
            validate_snapshot(value)


def test_unknown_operation_and_extra_argv_never_launch_subprocess(tmp_path, monkeypatch):
    import json
    import subprocess

    config = tmp_path / "private.json"
    config.write_text(json.dumps({"token": "a" * 64}))
    bridge = Bridge(config)

    def forbidden(*args, **kwargs):
        raise AssertionError("Rejected operation reached a process launcher")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    for request in (
        {"op": "shell", "command": "whoami"},
        {"op": "pg_dump", "args": ["--file=/tmp/x"]},
        {"op": "start_gotrue", "env": {"PATH": "/evil"}},
    ):
        with pytest.raises(BridgeError):
            bridge.execute(request)


def test_stop_only_targets_processes_owned_by_bridge(tmp_path):
    import json

    config = tmp_path / "private.json"
    config.write_text(json.dumps({"token": "a" * 64}))
    bridge = Bridge(config)
    with pytest.raises(BridgeError):
        bridge.execute({"op": "stop_gotrue", "port": 5432})
    assert bridge.execute({"op": "stop_gotrue", "port": 25512}) == {"stopped": True, "port": 25512}
