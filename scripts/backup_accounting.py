"""Consistent database + referenced immutable originals, verified new-target restore.

Credentials are read only from environment and never written to a manifest or
printed. A partial failed backup/restore is left for investigation, never erased.
"""

from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

FORMAT_VERSION = 1
ORIGINALS_POLICY_VERSION = 2

# Single registry for every database row that points at a private original.
# A backup must cover all of them; a restore must verify the same set. New
# attachment tables must be added here and to the restore verification test.
ORIGINAL_TABLES = (
    ("business_attachments", "1=1"),
    ("case_attachments", "1=1"),
    ("intake_attachments", "1=1"),
    ("bank_import_files", "1=1"),
    ("migration_files", "1=1"),
    ("material_attachments", "1=1"),
    ("invoice_import_batches", "1=1"),
    # Upload intents are never deleted. Stored/consuming rows may hold an
    # original that has not yet been attached to a business record.
    ("upload_intents", "state IN ('stored','consuming','consumed')"),
)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def empty_destination(path):
    path = Path(path).absolute()
    if path.is_symlink() or any(part.is_symlink() for part in [path, *path.parents]):
        raise ValueError("目标目录不能包含符号链接")
    resolved = path.resolve()
    if (
        resolved == resolved.parent
        or resolved.exists()
        and (not resolved.is_dir() or any(resolved.iterdir()))
    ):
        raise ValueError("只能使用明确指定的新目录或现有空目录，不能覆盖已有资料")
    resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
    return resolved


def safe_child(root, relative):
    relative = Path(relative)
    if (
        relative.is_absolute()
        or any(part in {"..", "."} for part in relative.parts)
        or not relative.parts
    ):
        raise ValueError("备份文件路径非法")
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()) or any(
        part.is_symlink()
        for part in [root / relative, *(root / relative).parents]
        if part.is_relative_to(root)
    ):
        raise ValueError("备份文件不能越出指定目录或经由符号链接")
    return result


def original_rows(conn, dialect):
    """Return deduplicated originals referenced by every registered table."""
    originals = {}
    present = []
    if dialect == "postgres":
        for table, predicate in ORIGINAL_TABLES:
            if (
                conn.execute("SELECT to_regclass(%s)", ("accounting." + table,)).fetchone()[0]
                is None
            ):
                continue
            present.append(table)
            rows = conn.execute(
                f"SELECT storage_key,sha256,size_bytes FROM accounting.{table} WHERE {predicate}"
            )
            for key, digest, size in rows:
                prior = originals.setdefault(
                    key, {"storage_key": key, "sha256": digest, "size_bytes": size}
                )
                if prior["sha256"] != digest or prior["size_bytes"] != size:
                    raise ValueError("同一原件存储键对应不同哈希")
    else:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table, predicate in ORIGINAL_TABLES:
            if table not in tables:
                continue
            present.append(table)
            rows = conn.execute(
                f"SELECT storage_key,sha256,size_bytes FROM {table} WHERE {predicate}"
            )
            for key, digest, size in rows:
                prior = originals.setdefault(
                    key, {"storage_key": key, "sha256": digest, "size_bytes": size}
                )
                if prior["sha256"] != digest or prior["size_bytes"] != size:
                    raise ValueError("同一原件存储键对应不同哈希")
    return present, list(originals.values())


def sqlite_metadata(conn):
    versions = [
        dict(zip(("version", "checksum"), row))
        for row in conn.execute("SELECT version,checksum FROM schema_migrations ORDER BY version")
    ]
    tables, originals = original_rows(conn, "sqlite")
    return versions, originals, tables


def pg_environment(url):
    # libpq URI/options are parsed by psycopg, not interpolated into shell code.
    from psycopg.conninfo import conninfo_to_dict

    values = conninfo_to_dict(url)
    env = os.environ.copy()
    for name, value in values.items():
        key = {
            "dbname": "PGDATABASE",
            "user": "PGUSER",
            "password": "PGPASSWORD",
            "host": "PGHOST",
            "hostaddr": "PGHOSTADDR",
            "port": "PGPORT",
            "sslmode": "PGSSLMODE",
            "sslrootcert": "PGSSLROOTCERT",
            "sslcert": "PGSSLCERT",
            "sslkey": "PGSSLKEY",
            "connect_timeout": "PGCONNECT_TIMEOUT",
        }.get(name)
        if key:
            env[key] = str(value)
    return env


def pg_tool(name, args, url):
    executable = shutil.which(name)
    if not executable:
        raise ValueError(f"未找到 {name}，请安装与数据库兼容的 PostgreSQL 客户端")
    result = subprocess.run(
        [executable, *args],
        env=pg_environment(url),
        capture_output=True,
        text=True,
        timeout=600,
        shell=False,
    )
    if result.returncode:
        # Vendor stderr may echo credentials, URL or host. Keep it private.
        raise ValueError(
            f"{name} 未成功；请在本机检查数据库授权、客户端版本和目标，未导出任何连接信息"
        )


def copy_originals(storage_root, target, originals, *, storage_store=None):
    storage_root = Path(storage_root).resolve() if storage_store is None else None
    files = []
    for item in originals:
        if storage_store is None:
            source = safe_child(storage_root, item["storage_key"])
            if (
                not source.is_file()
                or source.stat().st_size != item["size_bytes"]
                or sha256(source) != item["sha256"]
            ):
                raise ValueError("数据库快照引用的原件不存在或哈希不符，备份未完成")
        else:
            # Read through the same private backend as the API. It bounds bytes
            # and checks snapshot SHA/size; no signed/public object URLs.
            data = storage_store.read(item)
        relative = "originals/" + item["storage_key"].replace("\\", "/")
        destination = safe_child(target, relative)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with destination.open("xb") as dst:
            if storage_store is None:
                with source.open("rb") as src:
                    shutil.copyfileobj(src, dst)
            else:
                dst.write(data)
            dst.flush()
            os.fsync(dst.fileno())
        destination.chmod(0o600)
        if sha256(destination) != item["sha256"]:
            raise ValueError("复制后的原件哈希不符")
        files.append({"path": relative, "sha256": item["sha256"], "size_bytes": item["size_bytes"]})
    return files


def backup(database, storage_root, destination, *, storage_store=None):
    if storage_store is not None:
        storage_store.verify_private()
    target = empty_destination(destination)
    postgres = database.startswith(("postgres://", "postgresql://"))
    if postgres:
        import psycopg

        with psycopg.connect(database) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            snapshot = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]
            versions = [
                {"version": row[0], "checksum": row[1]}
                for row in conn.execute(
                    "SELECT version,checksum FROM accounting.schema_migrations ORDER BY version"
                )
            ]
            original_tables, originals = original_rows(conn, "postgres")
            dump = target / "database.dump"
            pg_tool(
                "pg_dump",
                [
                    "--format=custom",
                    "--schema=accounting",
                    "--no-password",
                    "--snapshot",
                    snapshot,
                    "--file",
                    str(dump),
                ],
                database,
            )
            files = copy_originals(storage_root, target, originals, storage_store=storage_store)
    else:
        source = Path(database).resolve()
        if not source.is_file() or source.is_symlink():
            raise ValueError("SQLite 源数据库不存在或不是普通文件")
        dump = target / "database.sqlite"
        with (
            closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src,
            closing(sqlite3.connect(dump)) as snapshot,
        ):
            src.backup(snapshot)
            if (
                snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or snapshot.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise ValueError("SQLite 快照完整性验证失败")
            versions, originals, original_tables = sqlite_metadata(snapshot)
        files = copy_originals(storage_root, target, originals, storage_store=storage_store)
    dump.chmod(0o600)
    files.insert(0, {"path": dump.name, "sha256": sha256(dump), "size_bytes": dump.stat().st_size})
    manifest = {
        "format_version": FORMAT_VERSION,
        "originals_policy_version": ORIGINALS_POLICY_VERSION,
        "dialect": "postgres" if postgres else "sqlite",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "migrations": versions,
        "database_schema_version": versions[-1]["version"] if versions else 0,
        "original_tables": original_tables,
        "files": files,
        "consistency": "exported_repeatable_read_snapshot"
        if postgres
        else "sqlite_backup_snapshot",
        "contains_credentials": False,
    }
    with (target / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    (target / "manifest.json").chmod(0o600)
    return manifest


def verify(directory):
    directory = Path(directory).resolve()
    path = safe_child(directory, "manifest.json")
    if path.stat().st_size > 50_000_000:
        raise ValueError("备份清单过大")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != FORMAT_VERSION or manifest.get("dialect") not in {
        "sqlite",
        "postgres",
    }:
        raise ValueError("不支持的备份格式")
    names = set()
    expected_database = "database.sqlite" if manifest["dialect"] == "sqlite" else "database.dump"
    for item in manifest["files"]:
        name = item["path"]
        if name in names or (name != expected_database and not name.startswith("originals/")):
            raise ValueError("备份清单文件重复或非预期")
        names.add(name)
        source = safe_child(directory, name)
        if (
            not source.is_file()
            or source.stat().st_size != item["size_bytes"]
            or sha256(source) != item["sha256"]
        ):
            raise ValueError("备份文件缺失或哈希不符")
    if expected_database not in names:
        raise ValueError("备份缺少数据库")
    return manifest


def restore(directory, destination, postgres_target=None):
    manifest = verify(directory)
    source, target = Path(directory).resolve(), empty_destination(destination)
    if manifest["dialect"] == "postgres":
        if not postgres_target:
            raise ValueError("PostgreSQL 恢复需要显式指定新空数据库")
        import psycopg

        with psycopg.connect(postgres_target) as conn:
            # Extensions' system objects are allowed, application/user objects
            # in ANY schema are not. Never restore over an existing accounting DB.
            count = conn.execute(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.deptype='e')"
            ).fetchone()[0]
            custom_schemas = conn.execute(
                "SELECT count(*) FROM pg_namespace n WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname NOT IN ('public','information_schema') AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_namespace'::regclass AND d.objid=n.oid AND d.deptype='e')"
            ).fetchone()[0]
            custom_functions = conn.execute(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e')"
            ).fetchone()[0]
            if count or custom_schemas or custom_functions:
                raise ValueError("PostgreSQL 目标不是空数据库，禁止覆盖")
        pg_tool(
            "pg_restore",
            [
                "--no-owner",
                "--no-privileges",
                "--single-transaction",
                "--exit-on-error",
                "--no-password",
                "--dbname",
                pg_environment(postgres_target).get("PGDATABASE", ""),
                str(source / "database.dump"),
            ],
            postgres_target,
        )
    elif postgres_target:
        raise ValueError("SQLite 备份不能按 PostgreSQL 恢复")
    for item in manifest["files"]:
        if item["path"] == "database.dump":
            continue
        destination_file = safe_child(target, item["path"])
        destination_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with safe_child(source, item["path"]).open("rb") as src, destination_file.open("xb") as dst:
            shutil.copyfileobj(src, dst)
        destination_file.chmod(0o600)
        if sha256(destination_file) != item["sha256"]:
            raise ValueError("恢复原件哈希不符")
    if manifest["dialect"] == "sqlite":
        with closing(sqlite3.connect(target / "database.sqlite")) as conn:
            if (
                conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or conn.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise ValueError("恢复数据库完整性检查失败")
            migrations, originals, _ = sqlite_metadata(conn)
            if migrations != manifest["migrations"]:
                raise ValueError("恢复数据库迁移清单不同")
            for item in originals:
                file = safe_child(target, "originals/" + item["storage_key"].replace("\\", "/"))
                if file.stat().st_size != item["size_bytes"] or sha256(file) != item["sha256"]:
                    raise ValueError("恢复后的数据库与原件不一致")
    else:
        with psycopg.connect(postgres_target) as conn:
            migrations = [
                {"version": row[0], "checksum": row[1]}
                for row in conn.execute(
                    "SELECT version,checksum FROM accounting.schema_migrations ORDER BY version"
                )
            ]
            if migrations != manifest["migrations"]:
                raise ValueError("恢复数据库迁移清单不同")
            _, originals = original_rows(conn, "postgres")
            for item in originals:
                file = safe_child(target, "originals/" + item["storage_key"].replace("\\", "/"))
                if (
                    not file.is_file()
                    or file.stat().st_size != item["size_bytes"]
                    or sha256(file) != item["sha256"]
                ):
                    raise ValueError("恢复后的数据库与原件不一致")
    receipt = {
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "dialect": manifest["dialect"],
        "verified_files": len(manifest["files"]),
        "migration_count": len(manifest["migrations"]),
        "serving_enabled": False,
    }
    with (target / "restore-verification.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("backup")
    create.add_argument("--database-env", default="ACCOUNTING_DATABASE_URL")
    source_storage = create.add_mutually_exclusive_group(required=True)
    source_storage.add_argument("--storage-root", help="本地原件的持久目录")
    source_storage.add_argument(
        "--configured-storage",
        action="store_true",
        help="使用 ACCOUNTING_STORAGE_* 的私有共享存储；不创建或删除对象",
    )
    create.add_argument("--destination", required=True)
    check = sub.add_parser("verify")
    check.add_argument("directory")
    recover = sub.add_parser("restore")
    recover.add_argument("directory")
    recover.add_argument("--destination", required=True)
    recover.add_argument("--postgres-target-env")
    args = parser.parse_args()
    try:
        if args.command == "backup":
            database = os.getenv(args.database_env)
            if not database:
                raise ValueError("指定数据库环境变量未配置")
            storage = None
            if args.configured_storage:
                sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nexus_backend"))
                from app.storage.factory import create_attachment_store

                storage = create_attachment_store()
            result = backup(database, args.storage_root, args.destination, storage_store=storage)
            print(
                json.dumps(
                    {
                        "completed": True,
                        "dialect": result["dialect"],
                        "verified_files": len(result["files"]),
                        "migration_count": len(result["migrations"]),
                    },
                    ensure_ascii=False,
                )
            )
        elif args.command == "verify":
            result = verify(args.directory)
            print(
                json.dumps(
                    {
                        "verified": True,
                        "files": len(result["files"]),
                        "migrations": len(result["migrations"]),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            result = restore(
                args.directory,
                args.destination,
                os.getenv(args.postgres_target_env) if args.postgres_target_env else None,
            )
            print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        # File paths may contain names; external errors may contain credentials.
        print(
            str(error)
            if isinstance(error, ValueError)
            else "备份或恢复未完成，请在本机检查路径、授权和客户端环境",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
