"""One-time operator bootstrap. Credentials are environment-only or hidden input.

Requires migrated PostgreSQL and ACCOUNTING_ADMIN_DATABASE_URL (operator only),
ACCOUNTING_SUPABASE_URL, ACCOUNTING_SUPABASE_ADMIN_KEY, ACCOUNTING_PUBLIC_APP_URL.
The password is never accepted as a command-line argument or saved to disk.
Existing Supabase identities retain their existing password.
"""

import argparse
import getpass
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nexus_backend"))

from app.auth.admin import SupabaseAdmin  # noqa: E402
from app.accounting.models import FirmMemberGrant  # noqa: E402
from scripts.migrate_accounting import admin_connection, require_admin  # noqa: E402


def bootstrap_owner(conn, user, *, name, firm_name, firm_id=None):
    """The operator DB role is intentionally separate from the runtime role."""
    require_admin(conn)
    timestamp = datetime.now(timezone.utc).isoformat()
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (6420031786281903101,))
        conn.execute("SET LOCAL search_path TO accounting,pg_catalog")
        if firm_id:
            firm = conn.execute("SELECT id,name FROM firms WHERE id=%s", (firm_id,)).fetchone()
            if not firm:
                raise ValueError("指定事务所不存在")
        else:
            firms = conn.execute("SELECT id,name FROM firms ORDER BY id").fetchall()
            if len(firms) > 1:
                raise ValueError("数据库已有多个事务所，请显式指定 --firm-id")
            firm = firms[0] if firms else None
        if firm:
            receipt = conn.execute(
                "SELECT user_id FROM admin_bootstrap_records WHERE firm_id=%s", (firm["id"],)
            ).fetchone()
            if receipt:
                if receipt["user_id"] != user["id"]:
                    raise ValueError("该事务所已完成初始化，请通过现有负责人管理权限")
                return {"firm_id": firm["id"], "user_id": user["id"], "already_bootstrapped": True}
        existing = conn.execute(
            "SELECT active,email FROM users WHERE id=%s", (user["id"],)
        ).fetchone()
        if existing and not existing["active"]:
            raise ValueError("目标账号已停用，初始化不能恢复被停用账号")
        conn.execute(
            "INSERT INTO users(id,name,email) VALUES(%s,%s,%s) ON CONFLICT(id) DO NOTHING",
            (user["id"], name, user["email"].lower()),
        )
        if not firm:
            firm = {"id": uuid4().hex, "name": firm_name}
            conn.execute(
                "SELECT set_config('app.user_id',%s,true),set_config('app.firm_id',%s,true)",
                (user["id"], firm["id"]),
            )
            conn.execute(
                "INSERT INTO firms(id,name,created_by) VALUES(%s,%s,%s)",
                (firm["id"], firm["name"], user["id"]),
            )
        else:
            # Explicit operator recovery uses an existing owner for the native
            # authorization guard. The bootstrap audit records the target user.
            owner = conn.execute(
                "SELECT user_id FROM memberships WHERE firm_id=%s AND role='owner' ORDER BY user_id LIMIT 1",
                (firm["id"],),
            ).fetchone()
            conn.execute(
                "SELECT set_config('app.user_id',%s,true),set_config('app.firm_id',%s,true)",
                (owner["user_id"] if owner else user["id"], firm["id"]),
            )
        current = conn.execute(
            "SELECT role FROM memberships WHERE firm_id=%s AND user_id=%s", (firm["id"], user["id"])
        ).fetchone()
        if not current:
            conn.execute(
                "INSERT INTO memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
                (firm["id"], user["id"]),
            )
        elif current["role"] != "owner":
            conn.execute(
                "UPDATE memberships SET role='owner' WHERE firm_id=%s AND user_id=%s",
                (firm["id"], user["id"]),
            )
        conn.execute(
            "INSERT INTO admin_bootstrap_records(firm_id,user_id,created_at) VALUES(%s,%s,%s)",
            (firm["id"], user["id"], timestamp),
        )
        conn.execute(
            "INSERT INTO audit_events(firm_id,user_id,action,object_id,details,created_at) VALUES(%s,%s,'admin.bootstrap.owner',%s,%s,%s)",
            (
                firm["id"],
                user["id"],
                user["id"],
                '{"method":"explicit_operator_bootstrap"}',
                timestamp,
            ),
        )
        return {"firm_id": firm["id"], "user_id": user["id"], "already_bootstrapped": False}


def main():
    parser = argparse.ArgumentParser(description="一次性初始化事务所负责人；不设置任何永久邮箱后门")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="事务所负责人")
    parser.add_argument("--firm-name", default="会计工作空间")
    parser.add_argument("--firm-id")
    args = parser.parse_args()
    email = FirmMemberGrant.normalize_email(args.email.strip())
    if not 1 <= len(args.name.strip()) <= 120 or not 1 <= len(args.firm_name.strip()) <= 120:
        raise ValueError("姓名和事务所名称须为 1–120 字")
    provider = SupabaseAdmin.from_env()
    provider.validate()
    password = os.environ.pop("ACCOUNTING_BOOTSTRAP_PASSWORD", "")
    if provider.find_user(email) is None and not password:
        password = getpass.getpass("初始密码（隐藏输入，不保存）：")
    user = provider.bootstrap_user(email, args.name.strip(), password)
    password = ""
    with admin_connection(os.getenv("ACCOUNTING_ADMIN_DATABASE_URL", "")) as conn:
        result = bootstrap_owner(
            conn,
            user,
            name=args.name.strip(),
            firm_name=args.firm_name.strip(),
            firm_id=args.firm_id,
        )
    print(
        "负责人初始化完成"
        if not result["already_bootstrapped"]
        else "负责人已初始化，本次未重复授权"
    )
    print("事务所 ID：" + result["firm_id"])
    print("已有账号的原密码保持不变。")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Driver/provider errors can contain DSNs or headers; never echo them.
        print(
            "初始化未完成。请检查迁移 081、管理员数据库连接、身份配置及目标事务所；凭据未输出。",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
