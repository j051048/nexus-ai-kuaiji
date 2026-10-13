"""Read-only deployment preflight; no migrations, uploads or model requests."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sys
from typing import Callable, Mapping
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nexus_backend"))
sys.path.insert(0, str(ROOT / "scripts"))


@dataclass(frozen=True)
class Check:
    name: str
    state: str
    detail: str


def _check(name: str, operation: Callable[[], object], detail: str, failure: str) -> Check:
    try:
        operation()
    except Exception:
        # Driver and HTTP error text may contain secrets or customer data.
        return Check(name, "error", failure)
    return Check(name, "ok", detail)


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("invalid")


def _missing(env: Mapping[str, str], names: tuple[str, ...]) -> list[str]:
    return [name for name in names if not env.get(name, "").strip()]


def _valid_origins(env: Mapping[str, str]) -> None:
    hosts = [
        item.strip() for item in env.get("ACCOUNTING_ALLOWED_HOSTS", "").split(",") if item.strip()
    ]
    origins = [
        item.strip()
        for item in env.get("ACCOUNTING_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    ]
    _require(bool(hosts and origins) and "*" not in hosts and "*" not in origins)
    for host in hosts:
        _require(
            "*" not in host
            and not any(c.isspace() for c in host)
            and "://" not in host
            and "/" not in host
        )
    for origin in origins:
        parsed = urlsplit(origin)
        _require(
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and parsed.username is None
            and parsed.password is None
        )


def inspect_configuration(
    env: Mapping[str, str], role: str, *, require_ai: bool = False
) -> list[Check]:
    from run_accounting_service import validate_runtime

    result = [
        _check(
            "runtime",
            lambda: validate_runtime(role, env),
            "生产模式、单一受限 DSN、独立后台与远端原件配置正确",
            "核对 ACCOUNTING_ENV、当前角色 DSN/TLS、ACCOUNTING_STORAGE_BACKEND、SEED/EMBEDDED_WORKER；运行容器不能有管理员或其他服务 DSN",
        )
    ]
    if role == "api":
        from app.auth.supabase import AuthSettings

        missing = _missing(env, ("ACCOUNTING_SUPABASE_URL", "ACCOUNTING_SUPABASE_PUBLISHABLE_KEY"))
        result.append(
            _check(
                "auth",
                lambda: _require(AuthSettings.from_env(env).mode == "supabase"),
                "Supabase 登录静态配置通过",
                "缺少 " + ", ".join(missing)
                if missing
                else "Supabase 登录配置无效；公钥字段禁止 secret/service_role",
            )
        )
        result.append(
            _check(
                "public_routes",
                lambda: _valid_origins(env),
                "域名和 HTTPS 网页来源白名单已配置",
                "设置 ACCOUNTING_ALLOWED_HOSTS 和 ACCOUNTING_ALLOWED_ORIGINS 为明确域名及 HTTPS 来源",
            )
        )
    from app.storage.settings import StorageSettings

    missing = _missing(
        env,
        (
            "ACCOUNTING_STORAGE_SUPABASE_URL",
            "ACCOUNTING_STORAGE_BUCKET",
            "ACCOUNTING_STORAGE_SERVER_KEY",
        ),
    )
    result.append(
        _check(
            "storage",
            lambda: _require(StorageSettings.from_env(env).backend == "supabase"),
            "Supabase 原件存储静态配置通过；私有性需 --online 确认",
            "缺少 " + ", ".join(missing)
            if missing
            else "存储配置无效；检查项目根地址、私有 bucket 名与服务端 secret/service_role 密钥",
        )
    )
    if role != "migration":
        from app.intelligence.provider import AISettings

        try:
            settings = AISettings.from_env(env)
        except Exception:
            result.append(
                Check(
                    "models",
                    "error",
                    "模型配置无效；场景 BASE_URL/API_KEY 必须配对，地址须 HTTPS；不会外发请求",
                )
            )
        else:
            for scenario, label, fields in (
                (
                    "ocr",
                    "OCR",
                    "ACCOUNTING_AI_OCR_BASE_URL/API_KEY/MODEL（或共用 BASE_URL/API_KEY 与 VISION_MODEL）",
                ),
                (
                    "journal",
                    "分录",
                    "ACCOUNTING_AI_JOURNAL_BASE_URL/API_KEY/MODEL（或共用 BASE_URL/API_KEY）",
                ),
                (
                    "chat",
                    "对话",
                    "ACCOUNTING_AI_CHAT_BASE_URL/API_KEY/MODEL（或共用 BASE_URL/API_KEY）",
                ),
            ):
                available = settings.for_scenario(scenario).available
                result.append(
                    Check(
                        f"model_{scenario}",
                        "ok" if available else "error" if require_ai else "warning",
                        f"{label}场景已配置；尚未验证供应商输出与计费"
                        if available
                        else f"{label}场景未配置：{fields}；人工流程可用，自动化暂不可用",
                    )
                )
            for kind in ("OCR", "PROPOSAL", "CHAT"):
                from app.operations.pricing import Rate

                keys = tuple(
                    f"ACCOUNTING_AI_{kind}_{direction}_CNY_PER_MILLION"
                    for direction in ("INPUT", "OUTPUT")
                )
                missing = _missing(env, keys)
                if missing:
                    result.append(
                        Check(
                            f"pricing_{kind.lower()}",
                            "warning",
                            "未知报价：" + ", ".join(missing) + "；费用配额启用前须填写真实报价",
                        )
                    )
                else:
                    result.append(
                        _check(
                            f"pricing_{kind.lower()}",
                            lambda keys=keys: Rate(*(env[key].strip() for key in keys)),
                            "显式人民币单价格式通过；未核验供应商账单",
                            "报价无效："
                            + ", ".join(keys)
                            + "；应为非负十进制字符串，最多 6 位小数",
                        )
                    )
    result.append(
        Check(
            "business_setup",
            "manual",
            "仍须建立事务所、客户企业、账套/期间、人员授权与科目模板；企业开启模型处理并配置预算。自检不会创建业务数据",
        )
    )
    return result


def verify_database(env: Mapping[str, str], role: str) -> None:
    """Catalog-only READ ONLY transaction; never initialize Database/SQLite."""
    import psycopg
    from psycopg.rows import dict_row
    from app.accounting.database import migration_digest, migration_files

    key = "ACCOUNTING_DATABASE_URL" if role == "api" else "ACCOUNTING_WORKER_DATABASE_URL"
    with psycopg.connect(
        env[key], autocommit=True, prepare_threshold=None, connect_timeout=10, row_factory=dict_row
    ) as conn:
        conn.execute("BEGIN READ ONLY")
        try:
            conn.execute("SET LOCAL statement_timeout='10s'")
            principal = conn.execute(
                "SELECT oid,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            _require(
                bool(principal)
                and not any(
                    principal[field]
                    for field in ("rolsuper", "rolbypassrls", "rolcreatedb", "rolcreaterole")
                )
            )
            memberships = conn.execute(
                """SELECT rolname FROM pg_roles
                WHERE rolname IN ('accounting_app','accounting_system','accounting_worker')
                  AND pg_has_role(%s,oid,'MEMBER')""",
                (principal["oid"],),
            ).fetchall()
            actual = {row["rolname"] for row in memberships}
            expected = {
                "accounting_app",
                "accounting_system" if role == "api" else "accounting_worker",
            }
            _require(actual == expected)
            dangerous = conn.execute(
                """SELECT 1 FROM pg_roles WHERE
                (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole)
                AND pg_has_role(%s,oid,'MEMBER') LIMIT 1""",
                (principal["oid"],),
            ).fetchone()
            _require(dangerous is None)
            conn.execute("SET LOCAL ROLE accounting_app")
            rows = conn.execute(
                "SELECT version,checksum FROM accounting.schema_migrations"
            ).fetchall()
            expected_files = {
                int(path.name.split("_", 1)[0]): migration_digest(path)
                for path in migration_files("postgres")
            }
            _require({row["version"]: row["checksum"] for row in rows} == expected_files)
            insecure = conn.execute("""SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='accounting' AND c.relkind IN ('r','p')
                  AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity) LIMIT 1""").fetchone()
            _require(insecure is None)
        finally:
            conn.rollback()


def verify_auth(env: Mapping[str, str]) -> None:
    import httpx
    from app.auth.supabase import AuthSettings

    settings = AuthSettings.from_env(env)
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
        response = client.get(
            settings.supabase_url + "/auth/v1/settings",
            headers={"apikey": settings.publishable_key},
        )
        _require(response.status_code == 200 and isinstance(response.json(), dict))


def verify_storage(env: Mapping[str, str]) -> None:
    from app.storage.settings import StorageSettings
    from app.storage.supabase import SupabaseBlobBackend

    backend = SupabaseBlobBackend(StorageSettings.from_env(env))
    try:
        metadata = backend.verify_private()
        _require(metadata.get("private") is True)
    finally:
        close = getattr(backend, "close", None)
        if close:
            close()


def inspect_online(env: Mapping[str, str], role: str) -> list[Check]:
    checks = [
        _check(
            "database_online",
            lambda: verify_database(env, role),
            "只读查询通过：受限角色、迁移摘要和强制 RLS",
            "数据库只读校验失败：检查当前服务 DSN/TLS、账号角色、显式迁移及强制 RLS；驱动错误已隐藏",
        ),
        _check(
            "storage_online",
            lambda: verify_storage(env),
            "bucket 存在且 public=false；未上传、下载或创建对象",
            "原件存储只读校验失败：检查服务端密钥、bucket 存在且为 private；HTTP 内容已隐藏",
        ),
    ]
    if role == "api":
        checks.append(
            _check(
                "auth_online",
                lambda: verify_auth(env),
                "Auth settings 可访问；未创建用户或执行登录",
                "Auth settings 不可访问：检查 Supabase 项目地址、公钥、网络；HTTP 内容已隐藏",
            )
        )
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="部署只读自检，不迁移、不调用模型、不更改客户数据")
    parser.add_argument("--role", choices=("api", "worker", "assistant", "migration"))
    parser.add_argument("--env-file", help="显式加载该私密文件，不自动读取 .env")
    parser.add_argument(
        "--online", action="store_true", help="额外只读查询数据库、Auth settings 及 bucket metadata"
    )
    parser.add_argument("--require-ai", action="store_true", help="三个模型场景未配置时以失败退出")
    parser.add_argument("--json", action="store_true", help="仅输出不含配置值的检查报告")
    args = parser.parse_args(argv)
    if args.env_file:
        path = Path(args.env_file).resolve()
        if not path.is_file():
            parser.error("指定配置文件不存在")
        from dotenv import load_dotenv

        load_dotenv(path, override=False)
    role = args.role or os.getenv("ACCOUNTING_SERVICE_ROLE", "api").strip().lower()
    if role not in {"api", "worker", "assistant", "migration"}:
        parser.error("ACCOUNTING_SERVICE_ROLE 无效")
    checks = inspect_configuration(os.environ, role, require_ai=args.require_ai)
    if args.online:
        if any(check.state == "error" for check in checks):
            checks.append(Check("online", "skipped", "存在静态配置错误，未发送网络请求"))
        else:
            checks.extend(inspect_online(os.environ, role))
    if args.json:
        print(
            json.dumps(
                {
                    "role": role,
                    "online": args.online,
                    "checks": [asdict(check) for check in checks],
                },
                ensure_ascii=False,
            )
        )
    else:
        for check in checks:
            print(f"[{check.state}] {check.name}: {check.detail}")
    return 1 if any(check.state == "error" for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
