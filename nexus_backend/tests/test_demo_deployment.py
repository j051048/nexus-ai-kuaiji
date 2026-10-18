"""Deployment routing and preflight safety, without cloud credentials/services."""

from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_demo_deployment as preflight
import run_accounting_service as runner


def configured(role="api"):
    env = {
        "ACCOUNTING_ENV": "production", "ACCOUNTING_SERVICE_ROLE": role,
        "ACCOUNTING_AUTH_MODE": "supabase", "ACCOUNTING_SEED": "0", "ACCOUNTING_QUEUE_EMBEDDED_WORKER": "0",
        "ACCOUNTING_STORAGE_BACKEND": "supabase", "ACCOUNTING_STORAGE_SUPABASE_URL": "https://demo.supabase.co",
        "ACCOUNTING_STORAGE_BUCKET": "accounting-originals", "ACCOUNTING_STORAGE_SERVER_KEY": "sb_secret_SYNTHETIC_STORAGE_SECRET",
        "ACCOUNTING_SUPABASE_URL": "https://demo.supabase.co", "ACCOUNTING_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_SYNTHETIC",
        "ACCOUNTING_ALLOWED_HOSTS": "demo-api.zeabur.app", "ACCOUNTING_ALLOWED_ORIGINS": "https://demo.vercel.app",
        "ACCOUNTING_AI_OCR_BASE_URL": "https://synthetic-vision.example/v1", "ACCOUNTING_AI_OCR_API_KEY": "SYNTHETIC_OCR_SECRET",
        "ACCOUNTING_AI_OCR_MODEL": "synthetic-vision", "ACCOUNTING_AI_JOURNAL_BASE_URL": "https://synthetic-journal.example/v1",
        "ACCOUNTING_AI_JOURNAL_API_KEY": "SYNTHETIC_JOURNAL_SECRET", "ACCOUNTING_AI_JOURNAL_MODEL": "synthetic-journal",
        "ACCOUNTING_AI_CHAT_BASE_URL": "https://synthetic-chat.example/v1", "ACCOUNTING_AI_CHAT_API_KEY": "SYNTHETIC_CHAT_SECRET",
        "ACCOUNTING_AI_CHAT_MODEL": "synthetic-chat",
    }
    env["ACCOUNTING_DATABASE_URL" if role == "api" else "ACCOUNTING_WORKER_DATABASE_URL"] = "postgresql://synthetic:VERY_SECRET_PASSWORD@db.example/postgres?sslmode=require"
    return env


@pytest.mark.parametrize("role", runner.ROLES)
def test_each_role_uses_one_restricted_database_config(role):
    assert runner.validate_runtime(role, configured(role)) == 8080


def test_runtime_image_uses_runtime_lock_and_includes_maintenance_consumer():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    lock = (ROOT / "nexus_backend" / "requirements.runtime.lock").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.demo.yml").read_text(encoding="utf-8")
    assert "requirements.runtime.lock" in dockerfile
    assert "pytest==" not in lock and "ruff==" not in lock
    assert "maintenance:" in compose
    assert "ACCOUNTING_SERVICE_ROLE: maintenance" in compose
    assert "maintenance" in runner.ROLES


@pytest.mark.parametrize("key,value", [
    ("ACCOUNTING_ADMIN_DATABASE_URL", "postgresql://postgres:ADMIN_SECRET@db.example/postgres"),
    ("ACCOUNTING_WORKER_DATABASE_URL", "postgresql://worker:WORKER_SECRET@db.example/postgres"),
    ("ACCOUNTING_STORAGE_BACKEND", "local"), ("ACCOUNTING_ENV", "development"),
    ("ACCOUNTING_AUTH_MODE", "local"), ("ACCOUNTING_QUEUE_EMBEDDED_WORKER", "1"),
    ("ACCOUNTING_SEED", "1"), ("PORT", "-1"), ("PORT", "not-a-port"),
    ("ACCOUNTING_DATABASE_URL", "postgresql://user:DB_SECRET@db.example/postgres"),
])
def test_runtime_rejects_unsafe_configuration_without_value_leaks(key, value):
    env = configured()
    env[key] = value
    with pytest.raises(ValueError) as caught:
        runner.validate_runtime("api", env)
    assert value not in str(caught.value)
    assert "VERY_SECRET_PASSWORD" not in str(caught.value)


def test_api_honors_injected_port_without_spawning_embedded_workers(monkeypatch):
    monkeypatch.setattr(runner.os, "environ", configured() | {"PORT": "32199"})
    calls = []
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=lambda *args, **kwargs: calls.append((args, kwargs))))
    assert runner.main([]) == 0
    assert calls == [(('app.main:app',), {"host": "0.0.0.0", "port": 32199, "workers": 1, "proxy_headers": False})]


@pytest.mark.parametrize("role,module", runner.WORKER_MODULES.items())
def test_worker_dispatch_preserves_cli_and_has_no_api_import(monkeypatch, role, module):
    monkeypatch.setattr(runner.os, "environ", configured(role))
    before = sys.argv
    calls = []
    monkeypatch.setattr(runner.importlib, "import_module", lambda name: SimpleNamespace(main=lambda ready_callback=None: calls.append((name, list(sys.argv), ready_callback)) or 0))
    assert runner.main(["--once"]) == 0
    assert calls == [(module, [module, "--once"], None)]
    assert sys.argv is before


def test_worker_readiness_only_starts_on_successful_initialization_callback(monkeypatch):
    monkeypatch.setattr(runner.os, "environ", configured("worker"))
    events = []
    monkeypatch.setattr(runner, "WorkerHealthProbe", lambda port: SimpleNamespace(
        start=lambda: events.append(("start", port)), stop=lambda: events.append(("stop", port))))

    def main(*, ready_callback):
        events.append(("initialized", None))
        ready_callback()
        return 0

    monkeypatch.setattr(runner.importlib, "import_module", lambda _: SimpleNamespace(main=main))
    assert runner.main([]) == 0
    assert events == [("initialized", None), ("start", 8080), ("stop", 8080)]


def test_worker_initialization_failure_does_not_signal_readiness_and_closes(monkeypatch):
    monkeypatch.setattr(runner.os, "environ", configured("worker"))
    events = []
    monkeypatch.setattr(runner, "WorkerHealthProbe", lambda port: SimpleNamespace(
        start=lambda: events.append("ready"), stop=lambda: events.append("closed")))
    monkeypatch.setattr(runner.importlib, "import_module", lambda _: SimpleNamespace(main=lambda ready_callback: 1))
    assert runner.main([]) == 1
    assert events == ["closed"]


def test_health_probe_binds_injected_port_and_only_returns_fixed_process_status(monkeypatch):
    endpoints = []

    class Server:
        def __init__(self, address, handler): endpoints.append((address, handler))
        def serve_forever(self): pass
        def shutdown(self): endpoints.append("shutdown")
        def server_close(self): endpoints.append("close")

    monkeypatch.setattr(runner, "ThreadingHTTPServer", Server)
    probe = runner.WorkerHealthProbe(32001)
    assert not endpoints  # Before init there is no bound socket/readiness.
    probe.start()
    probe.start()
    address, handler = endpoints[0]
    assert address == ("0.0.0.0", 32001)
    import io
    headers, codes = [], []
    response = SimpleNamespace(path="/healthz", wfile=io.BytesIO(),
        send_response=lambda code: codes.append(code), send_header=lambda *args: headers.append(args), end_headers=lambda: None)
    handler.do_GET(response)
    assert codes == [200]
    assert json.loads(response.wfile.getvalue()) == {"status": "process_running"}
    assert ("Cache-Control", "no-store") in headers
    invalid = []
    handler.do_GET(SimpleNamespace(path="/api/companies", send_error=lambda code: invalid.append(code)))
    assert invalid == [404]
    probe.stop()
    assert endpoints[1:] == ["shutdown", "close"]


def test_configured_preflight_has_separate_model_scenes_and_no_secret_values():
    report = preflight.inspect_configuration(configured(), "api", require_ai=True)
    assert not any(check.state == "error" for check in report)
    assert {check.name for check in report if check.state == "ok"} >= {"runtime", "auth", "storage", "model_ocr", "model_journal", "model_chat"}
    text = json.dumps([check.__dict__ for check in report])
    assert "SECRET" not in text and "PASSWORD" not in text and "synthetic-vision.example" not in text


def test_unconfigured_models_warn_or_block_explicit_ai_readiness():
    env = {key: value for key, value in configured().items() if not key.startswith("ACCOUNTING_AI_")}
    advisory = preflight.inspect_configuration(env, "api")
    strict = preflight.inspect_configuration(env, "api", require_ai=True)
    assert len([check for check in advisory if check.name.startswith("model_") and check.state == "warning"]) == 3
    assert len([check for check in strict if check.name.startswith("model_") and check.state == "error"]) == 3


def test_invalid_scene_does_not_emit_endpoint_key_or_exception():
    env = configured() | {"ACCOUNTING_AI_CHAT_BASE_URL": "http://unsafe-secret-host.example/v1", "ACCOUNTING_AI_CHAT_API_KEY": "LEAK_TEST_KEY"}
    report = preflight.inspect_configuration(env, "api")
    assert next(check for check in report if check.name == "models").state == "error"
    assert "unsafe-secret-host" not in repr(report) and "LEAK_TEST_KEY" not in repr(report)


def test_invalid_tariff_never_passes_as_free():
    env = configured() | {"ACCOUNTING_AI_CHAT_INPUT_CNY_PER_MILLION": "-2", "ACCOUNTING_AI_CHAT_OUTPUT_CNY_PER_MILLION": "0"}
    report = preflight.inspect_configuration(env, "api")
    assert next(check for check in report if check.name == "pricing_chat").state == "error"


@pytest.mark.parametrize("origin", ["*", "http://frontend.example", "https://user:secret@frontend.example", "https://frontend.example/path"])
def test_api_origin_whitelist_rejects_public_misconfiguration(origin):
    report = preflight.inspect_configuration(configured() | {"ACCOUNTING_ALLOWED_ORIGINS": origin}, "api")
    assert next(check for check in report if check.name == "public_routes").state == "error"


def test_static_errors_skip_all_online_operations(monkeypatch, capsys):
    monkeypatch.setattr(preflight.os, "environ", configured() | {"ACCOUNTING_ADMIN_DATABASE_URL": "ADMIN_SECRET_VALUE"})
    monkeypatch.setattr(preflight, "inspect_online", lambda *_: pytest.fail("invalid configuration must not use network"))
    assert preflight.main(["--online", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["checks"][-1]["state"] == "skipped"
    assert "ADMIN_SECRET_VALUE" not in repr(report)


def test_online_failures_redact_driver_and_http_output(monkeypatch):
    def fail(*_):
        raise RuntimeError("DB_SECRET_CONNECTION_HTTP_BODY_SUPPLIER_KEY")
    for name in ("verify_database", "verify_auth", "verify_storage"):
        monkeypatch.setattr(preflight, name, fail)
    checks = preflight.inspect_online(configured(), "api")
    assert all(check.state == "error" for check in checks)
    assert "DB_SECRET" not in repr(checks)


def test_migration_preflight_does_not_require_models_or_auth():
    env = {key: value for key, value in configured("migration").items() if not key.startswith("ACCOUNTING_AI_") and not key.startswith("ACCOUNTING_SUPABASE_")}
    report = preflight.inspect_configuration(env, "migration", require_ai=True)
    assert not any(check.state == "error" for check in report)
    assert not any(check.name.startswith("model") or check.name == "auth" for check in report)


def test_database_probe_is_only_read_only_catalog_queries(monkeypatch):
    from app.accounting.database import migration_digest, migration_files
    migrations = [{"version": int(path.name.split('_', 1)[0]), "checksum": migration_digest(path)} for path in migration_files("postgres")]
    statements = []

    class Result:
        def __init__(self, rows): self.rows = rows
        def fetchone(self): return self.rows[0] if self.rows else None
        def fetchall(self): return self.rows

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def rollback(self): statements.append("ROLLBACK")
        def execute(self, sql, parameters=None):
            statements.append(sql)
            if "WHERE rolname=current_user" in sql:
                return Result([{ "oid": 42, "rolsuper": False, "rolbypassrls": False, "rolcreatedb": False, "rolcreaterole": False }])
            if "WHERE rolname IN" in sql:
                return Result([{"rolname": "accounting_app"}, {"rolname": "accounting_system"}])
            if "schema_migrations" in sql:
                return Result(migrations)
            return Result([])

    import psycopg
    monkeypatch.setattr(psycopg, "connect", lambda *_args, **_kwargs: Connection())
    preflight.verify_database(configured(), "api")
    assert statements[0] == "BEGIN READ ONLY" and statements[-1] == "ROLLBACK"
    assert not any(token in statement.upper() for statement in statements for token in ("INSERT ", "UPDATE ", "DELETE ", "CREATE ", "ALTER ", "GRANT ", "REVOKE "))


def test_storage_probe_only_reads_bucket_metadata(monkeypatch):
    import app.storage.supabase as storage
    actions = []
    monkeypatch.setattr(storage, "SupabaseBlobBackend", lambda settings: SimpleNamespace(verify_private=lambda: actions.append("metadata") or {"private": True}))
    preflight.verify_storage(configured())
    assert actions == ["metadata"]
