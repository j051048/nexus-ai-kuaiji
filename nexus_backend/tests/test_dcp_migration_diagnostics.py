"""Driver diagnostics must be actionable without exposing connection or row data."""

import pytest
from scripts import migrate_accounting as migration


def test_driver_failure_is_redacted_but_classified(monkeypatch, capsys):
    class DriverFailure(Exception):
        sqlstate = "42501"

    def fail(_):
        raise DriverFailure("postgresql://user:secret@example.test/db confidential invoice")

    monkeypatch.setenv("ACCOUNTING_ADMIN_DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(migration, "migrate", fail)
    monkeypatch.setattr("sys.argv", ["migration", "migrate"])
    assert migration.main() == 1
    output = capsys.readouterr().err
    assert "DriverFailure" in output and "42501" in output and "migrate" in output
    assert "secret" not in output and "confidential" not in output


def test_wrapped_migration_error_keeps_version_and_redacts_values():
    error = ValueError("private row and password")
    error.sqlstate = "secret:42501"
    text = migration.safe_diagnostic(migration.MigrationExecutionError(200, error), "migrate")
    assert "migration=200" in text and "ValueError" in text and "sqlstate=unknown" in text
    assert "password" not in text and "private row" not in text and "secret" not in text
