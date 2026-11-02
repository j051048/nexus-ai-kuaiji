"""One pinned asset contract must survive download, bootstrap and bridge use."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_ci_and_runtime_use_same_identity_binary():
    from scripts.c01_local_wsl_bridge import AUTH_SHA

    # Official amd64 payload already verified by the current workflow download.
    assert AUTH_SHA == "c66b5efc816ccf3e13c24022bdefd5c6a9bea1fd1a1aeb9c586e1279c75a6306"


def test_manifest_is_single_source_and_rejects_unsupported_hosts():
    from scripts.identity_test_assets import asset_contract

    value = asset_contract(system="Linux", machine="x86_64")
    assert value == asset_contract(system="Linux", machine="amd64")
    assert value["release"] == "v2.197.0"
    with pytest.raises(ValueError, match="unsupported"):
        asset_contract(system="Linux", machine="aarch64")
    workflow = (ROOT / ".github/workflows/p01-recovery.yml").read_text()
    assert "identity_test_assets.py fetch" in workflow
    assert value["binary_sha256"] not in workflow


def test_bad_binary_and_archive_are_rejected_without_replacing_old_assets(tmp_path):
    from scripts.identity_test_assets import verify_binary, extract_verified

    binary = tmp_path / "auth"
    binary.write_bytes(b"existing evidence")
    archive = tmp_path / "fake.tar.xz"
    archive.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="digest"):
        verify_binary(binary)
    with pytest.raises(ValueError, match="digest"):
        extract_verified(archive, binary)
    assert binary.read_bytes() == b"existing evidence"


def test_setup_failure_report_is_redacted_and_cannot_claim_success(tmp_path):
    from scripts.qa_diagnostics import write_failure

    target = tmp_path / "failure.json"
    write_failure(
        target, phase="bootstrap", error=RuntimeError("postgresql://secret:password@host")
    )
    data = json.loads(target.read_text())
    assert data["status"] == "failed" and data["phase"] == "bootstrap"
    assert data["exception"] == "RuntimeError"
    assert "password" not in target.read_text()
    with pytest.raises(FileExistsError):
        write_failure(target, phase="identity", error=ValueError("don't overwrite"))
