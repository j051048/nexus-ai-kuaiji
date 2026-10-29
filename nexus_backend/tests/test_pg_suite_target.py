"""N02/N03 must share the explicit CI-safe, restricted PostgreSQL target."""

from contextlib import contextmanager

import pytest

from scripts.ak05_postgres_gate import GateError


ENV_KEYS = {
    "admin": "ACCOUNTING_TEST_POSTGRES_URL",
    "app": "ACCOUNTING_TEST_DATABASE_URL",
    "worker": "ACCOUNTING_TEST_WORKER_DATABASE_URL",
    "projection": "ACCOUNTING_PROJECTION_DATABASE_URL",
}


def config():
    return {
        key: f"postgresql://{user}:local-test-only@127.0.0.1:55432/ak01_local_ak05_n23_test"
        for key, user in {
            "admin": "postgres",
            "app": "ak05_runtime",
            "worker": "ak05_worker",
            "projection": "ak05_projection",
        }.items()
    }


def environment(monkeypatch, target=None):
    for name in ENV_KEYS.values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ACCOUNTING_AK05_ISOLATED_TARGET", "1")
    if target:
        for key, value in target.items():
            monkeypatch.setenv(ENV_KEYS[key], value)


class FakeConnection:
    def execute(self, sql, params=None):
        if "server_version_num" in sql:
            self.result = (170011,)
        else:
            self.result = (False, False, False, False)
        return self

    def fetchone(self):
        return self.result


@contextmanager
def fake_connect(*args, **kwargs):
    yield FakeConnection()


class FakeDatabase:
    def __init__(self, target):
        self.target = target

    def close(self):
        pass


def next_fixture(generator):
    try:
        return next(generator)
    except pytest.skip.Exception as failure:
        raise AssertionError("Required N02/N03 gate silently skipped") from failure


def test_n03_accepts_explicit_ci_port_before_any_database_work(monkeypatch):
    import psycopg
    import test_n03_notification_postgres as suite
    import scripts.ak05_postgres_gate as gate
    import scripts.migrate_accounting as migrations

    target = config()
    environment(monkeypatch, target)
    monkeypatch.setattr(psycopg, "connect", fake_connect)
    monkeypatch.setattr(gate, "assert_server_and_roles", lambda *args, **kwargs: 170011)
    monkeypatch.setattr(migrations, "migrate", lambda value: [])
    monkeypatch.setattr(suite, "Database", FakeDatabase)
    generator = suite.p0_pg.__wrapped__()
    value = next_fixture(generator)
    assert value.admin == target["admin"]
    assert value.worker_url == target["worker"]
    generator.close()


@pytest.mark.parametrize("name", ["test_tax_workpaper_postgres", "test_n03_notification_postgres"])
def test_required_gate_missing_configuration_fails_without_skip_or_migrate(monkeypatch, name):
    import importlib
    import scripts.migrate_accounting as migrations

    suite = importlib.import_module(name)
    environment(monkeypatch)
    calls = []

    def unexpected_migrate(value):
        calls.append(value)
        raise AssertionError("Database mutation attempted before target validation")

    monkeypatch.setattr(migrations, "migrate", unexpected_migrate)
    fixture = suite.pg if name == "test_tax_workpaper_postgres" else suite.p0_pg
    with pytest.raises(GateError):
        next_fixture(fixture.__wrapped__())
    assert calls == []


@pytest.mark.parametrize("port", [55432, 25531, 25532, 29001])
def test_valid_explicit_local_target_is_not_tied_to_one_developer_port(port):
    from scripts.pg_suite_target import load_suite_target

    target = {key: value.replace(":55432/", f":{port}/") for key, value in config().items()}
    env = {ENV_KEYS[key]: value for key, value in target.items()}
    env["ACCOUNTING_AK05_ISOLATED_TARGET"] = "1"
    assert load_suite_target(env) == target


def test_no_runner_and_no_database_is_optional_offline():
    from scripts.pg_suite_target import load_suite_target

    assert load_suite_target({}) is None


@pytest.mark.parametrize("missing", [None, "admin", "app", "worker", "projection", "flag"])
def test_required_and_partial_environment_cannot_silently_skip(missing):
    from scripts.pg_suite_target import load_suite_target

    env = {ENV_KEYS[key]: value for key, value in config().items()}
    env["ACCOUNTING_AK05_ISOLATED_TARGET"] = "1"
    if missing is None:
        env = {"ACCOUNTING_AK05_ISOLATED_TARGET": "1"}
    elif missing == "flag":
        del env["ACCOUNTING_AK05_ISOLATED_TARGET"]
    else:
        del env[ENV_KEYS[missing]]
    with pytest.raises(GateError):
        load_suite_target(env)


@pytest.mark.parametrize(
    "old,new",
    [
        ("127.0.0.1", "db.example.test"),
        ("127.0.0.1", "localhost"),
        (":55432/", ":5432/"),
        ("ak01_local_ak05_n23_test", "accounting_test"),
        ("ak01_local_ak05_n23_test", "n03_local_old"),
        ("ak01_local_ak05_n23_test", "ak01_local_ak05_n23%2Fother"),
        ("ak01_local_ak05_n23_test", "ak01_local_ak05_n23_test?host=remote"),
        ("ak01_local_ak05_n23_test", "ak01_local_ak05_n23_test#other"),
        ("ak05_worker", "postgres"),
    ],
)
def test_remote_default_ambiguous_or_privileged_target_is_rejected(old, new):
    from scripts.pg_suite_target import load_suite_target

    env = {ENV_KEYS[key]: value.replace(old, new) for key, value in config().items()}
    env["ACCOUNTING_AK05_ISOLATED_TARGET"] = "1"
    with pytest.raises(GateError) as caught:
        load_suite_target(env)
    assert "local-test-only" not in str(caught.value)


def test_all_identities_must_point_to_same_database():
    from scripts.pg_suite_target import load_suite_target

    env = {ENV_KEYS[key]: value for key, value in config().items()}
    env["ACCOUNTING_AK05_ISOLATED_TARGET"] = "1"
    env[ENV_KEYS["worker"]] = env[ENV_KEYS["worker"]].replace("n23_test", "n23_other")
    with pytest.raises(GateError):
        load_suite_target(env)


@pytest.mark.parametrize("name", ["test_tax_workpaper_postgres", "test_n03_notification_postgres"])
def test_server_guard_runs_before_fixture_migration(monkeypatch, name):
    import importlib
    import scripts.ak05_postgres_gate as gate
    import scripts.migrate_accounting as migrations

    suite = importlib.import_module(name)
    environment(monkeypatch, config())
    monkeypatch.setattr(gate, "local_connection", fake_connect)

    def bad_roles(*args, **kwargs):
        raise GateError("Restricted role/durability guard rejected the database")

    monkeypatch.setattr(gate, "assert_server_and_roles", bad_roles)
    calls = []
    monkeypatch.setattr(migrations, "migrate", lambda target: calls.append(target))
    fixture = suite.pg if name == "test_tax_workpaper_postgres" else suite.p0_pg
    with pytest.raises(GateError, match="Restricted role/durability"):
        next_fixture(fixture.__wrapped__())
    assert calls == []


@pytest.mark.parametrize("major", [16, 17])
def test_actual_major_and_restricted_role_guard_are_reused(monkeypatch, major):
    from scripts.pg_suite_target import verify_suite_target
    import scripts.ak05_postgres_gate as gate

    class VersionConnection(FakeConnection):
        def execute(self, sql, params=None):
            self.result = (major * 10000 + 11,)
            return self

    @contextmanager
    def connection(*args, **kwargs):
        yield VersionConnection()

    calls = []
    monkeypatch.setattr(gate, "local_connection", connection)
    monkeypatch.setattr(gate, "assert_server_and_roles", lambda *args: calls.append(args))
    assert verify_suite_target(config()) == config()
    assert calls == [(config(), major)]


@pytest.mark.parametrize("major", [14, 15, 18])
def test_unsupported_actual_major_fails_before_role_queries(monkeypatch, major):
    from scripts.pg_suite_target import verify_suite_target
    import scripts.ak05_postgres_gate as gate

    class VersionConnection(FakeConnection):
        def execute(self, sql, params=None):
            self.result = (major * 10000,)
            return self

    @contextmanager
    def connection(*args, **kwargs):
        yield VersionConnection()

    calls = []
    monkeypatch.setattr(gate, "local_connection", connection)
    monkeypatch.setattr(gate, "assert_server_and_roles", lambda *args: calls.append(args))
    with pytest.raises(GateError, match="actual PostgreSQL 16 or 17"):
        verify_suite_target(config())
    assert calls == []


def test_connection_errors_do_not_expose_credentials(monkeypatch):
    from scripts.pg_suite_target import verify_suite_target
    import scripts.ak05_postgres_gate as gate

    def failure(*args, **kwargs):
        raise RuntimeError("Driver connection error: " + config()["admin"])

    monkeypatch.setattr(gate, "local_connection", failure)
    with pytest.raises(GateError) as caught:
        verify_suite_target(config())
    assert "local-test-only" not in str(caught.value)
    assert "postgresql://" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("name", ["test_tax_workpaper_postgres", "test_n03_notification_postgres"])
def test_ordinary_offline_fixture_still_skips_without_touching_a_database(monkeypatch, name):
    import importlib
    import scripts.migrate_accounting as migrations

    suite = importlib.import_module(name)
    environment(monkeypatch)
    monkeypatch.delenv("ACCOUNTING_AK05_ISOLATED_TARGET")
    calls = []
    monkeypatch.setattr(migrations, "migrate", lambda target: calls.append(target))
    fixture = suite.pg if name == "test_tax_workpaper_postgres" else suite.p0_pg
    with pytest.raises(pytest.skip.Exception):
        next(fixture.__wrapped__())
    assert calls == []
