"""Local signed identities and restricted PostgreSQL for the P02 HTTP journey."""

from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.accounting.domain import Actor
from app.accounting.models import (
    BookCreate,
    BookMemberGrant,
    CompanyMemberGrant,
    DepartmentCreate,
    FirmCreate,
    FirmMemberGrant,
)
from app.auth import AuthSettings
from app.intelligence.models import ProviderUsage
from app.main import create_app
from c01_identity_fixture import LocalIdentityFixture
from scripts.ak05_postgres_gate import assert_server_and_roles
from scripts.migrate_accounting import migrate
from test_intelligence_workflow import FakeProvider, invoice_document


class PrivateHeaders(dict):
    def __repr__(self):
        return "<local signed test headers>"


class P02Session(SimpleNamespace):
    def __repr__(self):
        return "<P02 isolated signed session>"


@contextmanager
def create_session(config_path, directory, *, identity=None, major=None):
    """No identity lambda or business SQL: setup uses verified profiles/services."""
    config_path, directory = Path(config_path), Path(directory)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    # The gate rejects non-loopback endpoints and privileged business accounts.
    # Legacy P02 configurations encode the version in their filename. New
    # callers with generic configuration names must pass their explicit gate.
    if major is None:
        major = 17 if "17" in config_path.name else 16
    if type(major) is not int or major not in {16, 17}:
        raise ValueError("Explicit PostgreSQL 16 or 17 required")
    assert_server_and_roles(config, major)
    migrate(config["admin"])
    directory.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        if identity is None:
            identity = stack.enter_context(LocalIdentityFixture.create(directory / "identity"))
        users, headers, credentials = {}, {}, {}
        suffix = uuid4().hex[:12]
        for role in (
            "owner",
            "accountant",
            "reviewer",
            "employee",
            "department",
            "finance",
            "customer",
            "outsider",
        ):
            email, password = f"p02-{role}-{suffix}@example.test", "P02-local-only-" + uuid4().hex
            if isinstance(identity, LocalIdentityFixture):
                user = identity.provision(
                    email, password, totp=role == "owner", name="P02虚构" + role
                )
            else:
                user = identity.provision(str(uuid4()), email, password)
            users[role] = user["id"]
            login = identity.login(email, password)
            if role == "owner":
                login = identity.step_up(login["access_token"])
            headers[role] = PrivateHeaders(Authorization="Bearer " + login["access_token"])
            credentials[role] = {"email": email, "password": password, "user_id": user["id"]}
        provider = FakeProvider(
            invoice_document(
                amount="268.10",
                net_amount="268.10",
                issued_on="2026-10-08",
                seller_name="P02 Synthetic Supplier",
                buyer_name="P02 Synthetic Buyer",
            )
        )
        provider.usage = ProviderUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        provider.proposal_mutator = lambda output: output["lines"][1].update(account_code="2202")
        old = {
            k: os.environ.get(k)
            for k in (
                "ACCOUNTING_ENV",
                "ACCOUNTING_ALLOW_FIRM_SIGNUP",
                "ACCOUNTING_STORAGE_BACKEND",
            )
        }
        os.environ.update(
            ACCOUNTING_ENV="test",
            ACCOUNTING_ALLOW_FIRM_SIGNUP="1",
            ACCOUNTING_STORAGE_BACKEND="local",
        )

        def restore_env():
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        stack.callback(restore_env)
        app = create_app(
            config["app"],
            seed=False,
            attachment_root=directory / "originals",
            auth_settings=AuthSettings(
                mode="supabase",
                supabase_url=identity.origin,
                publishable_key=identity.publishable_key,
            ),
            authenticator=identity.authenticator(),
            intelligence_provider=provider,
            queue_worker_enabled=False,
        )
        client = stack.enter_context(TestClient(app, base_url="http://127.0.0.1"))
        for role in users:
            result = client.get("/api/me", headers=headers[role])
            assert result.status_code == 200, result.text
        dirs = app.state.directory
        firm = dirs.create_firm(users["owner"], FirmCreate(name="P02虚构事务所" + suffix))
        actor = Actor(users["owner"], firm["id"])
        for header in headers.values():
            header["X-Firm-Id"] = firm["id"]
        for role in ("accountant", "reviewer"):
            dirs.grant_firm_member(
                actor, FirmMemberGrant(email=credentials[role]["email"], role="reviewer")
            )
        book = app.state.service.create_book(
            actor, BookCreate(name="P02非零业务账套", period="2026-10")
        )
        other = app.state.service.create_book(
            actor, BookCreate(name="P02无关企业账套", period="2026-10")
        )
        department = dirs.create_department(
            actor, book["company_id"], DepartmentCreate(name="虚构业务部门")
        )
        for role in ("employee", "department", "finance", "customer"):
            dirs.grant_company_member(
                actor,
                book["company_id"],
                CompanyMemberGrant(
                    email=credentials[role]["email"],
                    role="finance" if role in {"finance", "customer"} else "employee",
                    department_id=department["id"] if role in {"employee", "department"} else None,
                ),
            )
        dirs.grant_company_member(
            actor,
            other["company_id"],
            CompanyMemberGrant(email=credentials["outsider"]["email"], role="employee"),
        )
        for role, grant in (
            ("accountant", "reviewer"),
            ("reviewer", "reviewer"),
            ("finance", "editor"),
            ("customer", "viewer"),
        ):
            dirs.grant_book_member(actor, book["id"], users[role], BookMemberGrant(role=grant))
        app.state.business.grant_department_approver(
            actor, book["company_id"], department["id"], users["department"]
        )
        ids = {
            "firm": firm["id"],
            "company": book["company_id"],
            "book": book["id"],
            "other_company": other["company_id"],
            "other_book": other["id"],
            "department": department["id"],
            "users": users,
        }
        yield P02Session(
            client=client,
            app=app,
            headers=headers,
            ids=ids,
            provider=provider,
            db=app.state.service.db,
            storage=app.state.attachment_store,
            identity=identity,
            credentials=credentials,
            config=config,
        )
