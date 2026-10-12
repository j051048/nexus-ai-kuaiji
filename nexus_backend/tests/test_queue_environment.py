"""The worker cannot silently load legacy configuration or run production on SQLite."""
import pytest

from app.jobs import worker


@pytest.mark.parametrize("url", ["test-only.db", "postgresql://worker@localhost/test", "postgresql://worker@localhost/test?sslmode=disable"])
def test_production_worker_requires_postgres_tls_before_connecting(monkeypatch, tmp_path, capsys, url):
    monkeypatch.setattr("sys.argv", ["worker", "--once"])
    monkeypatch.setenv("ACCOUNTING_ENV", "production")
    monkeypatch.setenv("ACCOUNTING_WORKER_DATABASE_URL", url)
    monkeypatch.setenv("ACCOUNTING_ATTACHMENT_ROOT", str(tmp_path))
    with pytest.raises(SystemExit) as stopped:
        worker.main()
    assert stopped.value.code == 2
    assert "生产worker" in capsys.readouterr().err
    assert not (tmp_path / "test-only.db").exists()


def test_worker_does_not_fall_back_to_api_database_configuration(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["worker", "--once"])
    monkeypatch.delenv("ACCOUNTING_WORKER_DATABASE_URL", raising=False)
    monkeypatch.setenv("ACCOUNTING_DATABASE_URL", "postgresql://api:private-placeholder@localhost/api")
    with pytest.raises(SystemExit) as stopped:
        worker.main()
    assert stopped.value.code == 2
    output = capsys.readouterr().err
    assert "ACCOUNTING_WORKER_DATABASE_URL" in output and "private-placeholder" not in output


def test_only_explicit_env_file_is_considered(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("sys.argv", ["worker", "--once", "--env-file", str(tmp_path / "missing-new-config")])
    with pytest.raises(SystemExit) as stopped:
        worker.main()
    assert stopped.value.code == 2 and "配置文件不存在" in capsys.readouterr().err


@pytest.mark.parametrize("kwargs", [{"concurrency": 3}, {"lease_seconds": 29}, {"poll_seconds": 0}])
def test_worker_bounds_checked_before_resources_are_opened(kwargs):
    with pytest.raises(ValueError):
        worker.QueueWorker(None, **kwargs)


def test_real_cli_once_initializes_only_an_isolated_local_queue(tmp_path):
    import os
    from pathlib import Path
    import sqlite3
    import subprocess
    import sys
    path = tmp_path / "worker-only.db"
    environment = {**os.environ, "ACCOUNTING_ENV": "local", "APP_ENV": "test", "ENV": "test", "ENVIRONMENT": "test",
                   "ACCOUNTING_WORKER_DATABASE_URL": str(path), "ACCOUNTING_ATTACHMENT_ROOT": str(tmp_path / "private"),
                   "ACCOUNTING_QUEUE_CONCURRENCY": "1", "ACCOUNTING_QUEUE_LEASE_SECONDS": "180"}
    result = subprocess.run([sys.executable, "scripts/run_accounting_worker.py", "--once"], cwd=Path(__file__).resolve().parents[2], env=environment, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 6
        assert conn.execute("SELECT COUNT(*) FROM ocr_jobs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
