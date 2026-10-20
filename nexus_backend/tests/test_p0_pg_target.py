from scripts.check_pg_test_target import main


def test_pg_target_check_fails_without_explicit_dsns(monkeypatch):
    monkeypatch.delenv("ACCOUNTING_ADMIN_DATABASE_URL", raising=False)
    monkeypatch.delenv("ACCOUNTING_TEST_DATABASE_URL", raising=False)
    assert main() == 1
