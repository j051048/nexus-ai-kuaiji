import pytest

from app.jobs.restore_hold import enforce_restore_hold
from test_accounting import service


def test_restore_hold_blocks_worker_start_when_marker_exists(tmp_path, monkeypatch):
    marker = tmp_path / "restore-verification.json"
    marker.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ACCOUNTING_RESTORE_HOLD_FILE", str(marker))
    with pytest.raises(SystemExit, match="隔离核对"):
        enforce_restore_hold()


def test_restore_hold_allows_normal_start_without_marker(monkeypatch, tmp_path):
    monkeypatch.delenv("ACCOUNTING_RESTORE_HOLD_FILE", raising=False)
    enforce_restore_hold(database_path=tmp_path / "normal.db")
    monkeypatch.setenv("ACCOUNTING_RESTORE_HOLD_FILE", str(tmp_path / "missing.json"))
    with pytest.raises(SystemExit, match="不存在"):
        enforce_restore_hold(database_path=tmp_path / "normal.db")


def test_database_restore_hold_blocks_and_can_be_released(service):
    with service.db.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO restore_state(instance_id,hold,restore_id,reason,updated_by,updated_at) "
            "VALUES('restored',1,'r1','隔离恢复','system','2026-10-18')"
        )
    with pytest.raises(SystemExit, match="恢复隔离"):
        service.db.assert_restore_hold()
    with service.db.transaction(write=True) as conn:
        conn.execute("UPDATE restore_state SET hold=0 WHERE instance_id='restored'")
    service.db.assert_restore_hold()


def test_restore_hold_detects_marker_from_restored_database_path(tmp_path, monkeypatch):
    monkeypatch.delenv("ACCOUNTING_RESTORE_HOLD_FILE", raising=False)
    (tmp_path / "restore-verification.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="恢复验证标记"):
        enforce_restore_hold(database_path=tmp_path / "database.sqlite")


def test_api_startup_refuses_restored_directory_without_env_marker(tmp_path, monkeypatch):
    from app.main import create_app

    monkeypatch.delenv("ACCOUNTING_RESTORE_HOLD_FILE", raising=False)
    (tmp_path / "restore-verification.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="恢复验证标记"):
        create_app(tmp_path / "database.sqlite", seed=False)
