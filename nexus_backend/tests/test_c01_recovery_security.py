"""A restored database needs exact, bounded ACL reconstruction, never GRANT ALL."""

import pytest

from scripts import recovery_security as security
from scripts.release_recovery_hold import REQUIRED_CHECKS, release_local_hold


@pytest.mark.parametrize(
    "maximum,last,called,valid",
    [(10, 10, True, True), (10, 10, False, False), (10, 3, True, False), (None, 1, False, True)],
)
def test_sequence_next_id_is_verified_without_advancing(maximum, last, called, valid):
    class Result:
        def __init__(self, data):
            self.data = data

        def fetchall(self):
            return self.data

        def fetchone(self):
            return self.data

    class Connection:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            return Result(
                [("audit_id_seq", "audit_events", "id", 1)]
                if self.calls == 1
                else ((maximum,) if self.calls == 2 else (last, called))
            )

    if valid:
        assert security.verify_sequences(Connection())["nextval_called"] is False
    else:
        with pytest.raises(ValueError, match="序列"):
            security.verify_sequences(Connection())


def sample():
    return {
        "version": 1,
        "structure_sha256": "a" * 64,
        "grants": [
            {
                "kind": "table",
                "object": "ocr_jobs",
                "column": "status",
                "role": "accounting_app",
                "privilege": "SELECT",
            },
        ],
    }


def test_column_acl_is_not_promoted_to_whole_table():
    assert security.validate_security_manifest(sample())["grants"][0]["column"] == "status"


@pytest.mark.parametrize(
    "field,value",
    [
        ("role", "PUBLIC"),
        ("role", "runtime_login"),
        ("privilege", "ALL"),
        ("privilege", "TRIGGER"),
        ("object", "public.ocr_jobs"),
        ("object", "ocr_jobs; GRANT ALL"),
        ("column", "*"),
    ],
)
def test_unapproved_acl_is_rejected(field, value):
    manifest = sample()
    manifest["grants"][0][field] = value
    with pytest.raises(ValueError):
        security.validate_security_manifest(manifest)


def test_unknown_manifest_fields_are_not_credentials_container():
    manifest = sample()
    manifest["credentials"] = "never allowed"
    with pytest.raises(ValueError):
        security.validate_security_manifest(manifest)


def test_duplicate_acl_or_unknown_manifest_fields_fail_closed():
    manifest = sample()
    manifest["grants"] *= 2
    with pytest.raises(ValueError):
        security.validate_security_manifest(manifest)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user@prod.example/database",
        "postgresql://user@127.0.0.1:5432/ak01_local_ak05_x",
        "postgresql://user@127.0.0.1:25497/production",
        "sqlite://local",
    ],
)
def test_local_release_never_connects_to_unapproved_target(url, tmp_path):
    with pytest.raises(ValueError, match="只允许"):
        release_local_hold(
            url,
            tmp_path,
            operator="local-test",
            reason="drill",
            checks=dict.fromkeys(REQUIRED_CHECKS, True),
            inventory_sha256="a" * 64,
        )


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_release_requires_explicit_completed_checks_before_connection(tmp_path, value):
    checks = dict.fromkeys(REQUIRED_CHECKS, True)
    checks["identity"] = value
    with pytest.raises(ValueError, match="必须全部完成"):
        release_local_hold(
            "postgresql://user@127.0.0.1:25497/ak01_local_ak05_local",
            tmp_path,
            operator="local-test",
            reason="drill",
            checks=checks,
            inventory_sha256="a" * 64,
        )
