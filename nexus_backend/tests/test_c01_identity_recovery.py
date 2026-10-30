"""Persistent offline identity substitute; never evidence of a GoTrue cloud restore."""

import hashlib
import json
import base64

import jwt
import pytest
import httpx

from app.auth import AuthError
from c01_identity_fixture import (
    LocalIdentityFixture,
    LocalPrivateObjectFixture,
    verify_joint_bundle,
)


PASSWORD = "C01-fixture-only-password-2026"


def test_password_login_is_signed_and_live_user_is_checked_on_every_call(tmp_path):
    with LocalIdentityFixture.create(tmp_path / "identity") as identity:
        user = identity.provision("accountant@example.test", PASSWORD, totp=True)
        assert user["id"] and user["email_confirmed_at"]
        login = identity.login(user["email"], PASSWORD)
        verifier = identity.authenticator()
        assert verifier.authenticate(login["access_token"]).user_id == user["id"]
        assert verifier.authenticate(login["access_token"]).aal == "aal1"
        assert identity.online_reads == 2
        identity.revoke(login["access_token"])
        with pytest.raises(AuthError):
            verifier.authenticate(login["access_token"])


def test_wrong_password_unconfirmed_and_client_aal_cannot_grant_access(tmp_path):
    with LocalIdentityFixture.create(tmp_path / "identity") as identity:
        user = identity.provision("staff@example.test", PASSWORD)
        with pytest.raises(httpx.HTTPStatusError):
            identity.login(user["email"], "incorrect-password")
        login = identity.client.post(
            identity.origin + "/auth/v1/token?grant_type=password",
            json={"email": user["email"], "password": PASSWORD, "aal": "aal2", "role": "owner"},
        ).json()
        assert identity.authenticator().authenticate(login["access_token"]).aal == "aal1"
        pending = identity.provision("pending@example.test", PASSWORD, confirmed=False)
        with pytest.raises(httpx.HTTPStatusError):
            identity.login(pending["email"], PASSWORD)


def test_totp_really_verifies_and_old_session_cannot_be_reused(tmp_path):
    with LocalIdentityFixture.create(tmp_path / "identity") as identity:
        user = identity.provision("owner@example.test", PASSWORD, totp=True)
        login = identity.login(user["email"], PASSWORD)
        challenge = identity.challenge(login["access_token"])
        denied = identity.client.post(
            identity.origin + f"/auth/v1/factors/{user['factor_id']}/verify",
            headers={"Authorization": "Bearer " + login["access_token"]},
            json={"challenge_id": challenge["id"], "code": "abcdef"},
        )
        assert denied.status_code == 422
        upgraded = identity.step_up(login["access_token"])
        verifier = identity.authenticator()
        assert verifier.authenticate(upgraded["access_token"]).aal == "aal2"
        with pytest.raises(AuthError):
            verifier.authenticate(login["access_token"])


def test_backup_restores_uuid_password_factor_and_revocation_to_independent_directory(tmp_path):
    bundle = tmp_path / "private" / "identity.bundle"
    with LocalIdentityFixture.create(tmp_path / "source") as source:
        user = source.provision("owner@example.test", PASSWORD, totp=True)
        old = source.login(user["email"], PASSWORD)
        source.revoke(old["access_token"])
        receipt = source.backup(bundle)
        assert receipt["sha256"] == hashlib.sha256(bundle.read_bytes()).hexdigest()
        assert PASSWORD.encode() not in bundle.read_bytes()
        assert source.protected(bundle)
    with LocalIdentityFixture.restore(
        bundle,
        tmp_path / "restored",
        expected_sha256=receipt["sha256"],
        origin="https://c01-restored-auth.example.test",
    ) as restored:
        login = restored.login(user["email"], PASSWORD)
        verifier = restored.authenticator()
        assert verifier.authenticate(login["access_token"]).user_id == user["id"]
        assert (
            verifier.authenticate(restored.step_up(login["access_token"])["access_token"]).aal
            == "aal2"
        )
        assert restored.session_state(old["access_token"])["revoked"]
        with pytest.raises(AuthError):
            verifier.authenticate(old["access_token"])


def test_bundle_tamper_and_existing_destination_are_rejected(tmp_path):
    with LocalIdentityFixture.create(tmp_path / "source") as identity:
        identity.provision("owner@example.test", PASSWORD)
        bundle = tmp_path / "private" / "identity.bundle"
        receipt = identity.backup(bundle)
        existing = tmp_path / "existing"
        existing.mkdir()
        (existing / "keep").write_text("keep", encoding="utf-8")
        with pytest.raises(ValueError):
            LocalIdentityFixture.restore(bundle, existing, expected_sha256=receipt["sha256"])
        bundle.write_bytes(bundle.read_bytes() + b" ")
        with pytest.raises(ValueError):
            LocalIdentityFixture.restore(
                bundle, tmp_path / "bad", expected_sha256=receipt["sha256"]
            )
        assert not (tmp_path / "bad").exists()
        assert (existing / "keep").read_text(encoding="utf-8") == "keep"


def test_signed_but_wrong_issuer_expired_and_malformed_tokens_fail_closed(tmp_path):
    with LocalIdentityFixture.create(tmp_path / "identity") as identity:
        user = identity.provision("owner@example.test", PASSWORD)
        login = identity.login(user["email"], PASSWORD)
        verifier = identity.authenticator()
        claims = jwt.decode(login["access_token"], options={"verify_signature": False})
        for change in ({"iss": "https://other.example.test/auth/v1"}, {"exp": 1}, {"sub": "bad"}):
            forged = jwt.encode(
                {**claims, **change},
                identity.signing_key,
                algorithm="ES256",
                headers={"kid": identity.key_id},
            )
            with pytest.raises(AuthError):
                verifier.authenticate(forged)
        with pytest.raises(AuthError):
            verifier.authenticate("malformed")


def test_ordinary_public_key_cannot_create_users_or_restore_privileges(tmp_path):
    with LocalIdentityFixture.create(tmp_path / "identity") as identity:
        denied = identity.client.post(
            identity.origin + "/auth/v1/admin/users",
            headers={"Authorization": "Bearer " + identity.publishable_key},
            json={"email": "attacker@example.test", "password": PASSWORD, "email_confirm": True},
        )
        assert denied.status_code == 403
        assert identity.user_count() == 0


def test_identity_bundle_contains_no_plaintext_password_and_uses_explicit_format(tmp_path):
    with LocalIdentityFixture.create(tmp_path / "identity") as identity:
        identity.provision("owner@example.test", PASSWORD)
        bundle = tmp_path / "private" / "identity.bundle"
        identity.backup(bundle)
        content = json.loads(bundle.read_text(encoding="utf-8"))
        assert content["format"] == "c01-offline-identity-v1"
        assert content["scope"] == "synthetic_identity_provider_only"
        assert PASSWORD not in json.dumps(content)


def test_persistent_private_object_adapter_recovery_and_unauthorized_download(tmp_path):
    source = LocalPrivateObjectFixture(tmp_path / "source-private")
    target = LocalPrivateObjectFixture(
        tmp_path / "target-private", "https://c01-restored-objects.example.test"
    )
    try:
        store = source.store()
        store.verify_private()
        raw = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC"
        )
        metadata = store.put("synthetic.png", "image/png", raw)
        store.retain(metadata)
        assert store.read(dict(metadata)) == raw
        key = metadata["storage_key"]
        assert (
            source.client.get(
                source.origin + "/storage/v1/object/" + source.bucket + "/" + key
            ).status_code
            == 403
        )
        target.restore_original(key, raw)
        restored = target.store()
        restored.verify_private()
        assert restored.read(dict(metadata)) == raw
        assert restored.root is None
        assert source.directory != target.directory
    finally:
        source.close()
        target.close()


def test_joint_manifest_rejects_missing_identity_and_tampered_accounting_before_restore(tmp_path):
    with LocalIdentityFixture.create(tmp_path / "identity") as identity:
        user = identity.provision("owner@example.test", PASSWORD)
        bundle = tmp_path / "private" / "identity.bundle"
        receipt = identity.backup(bundle)
        manifest = tmp_path / "manifest.json"
        manifest.write_text('{"format":"test-quiesced"}', encoding="utf-8")
        joint = {
            "quiesced": True,
            "identity_sha256": receipt["sha256"],
            "accounting_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "user_uuids": [user["id"]],
            "identity_contains_sensitive_material": True,
        }
        assert verify_joint_bundle(joint, bundle, manifest, {user["id"]}) == [user["id"]]
        with pytest.raises(ValueError, match="identity"):
            verify_joint_bundle(joint, bundle, manifest, {"11111111-1111-4111-8111-111111111111"})
        manifest.write_bytes(manifest.read_bytes() + b" ")
        with pytest.raises(ValueError, match="accounting"):
            verify_joint_bundle(joint, bundle, manifest, {user["id"]})


def test_private_signed_upload_uses_real_adapter_and_is_single_object_immutable(tmp_path):
    objects = LocalPrivateObjectFixture(tmp_path / "signed-objects")
    key = "ab/" + "a" * 32
    try:
        signed = objects.store().backend.sign_upload(key, 900)
        assert signed["expires_in"] == 900
        assert objects.server_key not in signed["url"]
        uploaded = objects.client.put(
            signed["url"], content=b"synthetic original", headers={"x-upsert": "false"}
        )
        assert uploaded.status_code == 200
        assert objects.store().backend.read(key, 100) == b"synthetic original"
        repeated = objects.client.put(
            signed["url"], content=b"tampered", headers={"x-upsert": "false"}
        )
        assert repeated.status_code == 409
        assert objects.store().backend.read(key, 100) == b"synthetic original"
    finally:
        objects.close()


def test_private_signed_upload_signing_requires_server_key_and_valid_scope(tmp_path):
    objects = LocalPrivateObjectFixture(tmp_path / "signed-objects")
    base = objects.origin + "/storage/v1/object/upload/sign/"
    key = "ab/" + "a" * 32
    try:
        assert (
            objects.client.post(
                base + objects.bucket + "/" + key, json={"expiresIn": 900}
            ).status_code
            == 403
        )
        headers = {"apikey": objects.server_key}
        assert (
            objects.client.post(
                base + "other-bucket/" + key, headers=headers, json={"expiresIn": 900}
            ).status_code
            == 404
        )
        assert (
            objects.client.post(
                base + objects.bucket + "/bad-key", headers=headers, json={"expiresIn": 900}
            ).status_code
            == 422
        )
        for expiry in (True, -1, 0, 7201, "900"):
            assert (
                objects.client.post(
                    base + objects.bucket + "/" + key, headers=headers, json={"expiresIn": expiry}
                ).status_code
                == 422
            )
        assert (
            objects.client.post(
                base + objects.bucket + "/" + key,
                headers={**headers, "x-upsert": "true"},
                json={"expiresIn": 900},
            ).status_code
            == 409
        )
    finally:
        objects.close()


def test_private_signed_upload_token_cannot_change_bucket_key_or_allow_overwrite(tmp_path):
    objects = LocalPrivateObjectFixture(tmp_path / "signed-objects")
    key = "ab/" + "a" * 32
    try:
        signed = objects.store().backend.sign_upload(key, 900)
        for changed in (
            signed["url"].replace(objects.bucket, "other-bucket"),
            signed["url"].replace(key, "ab/" + "b" * 32),
            signed["url"].split("?token=")[0] + "?token=forged",
        ):
            assert objects.client.put(
                changed, content=b"bad", headers={"x-upsert": "false"}
            ).status_code in {403, 404}
        assert (
            objects.client.put(
                signed["url"], content=b"bad", headers={"x-upsert": "true"}
            ).status_code
            == 409
        )
        assert not (objects.directory / key).exists()
    finally:
        objects.close()


def test_private_signed_upload_expiration_is_checked_before_storage(tmp_path, monkeypatch):
    import c01_identity_fixture

    objects = LocalPrivateObjectFixture(tmp_path / "signed-objects")
    key = "ab/" + "a" * 32
    try:
        monkeypatch.setattr(c01_identity_fixture.time, "time", lambda: 1000)
        signed = objects.store().backend.sign_upload(key, 1)
        monkeypatch.setattr(c01_identity_fixture.time, "time", lambda: 1002)
        assert (
            objects.client.put(
                signed["url"], content=b"late", headers={"x-upsert": "false"}
            ).status_code
            == 403
        )
        assert not (objects.directory / key).exists()
    finally:
        objects.close()
