"""PLT06: cross-major pg_restore must refuse before touching the target."""

import subprocess

import pytest

from scripts import backup_accounting as recovery


def test_equal_or_newer_target_is_supported():
    assert recovery.restore_version_conflict(17, 17, 17) is None
    assert recovery.restore_version_conflict(17, 16, 17) is None
    # A PG16 dump into a PG15 target is still a downgrade and must be refused.
    assert recovery.restore_version_conflict(17, 16, 15)


def test_dump_newer_than_target_is_refused_with_reason():
    reason = recovery.restore_version_conflict(17, 17, 16)
    assert reason and "17" in reason and "16" in reason
    assert "transaction_timeout" in reason
    assert "不得用过滤选项绕过" in reason


def test_client_older_than_dump_is_refused():
    reason = recovery.restore_version_conflict(16, 17, 17)
    assert reason and "pg_restore" in reason


def test_unknown_source_version_falls_back_to_client_check():
    assert recovery.restore_version_conflict(17, None, 16) is None
    assert recovery.restore_version_conflict(15, 16, 17)


def test_tool_major_version_parses_client_output(monkeypatch):
    monkeypatch.setattr(recovery.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        recovery.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "pg_restore (PostgreSQL) 16.4\n", ""),
    )
    assert recovery.tool_major_version("pg_restore") == 16


def test_tool_major_version_rejects_unparseable_output(monkeypatch):
    monkeypatch.setattr(recovery.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        recovery.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "unknown build\n", ""),
    )
    with pytest.raises(ValueError, match="无法识别"):
        recovery.tool_major_version("pg_restore")


def test_unreadable_archive_is_refused_not_treated_as_current(monkeypatch):
    monkeypatch.setattr(recovery.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        recovery.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "unsupported version"),
    )
    with pytest.raises(ValueError, match="无法读取该归档"):
        recovery.dump_source_major("dump.bin", "postgresql://127.0.0.1:5432/x")


@pytest.mark.parametrize(
    "text,expected", [("Dumped from database version 17.2", 17), ("no header", None)]
)
def test_dump_source_major_reads_archive_header(monkeypatch, text, expected):
    monkeypatch.setattr(recovery.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        recovery.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, text, ""),
    )
    assert recovery.dump_source_major("dump.bin", "postgresql://127.0.0.1:5432/x") == expected
