"""Real fixed-version GoTrue, real auth schema backup, no cloud service."""

import os
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.auth import AuthError
from c01_gotrue_fixture import RealGoTrue, create_auth_database, restore_auth_database

pytestmark = pytest.mark.skipif(
    os.getenv("C01_GOTRUE_ACCEPT") != "1",
    reason="Explicit local GoTrue artifact and PG clusters required",
)


@pytest.mark.parametrize("major", [16, 17])
def test_real_gotrue_identity_password_mfa_and_revocation_survive_restore(tmp_path, major):
    source = create_auth_database(major, target=False)
    target = create_auth_database(major, target=True)
    password = "C01-local-only-real-auth-2026!"
    user_id = str(uuid4())
    with RealGoTrue(source, tmp_path / "source", 25511) as original:
        user = original.provision(user_id, "real-owner@example.test", password)
        assert user["id"] == user_id and user["email_confirmed_at"]
        old = original.login(user["email"], password)
        verifier = original.authenticator()
        assert verifier.authenticate(old["access_token"]).user_id == user_id
        factor = original.enroll_factor(old["access_token"])
        elevated = original.verify_factor(old["access_token"], factor)
        assert verifier.authenticate(elevated["access_token"]).aal == "aal2"
        with pytest.raises(Exception):
            original.login(user["email"], "wrong password")
        archive = tmp_path / "private-auth.dump"
        proof = original.backup(archive)
    assert archive.exists() and password.encode() not in archive.read_bytes()
    restore_auth_database(target, archive, proof)
    with RealGoTrue(target, tmp_path / "target", 25512) as recovered:
        fresh = recovered.login(user["email"], password)
        assert fresh["user"]["id"] == user_id
        assert recovered.authenticator().authenticate(fresh["access_token"]).aal == "aal1"
        assert (
            recovered.authenticator()
            .authenticate(recovered.verify_factor(fresh["access_token"], factor)["access_token"])
            .aal
            == "aal2"
        )
        with pytest.raises(AuthError):
            recovered.authenticator().authenticate(elevated["access_token"])
        with pytest.raises(ValueError):
            recovered._request(
                "POST",
                "/token?grant_type=refresh_token",
                body={"refresh_token": elevated["refresh_token"]},
            )
        recovered.ban(user_id)
        with pytest.raises(AuthError):
            recovered.authenticator().authenticate(fresh["access_token"])


@pytest.mark.skipif(
    os.getenv("C01_GOTRUE_APP") != "1", reason="Requires completed full recovery evidence"
)
@pytest.mark.parametrize("major", [16, 17])
def test_real_restored_gotrue_drives_restored_accounting_api(tmp_path, monkeypatch, major):
    import httpx
    import psycopg
    from fastapi.testclient import TestClient
    from c01_identity_fixture import LocalPrivateObjectFixture
    from c01_gotrue_fixture import ROOT, _LocalAuthTransport
    from test_c01_full_recovery_postgres import _read_config, _app, _http, PASSWORD
    from test_intelligence_workflow import FakeProvider

    pointers_path = Path(
        os.getenv(
            "C01_ARTIFACT_POINTERS_PATH", str(ROOT / ".codex/c01-final-pg/combined-artifacts.json")
        )
    ).resolve()
    if not any(pointers_path.is_relative_to(ROOT / name) for name in ("tmp_test", ".codex")):
        raise ValueError("Identity recovery pointers must be local private artifacts")
    pointers = json.loads(pointers_path.read_text(encoding="utf-8"))
    artifact = Path(pointers[str(major)])
    fixture = json.loads((artifact / "source_fixture.json").read_text(encoding="utf-8"))
    evidence = json.loads(
        (artifact / f"pg{major}-full-recovery-evidence.json").read_text(encoding="utf-8")
    )
    restored_config = _read_config(major, True)
    with psycopg.connect(restored_config["admin"]) as conn:
        users = conn.execute(
            "SELECT id,email FROM accounting.users WHERE active=1 ORDER BY id"
        ).fetchall()
        assert len(users) >= len(fixture["users"])
    source = create_auth_database(major, target=False)
    target = create_auth_database(major, target=True)
    with RealGoTrue(source, tmp_path / "source", 25511) as original:
        for user_id, email in users:
            original.provision(user_id, email, PASSWORD)
        owner = fixture["users"]["owner"]
        old = original.login(owner["email"], PASSWORD)
        factor = original.enroll_factor(old["access_token"])
        original.verify_factor(old["access_token"], factor)
        auth_dump = tmp_path / "auth-private.dump"
        proof = original.backup(auth_dump)
    restore_auth_database(target, auth_dump, proof)
    objects = LocalPrivateObjectFixture(
        tmp_path / "objects", "https://c01-real-restored-objects.example.test"
    )
    for item in evidence["combined"]["originals"]:
        file = artifact / "restored-accounting" / item["path"]
        raw = file.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == item["sha256"]
        objects.restore_original(item["path"].removeprefix("originals/"), raw)
    try:
        with RealGoTrue(target, tmp_path / "target", 25512) as recovered:
            recovered.factors[owner["id"]] = factor
            headers = {}
            for name, user in fixture["users"].items():
                login = recovered.login(user["email"], PASSWORD)
                if name == "owner":
                    login = recovered.step_up(login["access_token"])
                headers[name] = {
                    "Authorization": "Bearer " + login["access_token"],
                    "X-Firm-Id": fixture["firm_id"],
                }
            app = _app(
                restored_config,
                recovered,
                objects,
                monkeypatch,
                FakeProvider(),
                tmp_path / "attachments",
            )
            with TestClient(app, base_url="http://127.0.0.1") as client:
                for name in ("owner", "reviewer", "employee"):
                    assert (
                        _http(client, "GET", "/api/me", headers[name])["user"]["id"]
                        == fixture["users"][name]["id"]
                    )
                assert client.get("/api/me", headers=headers["outsider"]).status_code == 403
                assert client.get("/api/session").status_code == 404
                # Actual restored GoTrue identities also exercise the newly
                # recovered review proof, expired/revoked scopes and contact-only access.
                current = fixture["current_review"]
                evidence = _http(
                    client,
                    "GET",
                    f"/api/books/{current['book_id']}/legacy-reviews/draft/{current['subject_id']}",
                    headers["reviewer"],
                )
                assert evidence["proof"]["proof_kind"] == "current_recheck"
                for name in ("editor", "customer"):
                    assert (
                        client.get(
                            f"/api/books/{current['book_id']}", headers=headers[name]
                        ).status_code
                        == 404
                    )
                assert (
                    client.get(
                        f"/api/books/{current['delivery_book_id']}", headers=headers["customer"]
                    ).status_code
                    == 404
                )
                package = _http(
                    client,
                    "GET",
                    f"/api/books/{current['delivery_book_id']}/delivery/monthly-package/{current['package_id']}",
                    headers["customer"],
                )["package"]
                assert package["manifest_sha256"] == current["manifest_sha256"]
                data = client.get(
                    f"/api/business/attachments/{fixture['attachment_id']}/content",
                    headers=headers["employee"],
                )
                assert data.status_code == 200 and data.content
                assert client.get(
                    f"/api/business/attachments/{fixture['attachment_id']}/content",
                    headers=headers["outsider"],
                ).status_code in (403, 404)
                assert (
                    client.get(
                        f"/api/books/{fixture['other_book_id']}", headers=headers["editor"]
                    ).status_code
                    == 404
                )
                book = _http(
                    client,
                    "POST",
                    "/api/books",
                    headers["owner"],
                    {
                        "company_id": fixture["company_id"],
                        "name": "真实Auth恢复后虚构验证",
                        "period": "2026-10",
                    },
                    201,
                )
                book_id = book["id"]
                _http(
                    client,
                    "PUT",
                    f"/api/books/{book_id}/members/{fixture['users']['reviewer']['id']}",
                    headers["owner"],
                    {"role": "reviewer"},
                )
                _http(
                    client,
                    "POST",
                    f"/api/books/{book_id}/imports",
                    headers["owner"],
                    {
                        "source_name": "真实Auth恢复测试",
                        "rows": [
                            {
                                "external_id": "gotrue-restore-proof",
                                "date": "2026-10-13",
                                "description": "办公采购",
                                "amount": "1.20",
                                "direction": "expense",
                                "evidence_ref": "虚构恢复验证",
                            }
                        ],
                    },
                )
                _http(client, "POST", f"/api/books/{book_id}/suggestions", headers["owner"], {})
                draft = next(
                    row
                    for row in _http(client, "GET", f"/api/books/{book_id}", headers["reviewer"])[
                        "drafts"
                    ]
                    if row["status"] == "draft"
                )
                export = {"draft_ids": [draft["id"]], "idempotency_key": "real-auth-restore-export"}
                assert client.post(
                    f"/api/books/{book_id}/exports", headers=headers["owner"], json=export
                ).status_code in (403, 409, 422)
                _http(
                    client,
                    "POST",
                    f"/api/books/{book_id}/drafts/{draft['id']}/approve",
                    headers["reviewer"],
                    {"expected_version": draft["version"]},
                )
                assert (
                    _http(
                        client, "POST", f"/api/books/{book_id}/exports", headers["reviewer"], export
                    )["count"]
                    == 1
                )
                _http(
                    client,
                    "DELETE",
                    f"/api/books/{book_id}/members/{fixture['users']['reviewer']['id']}",
                    headers["owner"],
                )
                assert (
                    client.get(f"/api/books/{book_id}", headers=headers["reviewer"]).status_code
                    == 404
                )
    finally:
        objects.close()
