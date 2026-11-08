"""Explicit disposable PG source/new-cluster recovery with persistent offline auth.

No cloud/provider calls. Local signed identity and object protocol substitutes
are labelled as substitutes; PostgreSQL, RLS, ACL and pg_dump/restore are real.
"""

from concurrent.futures import CancelledError
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import psycopg
import pytest

from app.accounting.database import Database
from app.accounting.domain import Actor
from app.accounting.models import ApprovalRequest, ExportRequest
from app.accounting.service import AccountingService
from app.auth import AuthError, AuthSettings
from app.auth.admin import SupabaseAdmin
from app.intelligence.provider import ProviderError
from app.intelligence.service import IntelligenceService
from app.jobs.service import QueueService
from app.jobs.worker import QueueWorker
from app.main import create_app
from scripts import backup_accounting
from scripts.migrate_accounting import migrate
from scripts.release_recovery_hold import pending_inventory, release_local_hold, REQUIRED_CHECKS
from c01_identity_fixture import (
    LocalIdentityFixture,
    LocalPrivateObjectFixture,
    verify_joint_bundle,
    _secure,
)
from test_intelligence_workflow import FakeProvider
from c01_wsl_bridge import call as bridge_call
from scripts.p01_recovery_gate import REQUIRED_CHECKS as P01_CHECKS
from p01_recovery_business import (
    seed_latest_recovery_business,
    require_latest_business_inventory,
    verify_latest_business_http,
    assert_notifications_fenced,
    assert_restored_invitations,
)


ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "C01-full-recovery-synthetic-password"
pytestmark = pytest.mark.skipif(
    os.getenv("C01_FULL_RECOVERY_ACCEPT") != "1",
    reason="必须显式启用C01本机新集群恢复，不读取生产环境连接",
)


def recovery_config_directory():
    directory = Path(os.getenv("P01_CONFIG_DIR", str(ROOT / ".codex/c01-final-pg"))).resolve()
    if not any(directory.is_relative_to(ROOT / name) for name in ("tmp_test", ".codex")):
        raise ValueError("Recovery configuration must be private local workspace artifacts")
    return directory


def _read_config(major, target=False):
    name = f"pg{major}-recovery-fresh-target.json" if target else f"pg{major}-recovery_source.json"
    config = json.loads((recovery_config_directory() / name).read_text(encoding="utf-8-sig"))
    endpoints = []
    for key in ("admin", "app", "worker", "projection"):
        value = urlsplit(config[key])
        if (
            value.scheme not in {"postgres", "postgresql"}
            or value.hostname != "127.0.0.1"
            or value.port not in {25496, 25497, 25498, 25499}
            or not value.path.startswith("/ak01_local_ak05_")
            or value.query
            or value.fragment
        ):
            raise ValueError("只允许显式白名单本机新集群C01恢复目标")
        endpoints.append((value.hostname, value.port, value.path))
    if len(set(endpoints)) != 1:
        raise ValueError("单个恢复配置的四个角色必须指向同一隔离库")
    return config


def _pg_bridge(major):
    def run(name, arguments, database):
        if name not in {"pg_dump", "pg_restore"}:
            raise ValueError("恢复桥仅允许运行官方pg_dump/pg_restore")
        target = name == "pg_restore"
        if database != _read_config(major, target)["admin"]:
            raise ValueError("PG工具连接不属于本轮显式源/目标")
        key = ("target" if target else "source") + str(major)
        arguments = list(map(str, arguments))
        if name == "pg_dump":
            assert arguments[:3] == ["--format=custom", "--schema=accounting", "--no-password"]
            assert len(arguments) == 7 and arguments[3] == "--snapshot" and arguments[5] == "--file"
            payload = {"snapshot": arguments[4], "output": arguments[6], "schema": "accounting"}
        else:
            assert arguments[:6] == [
                "--no-owner",
                "--no-privileges",
                "--single-transaction",
                "--exit-on-error",
                "--no-password",
                "--dbname",
            ]
            assert len(arguments) == 8 and arguments[6] == urlsplit(database).path[1:]
            payload = {"input": arguments[7]}
        result = bridge_call(
            recovery_config_directory() / "wsl-bridge-private.json", name, target=key, **payload
        )
        assert result["completed"] is True

    return run


def _http(client, method, path, headers, body=None, expected=200):
    response = client.request(method, path, headers=headers, json=body)
    if response.status_code != expected:
        # Only application-safe bodies are inspected, never bearer headers.
        raise AssertionError(
            f"C01 local API {method} {path}: {response.status_code}, {response.text[:400]}"
        )
    return response.json()


def _headers(identity, email, firm=None, admin=False):
    session = identity.login(email, PASSWORD)
    if admin:
        session = identity.step_up(session["access_token"])
    return {
        "Authorization": "Bearer " + session["access_token"],
        **({"X-Firm-Id": firm} if firm else {}),
    }


def _app(config, identity, objects, monkeypatch, provider, attachment_root):
    monkeypatch.setenv("ACCOUNTING_ENV", "test")
    monkeypatch.setenv("ACCOUNTING_ALLOW_FIRM_SIGNUP", "1")
    monkeypatch.setenv("ACCOUNTING_STORAGE_BACKEND", "supabase")
    monkeypatch.setenv("ACCOUNTING_STORAGE_SUPABASE_URL", objects.origin)
    monkeypatch.setenv("ACCOUNTING_STORAGE_BUCKET", objects.bucket)
    monkeypatch.setenv("ACCOUNTING_STORAGE_SERVER_KEY", objects.server_key)
    monkeypatch.setenv("ACCOUNTING_QUEUE_EMBEDDED_WORKER", "0")
    monkeypatch.delenv("ACCOUNTING_PROJECTION_DATABASE_URL", raising=False)
    monkeypatch.delenv("ACCOUNTING_RESTORE_HOLD_FILE", raising=False)
    monkeypatch.setattr("app.main.create_attachment_store", lambda *args, **kwargs: objects.store())
    return create_app(
        config["app"],
        seed=False,
        auth_settings=AuthSettings("supabase", identity.origin, identity.publishable_key),
        authenticator=identity.authenticator(),
        attachment_root=attachment_root,
        intelligence_provider=provider,
        queue_worker_enabled=False,
    )


def _native_security(config, owner, editor, other_company):
    with psycopg.connect(config["admin"]) as conn:
        tables = conn.execute(
            "SELECT count(*),bool_and(relrowsecurity AND relforcerowsecurity) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='accounting' AND c.relkind IN ('r','p')"
        ).fetchone()
        assert tables[0] > 50
        forced = conn.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='accounting' AND c.relkind IN ('r','p') AND relrowsecurity AND relforcerowsecurity"
        ).fetchone()[0]
        assert forced > 50
        roles = conn.execute(
            "SELECT rolname,rolcanlogin,rolsuper,rolbypassrls FROM pg_roles WHERE rolname IN('accounting_app','accounting_worker','accounting_system','accounting_projection')"
        ).fetchall()
        assert len(roles) == 4 and all(not any(row[1:]) for row in roles)
    runtime = Database(config["app"])
    try:
        with runtime.transaction(actor=owner) as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM companies WHERE id=?", (other_company,)
                ).fetchone()[0]
                == 1
            )
        with runtime.transaction(actor=editor) as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM companies WHERE id=?", (other_company,)
                ).fetchone()[0]
                == 0
            )
            assert conn.execute("SELECT id,status FROM assistant_turns").fetchall() == []
        for sql in (
            "SELECT * FROM accounting.assistant_turns",
            "UPDATE accounting.audit_events SET action='tamper'",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with psycopg.connect(config["app"]) as conn:
                    conn.execute("SET ROLE accounting_app")
                    conn.execute(sql)
    finally:
        runtime.close()


@pytest.mark.parametrize("major", [17, 16])
def test_fresh_cluster_database_objects_signed_identity_and_http_recovery(
    major, tmp_path, monkeypatch
):
    started = time.monotonic()
    p01_checks = dict.fromkeys(P01_CHECKS, False)
    _secure(tmp_path)
    source, target = _read_config(major), _read_config(major, True)
    if urlsplit(source["admin"]).port == urlsplit(target["admin"]).port:
        raise ValueError("完整恢复验收要求全新独立集群")
    allowed = set(source.values()) | set(target.values())
    real_connect = psycopg.connect

    def isolated_connect(conninfo="", *args, **kwargs):
        if conninfo not in allowed or set(kwargs) & {
            "host",
            "port",
            "dbname",
            "user",
            "password",
            "service",
        }:
            raise RuntimeError("C01恢复拒绝显式源/目标之外的数据库")
        return real_connect(conninfo, *args, **kwargs)

    monkeypatch.setattr(psycopg, "connect", isolated_connect)
    monkeypatch.setattr(
        httpx.HTTPTransport,
        "handle_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("C01恢复禁止外部HTTP")),
    )
    monkeypatch.setattr(backup_accounting, "pg_tool", _pg_bridge(major))
    migrate(source["admin"])
    with psycopg.connect(target["admin"]) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='accounting'"
            ).fetchone()[0]
            == 0
        )
    source_identity = LocalIdentityFixture.create(tmp_path / "identity-source")
    objects = LocalPrivateObjectFixture(tmp_path / "source-objects")
    replacement = LocalPrivateObjectFixture(
        tmp_path / "restored-objects", "https://c01-restored-objects.example.test"
    )
    users = {
        name: source_identity.provision(
            f"{name}-{uuid4().hex[:8]}@example.test",
            PASSWORD,
            totp=name == "owner",
            name="虚构" + name,
        )
        for name in ("owner", "editor", "reviewer", "employee", "outsider", "customer")
    }
    source_headers = {
        name: _headers(source_identity, user["email"], admin=name == "owner")
        for name, user in users.items()
    }
    provider = FakeProvider()
    source_app = _app(
        source, source_identity, objects, monkeypatch, provider, tmp_path / "source-attachments"
    )
    original = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC"
    )
    try:
        with TestClient(source_app, base_url="http://127.0.0.1") as client:
            for headers in source_headers.values():
                _http(client, "GET", "/api/me", headers)
            firm = _http(
                client,
                "POST",
                "/api/firms",
                source_headers["owner"],
                {"name": "C01虚构完整恢复事务所"},
                201,
            )["id"]
            for headers in source_headers.values():
                headers["X-Firm-Id"] = firm
            owner = source_headers["owner"]
            company = _http(
                client, "POST", "/api/companies", owner, {"name": "C01虚构恢复企业A"}, 201
            )["id"]
            other = _http(
                client, "POST", "/api/companies", owner, {"name": "C01虚构恢复企业B"}, 201
            )["id"]
            book = _http(
                client,
                "POST",
                "/api/books",
                owner,
                {"name": "C01虚构恢复账套A", "company_id": company, "period": "2026-10"},
                201,
            )["id"]
            other_book = _http(
                client,
                "POST",
                "/api/books",
                owner,
                {"name": "C01虚构恢复账套B", "company_id": other, "period": "2026-10"},
                201,
            )["id"]
            for name, role in (("editor", "operator"), ("reviewer", "reviewer")):
                _http(
                    client,
                    "PUT",
                    "/api/firm/members",
                    owner,
                    {"email": users[name]["email"], "role": role},
                )
                _http(
                    client,
                    "PUT",
                    f"/api/books/{book}/members/{users[name]['id']}",
                    owner,
                    {"role": "editor" if name == "editor" else "reviewer"},
                )
            _http(
                client,
                "PUT",
                f"/api/companies/{company}/members",
                owner,
                {"email": users["employee"]["email"], "role": "employee"},
            )
            event = _http(
                client,
                "POST",
                "/api/business/events",
                source_headers["employee"],
                {
                    "company_id": company,
                    "template_id": "reimbursement",
                    "payload": {
                        "occurred_on": "2026-10-12",
                        "description": "C01虚构恢复原始业务",
                        "counterparty": "虚构供方",
                        "amount": "0.30",
                        "direction": "expense",
                        "payment_status": "unpaid",
                        "category": "office",
                        "purpose": "仅本地恢复验收",
                    },
                },
                201,
            )
            message_ids = [uuid4().hex, uuid4().hex]
            with source_app.state.service.db.transaction(
                write=True, actor=Actor(users["owner"]["id"], firm)
            ) as conn:
                for message_id in message_ids:
                    conn.execute(
                        "INSERT INTO client_messages(id,firm_id,company_id,recipient_user_id,kind,subject,body,created_by,created_at) "
                        "VALUES(?,?,?,?,'return','恢复验收补件','仅虚构资料',?,'2026-11-03T00:00:00Z')",
                        (message_id, firm, company, users["employee"]["id"], users["owner"]["id"]),
                    )
            _http(
                client,
                "POST",
                f"/api/client/messages/{message_ids[0]}/read",
                source_headers["employee"],
            )
            result = client.post(
                f"/api/business/events/{event['id']}/attachments?expected_version={event['version']}",
                headers=source_headers["employee"],
                files={"file": ("C01虚构原件.png", original, "image/png")},
            )
            assert result.status_code == 200
            event = result.json()
            attachment_id = event["attachments"][0]["id"]
            for ordinal in (1, 2):
                _http(
                    client,
                    "POST",
                    f"/api/books/{book}/imports",
                    source_headers["editor"],
                    {
                        "source_name": "C01虚构流水",
                        "rows": [
                            {
                                "external_id": f"c01-recovery-{ordinal}",
                                "date": "2026-10-12",
                                "description": "办公用品",
                                "counterparty": "虚构供方",
                                "amount": "0.30",
                                "direction": "expense",
                                "evidence_ref": "虚构原件独立保留",
                            }
                        ],
                    },
                )
            _http(client, "POST", f"/api/books/{book}/suggestions", source_headers["editor"], {})
            drafts = _http(client, "GET", f"/api/books/{book}", source_headers["editor"])["drafts"]
            approved = _http(
                client,
                "POST",
                f"/api/books/{book}/drafts/{drafts[0]['id']}/approve",
                source_headers["reviewer"],
                {"expected_version": drafts[0]["version"]},
            )
            exported = _http(
                client,
                "POST",
                f"/api/books/{book}/exports",
                source_headers["reviewer"],
                {"draft_ids": [approved["id"]], "idempotency_key": "c01-before-backup-export"},
            )
            assert exported["count"] == 1
            _http(
                client,
                "POST",
                f"/api/books/{book}/delivery/exports/{exported['id']}/receipts",
                source_headers["reviewer"],
                {
                    "book_id": book,
                    "company_id": company,
                    "period": "2026-10",
                    "currency": "CNY",
                    "idempotency_key": "p01-unknown-external-receipt",
                    "reason": "虚构人工交付结果未知，恢复不得冒充厂商已过账",
                    "items": [{"draft_id": approved["id"], "status": "unknown"}],
                },
                201,
            )
            for _ in (1,):
                _http(
                    client,
                    "POST",
                    "/api/intelligence/batches",
                    source_headers["employee"],
                    {
                        "company_id": company,
                        "idempotency_key": "c01-pending-job",
                        "items": [
                            {
                                "event_id": event["id"],
                                "attachment_id": attachment_id,
                                "expected_version": event["version"],
                            }
                        ],
                    },
                    202,
                )
            worker_database = Database(source["worker"])
            try:
                unknown = ProviderError("timeout", "本地合成发送后断连", retryable=False)
                unknown.llm_attempted = True
                provider.failure = unknown
                worker = QueueWorker(
                    QueueService(
                        worker_database,
                        IntelligenceService(worker_database, objects.store(), provider),
                    )
                )
                assert worker.run_once()
                assert not worker.run_once()
                assert provider.extract_calls == 1
            finally:
                worker_database.close()
                provider.failure = None
            # A second immutable original remains pending at snapshot time.
            pending = _http(
                client,
                "POST",
                "/api/business/events",
                source_headers["employee"],
                {
                    "company_id": company,
                    "template_id": "reimbursement",
                    "payload": {
                        "occurred_on": "2026-10-13",
                        "description": "C01虚构待处理事项",
                        "counterparty": "虚构供方",
                        "amount": "0.30",
                        "direction": "expense",
                        "payment_status": "unpaid",
                        "category": "office",
                        "purpose": "仅本地恢复验收",
                    },
                },
                201,
            )
            upload = client.post(
                f"/api/business/events/{pending['id']}/attachments?expected_version={pending['version']}",
                headers=source_headers["employee"],
                files={"file": ("C01虚构待处理.png", original, "image/png")},
            )
            assert upload.status_code == 200
            pending = upload.json()
            _http(
                client,
                "POST",
                "/api/intelligence/batches",
                source_headers["employee"],
                {
                    "company_id": company,
                    "idempotency_key": "c01-pending-second",
                    "items": [
                        {
                            "event_id": pending["id"],
                            "attachment_id": pending["attachments"][0]["id"],
                            "expected_version": pending["version"],
                        }
                    ],
                },
                202,
            )
            latest_worker = Database(source["worker"])
            try:
                latest_business = seed_latest_recovery_business(
                    source_app.state.service.db,
                    latest_worker,
                    objects.store(),
                    owner=Actor(users["owner"]["id"], firm),
                    reviewer=Actor(users["reviewer"]["id"], firm),
                    customer=Actor(users["customer"]["id"], firm),
                    customer_email=users["customer"]["email"],
                )
                from permission_recovery_business import seed_permission_recovery

                def strip_fixture(kind, identifier):
                    # Only reconstruct unknown historical authority in the isolated source.
                    with psycopg.connect(source["admin"]) as restore:
                        restore.execute(
                            "ALTER TABLE accounting.review_authority_snapshots DISABLE TRIGGER authority_immutable"
                        )
                        restore.execute(
                            "DELETE FROM accounting.review_authority_snapshots WHERE subject_kind=%s AND subject_id=%s",
                            (kind, identifier),
                        )
                        restore.execute(
                            "ALTER TABLE accounting.review_authority_snapshots ENABLE TRIGGER authority_immutable"
                        )

                latest_business["access_review"] = seed_permission_recovery(
                    source_app.state.service.db,
                    owner=Actor(users["owner"]["id"], firm),
                    reviewer=Actor(users["reviewer"]["id"], firm),
                    observer=Actor(users["editor"]["id"], firm),
                    customer=Actor(users["customer"]["id"], firm),
                    company_id=latest_business["company_id"],
                    delivery_book=latest_business["delivery_book"],
                    strip_fixture=strip_fixture,
                )
            finally:
                latest_worker.close()
        # All business writers are now closed: this is a quiesced drill, not a distributed live snapshot.
        bundle = tmp_path / "private-identity-bundle" / "identity.bundle"
        identity_receipt = source_identity.backup(bundle)
        backup_directory = tmp_path / "accounting-backup"
        backup_accounting.backup(
            source["admin"], None, backup_directory, storage_store=objects.store()
        )
        manifest_bytes = (backup_directory / "manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        source_uuids = sorted(row["id"] for row in users.values())
        combined = {
            "format": "c01-quiesced-system-fixture-v1",
            "quiesced": True,
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
            "identity_sha256": identity_receipt["sha256"],
            "accounting_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "user_uuids": source_uuids,
            "originals": [
                {"path": row["path"], "sha256": row["sha256"]}
                for row in manifest["files"]
                if row["path"].startswith("originals/")
            ],
            "identity_contains_sensitive_material": True,
        }
        assert combined["originals"]
        with psycopg.connect(source["admin"]) as conn:
            referenced = {
                row[0] for row in conn.execute("SELECT id FROM accounting.users WHERE active=1")
            }
            assert referenced <= set(source_uuids)
            assert (
                verify_joint_bundle(
                    combined, bundle, backup_directory / "manifest.json", referenced
                )
                == source_uuids
            )
            source_inventory = pending_inventory(conn)
            require_latest_business_inventory(source_inventory)
            assert source_inventory["tables"]["delivery_receipt_items"]
            snapshot_version = conn.execute("SHOW server_version").fetchone()[0]
            assert (
                conn.execute(
                    "SELECT count(*) FROM accounting.ocr_attempts WHERE status='interrupted'"
                ).fetchone()[0]
                == 1
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM accounting.model_costs WHERE state='uncertain'"
                ).fetchone()[0]
                == 1
            )
        (tmp_path / "source_fixture.json").write_text(
            json.dumps(
                {
                    "firm_id": firm,
                    "company_id": company,
                    "other_company_id": other,
                    "book_id": book,
                    "other_book_id": other_book,
                    "attachment_id": attachment_id,
                    "current_review": {
                        "book_id": latest_business["access_review"]["book_id"],
                        "subject_id": latest_business["access_review"]["requests"][0]["source"][
                            "subject_id"
                        ],
                        "delivery_book_id": latest_business["delivery_book"]["id"],
                        "package_id": latest_business["packages"][-1]["id"],
                        "manifest_sha256": latest_business["packages"][-1]["manifest_sha256"],
                    },
                    "users": {
                        name: {"id": user["id"], "email": user["email"]}
                        for name, user in users.items()
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        restored_directory = tmp_path / "restored-accounting"
        restore_started = time.monotonic()
        receipt = backup_accounting.restore(
            backup_directory, restored_directory, target["admin"], restore_permissions=True
        )
        assert receipt["security_verified"] is True
        p01_checks["database"] = True
        assert combined["identity_sha256"] == hashlib.sha256(bundle.read_bytes()).hexdigest()
        assert combined["accounting_manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
        restored_identity = LocalIdentityFixture.restore(
            bundle,
            tmp_path / "identity-restored",
            expected_sha256=combined["identity_sha256"],
            origin="https://c01-restored-auth.example.test",
        )
        try:
            for row in combined["originals"]:
                raw = (restored_directory / row["path"]).read_bytes()
                assert hashlib.sha256(raw).hexdigest() == row["sha256"]
                replacement.restore_original(row["path"].removeprefix("originals/"), raw)
            p01_checks["originals"] = True
            with psycopg.connect(target["admin"]) as conn:
                restored_inventory = pending_inventory(conn)
                require_latest_business_inventory(restored_inventory)
                assert restored_inventory["sha256"] == source_inventory["sha256"]
                assert conn.execute(
                    "SELECT hold FROM accounting.restore_state WHERE instance_id='restored'"
                ).fetchone() == (True,)
            with pytest.raises((SystemExit, CancelledError)):
                held = _app(
                    target,
                    restored_identity,
                    replacement,
                    monkeypatch,
                    provider,
                    restored_directory / "originals",
                )
                with TestClient(held, base_url="http://127.0.0.1"):
                    pytest.fail("正常API不应在恢复hold期间启动")
            p01_checks["hold"] = True
            verified = restored_identity.authenticator()
            for name, row in users.items():
                fresh = restored_identity.login(row["email"], PASSWORD)
                assert verified.authenticate(fresh["access_token"]).user_id == row["id"]
                with pytest.raises(AuthError):
                    verified.authenticate(source_headers[name]["Authorization"][7:])
            p01_checks["identity"] = True
            owner_actor = Actor(users["owner"]["id"], firm)
            _native_security(target, owner_actor, Actor(users["editor"]["id"], firm), other)
            p01_checks["permissions"] = True
            database = Database(target["app"])
            try:
                assert_restored_invitations(database, owner_actor, latest_business, held=True)
                accounting = AccountingService(database)
                actor = Actor(users["reviewer"]["id"], firm)
                before = accounting.get_book(actor, book)
                remaining = next(row for row in before["drafts"] if row["status"] == "draft")
                accounting.approve(
                    actor,
                    book,
                    remaining["id"],
                    ApprovalRequest(expected_version=remaining["version"]),
                )
                assert (
                    accounting.export(
                        actor,
                        book,
                        ExportRequest(
                            draft_ids=[remaining["id"]],
                            idempotency_key="c01-held-direct-service-export",
                        ),
                    )["count"]
                    == 1
                )
            finally:
                database.close()
            proof = {name: True for name in REQUIRED_CHECKS}
            with psycopg.connect(target["admin"]) as conn:
                inventory_before_release = pending_inventory(conn)
            release_local_hold(
                target["admin"],
                restored_directory,
                operator="C01-local-recovery-verifier",
                reason="已实证核验数据库原件身份权限业务与在途未知状态，限定本地演练释放",
                checks=proof,
                inventory_sha256=inventory_before_release["sha256"],
            )
            restored_worker = Database(target["worker"])
            try:
                notification_proof = assert_notifications_fenced(restored_worker, latest_business)
            finally:
                restored_worker.close()
            target_app = _app(
                target,
                restored_identity,
                replacement,
                monkeypatch,
                provider,
                restored_directory / "originals",
            )
            new_headers = {
                name: _headers(restored_identity, row["email"], firm, admin=name == "owner")
                for name, row in users.items()
            }
            with TestClient(target_app, base_url="http://127.0.0.1") as client:
                assert_restored_invitations(
                    target_app.state.service.db, owner_actor, latest_business, held=False
                )
                messages = _http(client, "GET", "/api/client/messages", new_headers["employee"])[
                    "messages"
                ]
                receipts = {row["id"]: row["read_at"] for row in messages}
                assert receipts[message_ids[0]] and receipts[message_ids[1]] is None
                _http(
                    client,
                    "POST",
                    f"/api/client/messages/{message_ids[1]}/read",
                    new_headers["employee"],
                )
                assert all(
                    row["read_at"]
                    for row in _http(
                        client, "GET", "/api/client/messages", new_headers["employee"]
                    )["messages"]
                    if row["id"] in message_ids
                )
                assert client.post(
                    f"/api/client/messages/{message_ids[0]}/read", headers=new_headers["outsider"]
                ).status_code in {403, 404}
                business_proof = verify_latest_business_http(
                    client,
                    new_headers,
                    latest_business,
                    wrong_book_id=other_book,
                )
                from permission_recovery_business import verify_permission_recovery_http

                assert verify_permission_recovery_http(
                    client, new_headers, latest_business["access_review"]
                )
                p01_checks["tax_delivery"] = all(business_proof.values())
                for name, headers in new_headers.items():
                    if name == "outsider":
                        assert client.get("/api/me", headers=headers).status_code == 403
                        own_headers = {"Authorization": headers["Authorization"]}
                        assert (
                            _http(client, "GET", "/api/me", own_headers)["user"]["id"]
                            == users[name]["id"]
                        )
                        continue
                    assert (
                        _http(client, "GET", "/api/me", headers)["user"]["id"] == users[name]["id"]
                    )
                assert client.get("/api/session").status_code == 404
                content = client.get(
                    f"/api/business/attachments/{attachment_id}/content",
                    headers=new_headers["employee"],
                )
                assert content.status_code == 200 and content.content == original
                assert client.get(
                    f"/api/business/attachments/{attachment_id}/content",
                    headers=new_headers["outsider"],
                ).status_code in {403, 404}
                assert (
                    client.get(
                        f"/api/books/{other_book}", headers=new_headers["editor"]
                    ).status_code
                    == 404
                )
                _http(
                    client,
                    "POST",
                    f"/api/books/{book}/imports",
                    new_headers["editor"],
                    {
                        "source_name": "恢复后真实写入",
                        "rows": [
                            {
                                "external_id": "c01-after-restore",
                                "date": "2026-10-13",
                                "description": "办公用品",
                                "amount": "1.20",
                                "direction": "expense",
                                "evidence_ref": "虚构恢复后新证据",
                            }
                        ],
                    },
                )
                _http(client, "POST", f"/api/books/{book}/suggestions", new_headers["editor"], {})
                fresh_draft = next(
                    row
                    for row in _http(client, "GET", f"/api/books/{book}", new_headers["reviewer"])[
                        "drafts"
                    ]
                    if row["status"] == "draft"
                )
                assert client.post(
                    f"/api/books/{book}/exports",
                    headers=new_headers["reviewer"],
                    json={
                        "draft_ids": [fresh_draft["id"]],
                        "idempotency_key": "c01-unreviewed-must-fail",
                    },
                ).status_code in {403, 409, 422}
                _http(
                    client,
                    "POST",
                    f"/api/books/{book}/drafts/{fresh_draft['id']}/approve",
                    new_headers["reviewer"],
                    {"expected_version": fresh_draft["version"]},
                )
                assert (
                    _http(
                        client,
                        "POST",
                        f"/api/books/{book}/exports",
                        new_headers["reviewer"],
                        {
                            "draft_ids": [fresh_draft["id"]],
                            "idempotency_key": "c01-after-restore-export",
                        },
                    )["count"]
                    == 1
                )
                p01_checks["review_export"] = True
                _http(
                    client,
                    "DELETE",
                    f"/api/books/{book}/members/{users['editor']['id']}",
                    new_headers["owner"],
                )
                assert (
                    client.get(f"/api/books/{book}", headers=new_headers["editor"]).status_code
                    == 404
                )
                target_app.state.admin_identity.provider = SupabaseAdmin(
                    restored_identity.origin,
                    restored_identity.admin_key,
                    "https://c01-frontend.example.test",
                    restored_identity.client,
                )
                department = _http(
                    client,
                    "POST",
                    f"/api/companies/{company}/departments",
                    new_headers["owner"],
                    {"name": "虚构恢复后新员工部门"},
                    201,
                )
                new_email = "new-staff-" + uuid4().hex[:8] + "@example.test"
                account = _http(
                    client,
                    "POST",
                    "/api/admin/account-requests",
                    new_headers["owner"],
                    {
                        "idempotency_key": "c01-after-restore-new-account",
                        "name": "虚构恢复后新员工",
                        "email": new_email,
                        "company_id": company,
                        "company_role": "employee",
                        "department_id": department["id"],
                        "provisioning_mode": "password",
                        "initial_password": PASSWORD,
                    },
                    201,
                )
                assert account["request"]["status"] == "completed"
                staff_headers = _headers(restored_identity, new_email, firm)
                assert _http(client, "GET", "/api/me", staff_headers)["user"]["email"] == new_email
                assert client.get(f"/api/books/{book}", headers=staff_headers).status_code == 404
            with psycopg.connect(target["admin"]) as conn:
                final_inventory = pending_inventory(conn)
                for table in (
                    "ocr_jobs",
                    "ocr_attempts",
                    "model_costs",
                    "bridge_outbox",
                    "delivery_receipt_items",
                ):
                    assert final_inventory["tables"][table] == source_inventory["tables"][table]
                assert provider.extract_calls == 1
                p01_checks["no_replay"] = notification_proof == {
                    "channel_calls": 0,
                    "old_lease_settlement": False,
                }
                assert (
                    conn.execute(
                        "SELECT count(*) FROM accounting.recovery_release_audit"
                    ).fetchone()[0]
                    == 1
                )
                current_version = conn.execute("SHOW server_version").fetchone()[0]
                table_count = conn.execute(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema='accounting'"
                ).fetchone()[0]
                migration_max = conn.execute(
                    "SELECT max(version) FROM accounting.schema_migrations"
                ).fetchone()[0]
            evidence = {
                "major": major,
                "source_version": snapshot_version,
                "target_version": current_version,
                "migrations": receipt["migration_count"],
                "source_users": len(source_uuids),
                "originals": len(combined["originals"]),
                "quiesced": True,
                "source_identity_replaced": False,
                "cloud_provider_restored": False,
                "actual_pg_dump_restore": True,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "restore_to_verified_workflow_seconds": round(
                    time.monotonic() - restore_started, 3
                ),
                "elapsed_boundary": "local_drill_no_production_rto_sla",
                "table_count": table_count,
                "migration_max": migration_max,
                "restored_capability_grants": len(manifest["security"]["grants"]),
                "restored_column_grants": sum(
                    grant["column"] is not None for grant in manifest["security"]["grants"]
                ),
                "restore_sequences": receipt["sequences"],
                "scope": "local_signed_identity_and_private_object_protocol_substitutes",
                "p01_checks": p01_checks,
                "combined": combined,
            }
            (tmp_path / f"pg{major}-full-recovery-evidence.json").write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        finally:
            restored_identity.close()
    finally:
        source_identity.close()
        objects.close()
        replacement.close()
