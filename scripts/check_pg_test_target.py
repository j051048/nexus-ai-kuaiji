"""Fail CI when the PostgreSQL test target was not actually provisioned."""
from __future__ import annotations

import os
import sys


def main() -> int:
    admin = os.getenv("ACCOUNTING_ADMIN_DATABASE_URL", "")
    app = os.getenv("ACCOUNTING_TEST_DATABASE_URL", "")
    if not admin or not app:
        print("缺少 PostgreSQL 测试 DSN")
        return 1
    import psycopg

    with psycopg.connect(admin) as conn:
        count = conn.execute(
            "SELECT count(*) FROM accounting.schema_migrations"
        ).fetchone()[0]
        if count < 1:
            print("schema_migrations 为空")
            return 1
        missing = conn.execute(
            "SELECT count(*) FROM pg_roles WHERE rolname IN ('accounting_app','accounting_worker')"
        ).fetchone()[0]
        if missing != 2:
            print("受限运行角色未创建")
            return 1
    # The restricted runtime DSN must connect, but must not be superuser.
    with psycopg.connect(app) as conn:
        superuser = conn.execute("SELECT current_setting('is_superuser')").fetchone()[0]
        if superuser == "on":
            print("运行账号不应是超级用户")
            return 1
    print(f"PostgreSQL 测试目标就绪，迁移版本数 {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
