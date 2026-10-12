import pytest

from app.auth import AuthSettings
from app.main import create_app


def settings():
    return AuthSettings(mode="supabase", supabase_url="https://isolated-auth.example.test", publishable_key="sb_publishable_test_only")


def test_real_identity_cannot_use_demo_sqlite(tmp_path):
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        create_app(tmp_path / "demo.db", auth_settings=settings())


def test_postgres_cannot_use_anonymous_demo_identity():
    with pytest.raises(RuntimeError, match="禁止"):
        create_app("postgresql://runtime@localhost/isolated", auth_settings=AuthSettings(mode="local"))


def test_production_requires_database_tls_and_explicit_https_origins(monkeypatch):
    monkeypatch.setenv("ACCOUNTING_ENV", "production")
    with pytest.raises(RuntimeError, match="sslmode"):
        create_app("postgresql://runtime@localhost/isolated", auth_settings=settings())
    monkeypatch.delenv("ACCOUNTING_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("ACCOUNTING_ALLOWED_ORIGINS", raising=False)
    with pytest.raises(RuntimeError, match="显式配置"):
        create_app("postgresql://runtime@localhost/isolated?sslmode=require", auth_settings=settings())
    monkeypatch.setenv("ACCOUNTING_ALLOWED_HOSTS", "api.example.test")
    monkeypatch.setenv("ACCOUNTING_ALLOWED_ORIGINS", "http://app.example.test")
    with pytest.raises(RuntimeError, match="HTTPS"):
        create_app("postgresql://runtime@localhost/isolated?sslmode=require", auth_settings=settings())
    monkeypatch.setenv("ACCOUNTING_ALLOWED_ORIGINS", "https://app.example.test")
    monkeypatch.setenv("ACCOUNTING_ATTACHMENT_ROOT", "./private-test-attachments")
    # Creating configuration performs no remote connection; lifespan validates DB.
    create_app("postgresql://runtime@localhost/isolated?sslmode=require", auth_settings=settings())


@pytest.mark.parametrize("key", ["ACCOUNTING_ALLOWED_HOSTS", "ACCOUNTING_ALLOWED_ORIGINS"])
def test_wildcard_configuration_is_rejected(monkeypatch, tmp_path, key):
    monkeypatch.setenv(key, "*")
    with pytest.raises(RuntimeError, match="通配符"):
        create_app(tmp_path / "isolated.db")
