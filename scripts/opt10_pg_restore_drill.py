"""Destructive, disposable-database drill for OPT10 encrypted PostgreSQL restore.

Run only against an isolated acceptance cluster. It backs up the configured
database, drops the accounting schema, restores from the encrypted backup and
compares table/row facts. Usage:
  python scripts/opt10_pg_restore_drill.py --config tmp_test/fsc02-pg/env17/unified.json
"""

import argparse
import base64
import json
import os
import shutil
from pathlib import Path

import psycopg

from scripts import backup_accounting

ORIGINAL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAACgAAAAUCAIAAABwJOjsAAAANUlEQVR4nO3NwQEAIAwCMWT/"
    "nfFrF7g+JAvkJNEGr6xqDDKZvRpjzFVTY4y5amqMMVfp8/gCAeEDJZQenysAAAAASUVORK5CYII="
)


def counts(url):
    with psycopg.connect(url) as conn:
        companies = conn.execute("SELECT count(*) FROM accounting.companies").fetchone()[0]
        migrations = conn.execute(
            "SELECT count(*), max(version) FROM accounting.schema_migrations"
        ).fetchone()
        tables = conn.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='accounting' AND c.relkind='r'"
        ).fetchone()[0]
    return {
        "companies": companies,
        "migrations": migrations[0],
        "max_version": migrations[1],
        "tables": tables,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", default="tmp_test/opt10-pg-restore-drill")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    url = config["admin"]
    assert shutil.which("pg_dump") and shutil.which("pg_restore"), "PostgreSQL client tools missing"
    key = base64.b64encode(b"d" * 32).decode("ascii")
    os.environ["ACCOUNTING_BACKUP_KEY"] = key
    os.environ["ACCOUNTING_BACKUP_KEY_ID"] = "pg-restore-drill"
    root = Path(args.out).resolve()
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    storage = root / "storage"
    storage.mkdir()
    with psycopg.connect(url) as conn:
        _, originals = backup_accounting.original_rows(conn, "postgres")
    for item in originals:
        path = storage / item["storage_key"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(ORIGINAL)
    before = counts(url)
    backup = root / "backup"
    backup_accounting.backup(url, storage, backup)
    with psycopg.connect(url) as conn:
        conn.execute("DROP SCHEMA accounting CASCADE")
        conn.commit()
    restored = root / "restored"
    receipt = backup_accounting.restore(backup, restored, postgres_target=url)
    after = counts(url)
    assert after == before, (before, after)
    assert receipt["serving_enabled"] is False and receipt["outbound_disabled"] is True
    assert (restored / "restore-verification.json").is_file()
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert any(
        item["path"] == "database.dump" and item.get("encrypted") for item in manifest["files"]
    )
    result = {
        "status": "passed",
        "before": before,
        "after": after,
        "receipt": receipt["verified_files"],
        "serving_enabled": receipt["serving_enabled"],
        "outbound_disabled": receipt["outbound_disabled"],
    }
    (root / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
