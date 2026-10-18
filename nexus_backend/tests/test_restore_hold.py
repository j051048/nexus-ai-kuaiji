import pytest

from app.jobs.restore_hold import enforce_restore_hold


def test_restore_hold_blocks_worker_start_when_marker_exists(tmp_path, monkeypatch):
    marker = tmp_path / "restore-verification.json"
    marker.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ACCOUNTING_RESTORE_HOLD_FILE", str(marker))
    with pytest.raises(SystemExit, match="隔离核对"):
        enforce_restore_hold()


def test_restore_hold_allows_normal_start_without_marker(monkeypatch, tmp_path):
    monkeypatch.delenv("ACCOUNTING_RESTORE_HOLD_FILE", raising=False)
    enforce_restore_hold()
    monkeypatch.setenv("ACCOUNTING_RESTORE_HOLD_FILE", str(tmp_path / "missing.json"))
    enforce_restore_hold()
