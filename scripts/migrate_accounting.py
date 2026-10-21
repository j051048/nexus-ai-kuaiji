"""Explicit accounting migrations; administrator credentials never reach the app.

Usage: python scripts/migrate_accounting.py migrate | status | provision-login | provision-worker-login
Use ACCOUNTING_ADMIN_DATABASE_URL, and ACCOUNTING_RUNTIME_PASSWORD (or a hidden
interactive password prompt) for provision-login. DSNs/passwords are not logged.
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nexus_backend"))
from app.accounting.database import (  # noqa: E402
    WRITE_LOCK,
    migration_compatibility,
    migration_digest,
    migration_files,
)


def has_compatibility_column(conn):
    return bool(
        conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema='accounting' "
            "AND table_name='schema_migrations' AND column_name='compatibility'"
        ).fetchone()
    )


def admin_connection(url):
    import psycopg
    from psycopg.rows import dict_row

    if not url or not url.startswith(("postgres://", "postgresql://")):
        raise ValueError("请设置 ACCOUNTING_ADMIN_DATABASE_URL 为 PostgreSQL 管理员连接")
    return psycopg.connect(url, autocommit=True, prepare_threshold=None, row_factory=dict_row)


def require_admin(conn):
    role = conn.execute(
        "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
    ).fetchone()
    if not (role["rolsuper"] or role["rolbypassrls"]):
        raise ValueError(
            "迁移账号须具备 BYPASSRLS，以维护固定所有者的权限函数；运行账号不可具备该权限"
        )


def migrate(url):
    with admin_connection(url) as conn:
        require_admin(conn)
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (WRITE_LOCK,))
            conn.execute("CREATE SCHEMA IF NOT EXISTS accounting")
            conn.execute("REVOKE ALL ON SCHEMA accounting FROM PUBLIC")
            conn.execute("""CREATE TABLE IF NOT EXISTS accounting.schema_migrations(
                version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            current = {
                row["version"]: row["checksum"]
                for row in conn.execute("SELECT version,checksum FROM accounting.schema_migrations")
            }
            applied = []
            files = migration_files("postgres")
            known = {int(path.name.split("_", 1)[0]) for path in files}
            if set(current) - known:
                raise ValueError("数据库包含当前代码未知的迁移版本，请先更新代码")
            for path in files:
                version = int(path.name.split("_", 1)[0])
                checksum = migration_digest(path)
                if version in current:
                    if current[version] != checksum:
                        raise ValueError(f"迁移 {version} 校验不符，禁止修改已应用的迁移文件")
                    continue
                conn.execute(path.read_text(encoding="utf-8"), prepare=False)
                if has_compatibility_column(conn):
                    conn.execute(
                        "INSERT INTO accounting.schema_migrations(version,checksum,compatibility) "
                        "VALUES(%s,%s,%s)",
                        (version, checksum, migration_compatibility(path)),
                    )
                else:
                    conn.execute(
                        "INSERT INTO accounting.schema_migrations(version,checksum) VALUES(%s,%s)",
                        (version, checksum),
                    )
                applied.append(version)
            # This metadata table exists before 001; migration makes all tables
            # FORCE RLS. Runtime only reads versions, never changes them.
            conn.execute("ALTER TABLE accounting.schema_migrations ENABLE ROW LEVEL SECURITY")
            conn.execute("ALTER TABLE accounting.schema_migrations FORCE ROW LEVEL SECURITY")
            conn.execute("DROP POLICY IF EXISTS versions_read ON accounting.schema_migrations")
            conn.execute(
                "CREATE POLICY versions_read ON accounting.schema_migrations FOR SELECT TO accounting_app,accounting_system USING(true)"
            )
            conn.execute(
                "GRANT SELECT ON accounting.schema_migrations TO accounting_app,accounting_system"
            )
    return applied


def classify_migrations(current, files):
    """Pure classification so status semantics can be tested without a DB."""
    result = []
    for version, checksum in files:
        if version not in current:
            result.append((version, "缺失"))
        elif current[version] != checksum:
            result.append((version, "校验不符"))
        else:
            result.append((version, "已应用"))
    return result


def status(url):
    with admin_connection(url) as conn:
        exists = conn.execute(
            "SELECT to_regclass('accounting.schema_migrations') AS name"
        ).fetchone()["name"]
        current = (
            {
                row["version"]: row["checksum"]
                for row in conn.execute("SELECT version,checksum FROM accounting.schema_migrations")
            }
            if exists
            else {}
        )
        files = [
            (int(path.name.split("_", 1)[0]), migration_digest(path))
            for path in migration_files("postgres")
        ]
        return classify_migrations(current, files)


def provision_login(url, username, password, *, worker=False):
    import psycopg

    if not username or username in {
        "postgres",
        "accounting_app",
        "accounting_system",
        "accounting_worker",
        "anon",
        "authenticated",
    }:
        raise ValueError("请使用独立运行账号名，如 accounting_runtime")
    if len(password) < 20:
        raise ValueError("数据库运行密码至少 20 个字符")
    with admin_connection(url) as conn:
        require_admin(conn)
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (WRITE_LOCK,))
            if worker:
                capability = conn.execute("""SELECT oid,rolcanlogin,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole
                    FROM pg_roles WHERE rolname='accounting_worker'""").fetchone()
                if not capability:
                    raise ValueError("请先应用队列迁移，再创建后台工作账号")
                inherited = conn.execute(
                    "SELECT 1 FROM pg_auth_members WHERE member=%s", (capability["oid"],)
                ).fetchone()
                if inherited or any(
                    capability[field]
                    for field in (
                        "rolcanlogin",
                        "rolsuper",
                        "rolbypassrls",
                        "rolcreatedb",
                        "rolcreaterole",
                    )
                ):
                    raise ValueError("accounting_worker 已有危险权限；请管理员明确修复角色后重试")
            existing = conn.execute(
                "SELECT rolname FROM pg_roles WHERE rolname=%s", (username,)
            ).fetchone()
            if existing:
                raise ValueError("该运行账号已存在；为避免覆盖已有账号权限，请使用新账号名")
            # psycopg quotes identifiers/literals. The SQL and password are
            # never printed, kept in migration files, or sent as CLI arguments.
            roles = psycopg.sql.SQL(
                "accounting_app,accounting_worker" if worker else "accounting_app,accounting_system"
            )
            conn.execute(
                psycopg.sql.SQL(
                    "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD {} IN ROLE {}"
                ).format(psycopg.sql.Identifier(username), psycopg.sql.Literal(password), roles)
            )
    return username


def provision_projection_login(url, username, password):
    """Create a LOGIN role that has ONLY the projection role, never app/system."""
    import psycopg

    if not username:
        raise ValueError("请提供独立 projection 登录名")
    if len(password) < 20:
        raise ValueError("projection 密码至少 20 个字符")
    with admin_connection(url) as conn:
        require_admin(conn)
        with conn.transaction():
            existing = conn.execute(
                "SELECT rolname FROM pg_roles WHERE rolname=%s", (username,)
            ).fetchone()
            if existing:
                raise ValueError("该 projection 账号已存在")
            conn.execute(
                psycopg.sql.SQL(
                    "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD {} IN ROLE accounting_projection"
                ).format(psycopg.sql.Identifier(username), psycopg.sql.Literal(password))
            )
    return username


def provision_worker_login(url, username, password):
    """Workers can act in authenticated scopes and use bounded queue helpers.

    They have no system identity role, table-wide queue read, or BYPASSRLS.
    """
    return provision_login(url, username, password, worker=True)


def main():
    parser = argparse.ArgumentParser(description="会计 PostgreSQL 显式迁移与受限账号配置")
    parser.add_argument(
        "command",
        choices=[
            "migrate",
            "status",
            "provision-login",
            "provision-worker-login",
            "provision-projection-login",
        ],
    )
    parser.add_argument("--username", default=None)
    parser.add_argument("--strict", action="store_true", help="非全部已应用时返回非零")
    args = parser.parse_args()
    url = os.getenv("ACCOUNTING_ADMIN_DATABASE_URL", "")
    try:
        if args.command == "migrate":
            applied = migrate(url)
            print("迁移完成；新增版本：" + (",".join(map(str, applied)) or "无（已是最新版本）"))
        elif args.command == "status":
            rows = status(url)
            for version, state in rows:
                print(f"{version:03d}: {state}")
            if args.strict and any(state != "已应用" for _, state in rows):
                raise SystemExit(1)
        elif args.command == "provision-login":
            password = os.getenv("ACCOUNTING_RUNTIME_PASSWORD") or getpass.getpass(
                "运行数据库账号密码（隐藏输入）："
            )
            print(
                "已创建受限运行账号："
                + provision_login(url, args.username or "accounting_runtime", password)
            )
        elif args.command == "provision-projection-login":
            password = os.getenv("ACCOUNTING_PROJECTION_PASSWORD") or getpass.getpass(
                "投影数据库账号密码（隐藏输入）："
            )
            print(
                "已创建 projecton 账号："
                + provision_projection_login(
                    url, args.username or "accounting_projection_runtime", password
                )
            )
        else:
            password = os.getenv("ACCOUNTING_WORKER_PASSWORD") or getpass.getpass(
                "后台工作数据库账号密码（隐藏输入）："
            )
            print(
                "已创建受限后台账号："
                + provision_worker_login(
                    url, args.username or "accounting_worker_runtime", password
                )
            )
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print(
            "数据库操作失败；请检查管理员连接、账号权限和迁移。连接信息及驱动异常已隐藏。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
