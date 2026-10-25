"""Real dump/reparse must preserve constraints and failed-restore isolation."""

import os
from pathlib import Path
from urllib.parse import urlsplit
import pytest
import psycopg
from scripts import backup_accounting, recovery_security
from scripts.migrate_accounting import migrate
from c01_gotrue_fixture import create_auth_database, BRIDGE
from c01_wsl_bridge import call

pytestmark = pytest.mark.skipif(
    os.getenv("C01_SECURITY_ROUNDTRIP") != "1", reason="Explicit isolated PG restore needed"
)


@pytest.fixture
def roundtrip(tmp_path, monkeypatch):
    source = create_auth_database(17, target=False)
    target = create_auth_database(17, target=True)
    migrate(source["admin"])

    def pg_tool(name, args, url):
        config = source if url == source["admin"] else target
        assert url in (source["admin"], target["admin"])
        data = {"target": config["bridge_target"], "database": urlsplit(url).path[1:]}
        if name == "pg_dump":
            data.update(
                output=args[-1], schema="accounting", snapshot=args[args.index("--snapshot") + 1]
            )
        else:
            data.update(input=args[-1])
        call(BRIDGE, name, **data)

    monkeypatch.setattr(backup_accounting, "pg_tool", pg_tool)
    originals = tmp_path / "private"
    originals.mkdir()
    backup = tmp_path / "backup"
    backup_accounting.backup(source["admin"], originals, backup)
    return source, target, backup, tmp_path / "restored"


def test_real_pg_dump_constraint_reparse_has_same_security_structure(roundtrip):
    source, target, backup, restored = roundtrip
    receipt = backup_accounting.restore(backup, restored, target["admin"], restore_permissions=True)
    assert receipt["security_verified"] is True
    with psycopg.connect(source["admin"]) as before, psycopg.connect(target["admin"]) as after:
        assert recovery_security.capture_security(before) == recovery_security.capture_security(
            after
        )
        baseline = recovery_security.capture_security(after)
        after.execute(
            "ALTER TABLE accounting.migration_jobs ADD CONSTRAINT c01_changed_logic CHECK ((max_attempts>=1 OR max_attempts<=10) AND attempt_count<=max_attempts)"
        )
        assert (
            recovery_security.capture_security(after)["structure_sha256"]
            != baseline["structure_sha256"]
        )
        after.rollback()
        assert after.execute(
            "SELECT prosecdef FROM pg_proc WHERE oid='accounting.actor_active()'::regprocedure"
        ).fetchone() == (True,)
        after.execute("ALTER FUNCTION accounting.actor_active() OWNER TO accounting_app")
        with pytest.raises(ValueError, match="所有者"):
            recovery_security.restore_security(after, baseline)
        after.rollback()


def test_failed_security_restore_keeps_database_hold(roundtrip, monkeypatch):
    _, target, backup, restored = roundtrip

    def reject(*args):
        raise ValueError("injected security validation failure")

    monkeypatch.setattr(recovery_security, "restore_security", reject)
    with pytest.raises(ValueError, match="injected"):
        backup_accounting.restore(backup, restored, target["admin"], restore_permissions=True)
    with psycopg.connect(target["admin"]) as conn:
        assert conn.execute(
            "SELECT hold FROM accounting.restore_state WHERE instance_id='restored'"
        ).fetchone() == (True,)
