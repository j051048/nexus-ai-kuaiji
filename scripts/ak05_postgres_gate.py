"""Fail-closed PG16/17 gate using fresh databases and the existing local runner.

Only ``prepare --allow-create-local-databases`` mutates a disposable cluster.
No DSN is accepted from environment variables, and no database is dropped.
Reports are safe to upload; generated config files contain local passwords and
must stay outside the reports directory and outside Git.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from uuid import uuid4
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    "unified": ["test_ak05_unified_postgres.py"],
    "process": ["test_ak05_process_postgres.py"],
    "attempt": ["test_ak01_postgres.py"],
    "regression": [
        "test_migration115_write_gates_postgres.py",
        "test_projection_dispatch_postgres.py",
        "test_batch2_concurrency_postgres.py",
        "test_migration116_proposal_helpers_postgres.py",
        "test_business_postgres.py",
        "test_ak05_pg_scope.py",
        "test_stage15_mixed_queue_postgres.py",
        "test_c01_r3a_postgres.py",
        "test_queue_fairness_postgres.py",
        "test_ak05_projection_cleanup_postgres.py",
        "test_c01_auxiliary_postgres.py",
        "test_c01_bank_evidence_postgres.py",
    ],
}
REQUIRED_CASES = json.loads(
    Path(__file__).with_name("ak05_postgres_required_cases.json").read_text(encoding="utf-8")
)
LOGIN_ROLES = {"app": "ak05_runtime", "worker": "ak05_worker", "projection": "ak05_projection"}
CAPABILITIES = {
    "app": {"accounting_app", "accounting_system"},
    "worker": {"accounting_app", "accounting_worker"},
    "projection": {"accounting_projection"},
}


class GateError(ValueError):
    pass


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_config(config, *, admin_only=False):
    keys = ["admin"] if admin_only else ["admin", *LOGIN_ROLES]
    targets = []
    for key in keys:
        try:
            target = urlsplit(config[key])
            valid = (
                target.scheme in {"postgres", "postgresql"}
                and target.hostname == "127.0.0.1"
                and target.port
                and target.port != 5432
                and target.path.startswith("/ak01_local_ak05_")
                and not target.query
                and not target.fragment
                and target.username
                and unquote(target.path) == target.path
                and all(char.isalnum() or char in "_/" for char in target.path)
                and target.path.count("/") == 1
            )
            if key != "admin":
                valid = valid and target.username == LOGIN_ROLES[key]
            if not valid:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise GateError("需要完整、显式、独立端口的本地 AK05 数据库与受限身份配置") from None
        targets.append(target)
    if len({(t.hostname, t.port, t.path) for t in targets}) != 1:
        raise GateError("同一分组的四种身份必须指向同一隔离数据库")
    return {key: config[key] for key in keys}


def validate_report_manifest(manifest):
    if (
        manifest.get("version") != 1
        or type(manifest.get("major")) is not int
        or manifest["major"] not in {16, 17}
    ):
        raise GateError("门禁只接受版本 1 manifest 和 PG16/17")
    if set(manifest.get("groups", {})) != set(GROUPS):
        raise GateError("四个验收分组必须完整提供，禁止跳过或选择性运行")
    configs = {
        group: validate_config(read_json(path)) for group, path in manifest["groups"].items()
    }
    if len({urlsplit(config["admin"]).path for config in configs.values()}) != len(GROUPS):
        raise GateError("不同分组必须使用独立数据库，避免领取夹具相互污染")
    if (
        len(
            {
                (urlsplit(config["admin"]).hostname, urlsplit(config["admin"]).port)
                for config in configs.values()
            }
        )
        != 1
    ):
        raise GateError("同一版本四组必须使用同一隔离实例")
    return configs


def validate_junit(path, group):
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        raise GateError("JUnit 缺失或损坏") from None
    suites = list(root.iter("testsuite"))
    cases = list(root.iter("testcase"))
    if root.tag not in {"testsuites", "testsuite"} or not suites or not cases:
        raise GateError("JUnit 没有实际执行用例")
    for node in [root, *suites]:
        for key in ("skipped", "errors", "failures", "disabled", "deselected"):
            try:
                if int(node.get(key, "0")) != 0:
                    raise GateError("JUnit 存在未通过或未执行的用例")
            except ValueError:
                raise GateError("JUnit 计数格式错误") from None
        if "tests" in node.attrib:
            try:
                if int(node.attrib["tests"]) != len(list(node.iter("testcase"))):
                    raise GateError("JUnit 用例计数不一致")
            except ValueError:
                raise GateError("JUnit 用例计数格式错误") from None
    if any(list(root.iter(tag)) for tag in ("failure", "error", "skipped")):
        raise GateError("JUnit 实际结果存在失败、错误或跳过")
    identities = [f"{case.get('classname')}::{case.get('name')}" for case in cases]
    if Counter(identities) != Counter(REQUIRED_CASES[group]):
        raise GateError("实际用例与冻结门槛不一致：缺少、重复或替换了验收用例")
    return {"passed": len(cases), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def local_connection(url, *, autocommit=False):
    import psycopg

    return psycopg.connect(url, connect_timeout=5, autocommit=autocommit)


def assert_server_and_roles(config, major, *, roles=True):
    with local_connection(config["admin"]) as conn:
        actual = int(conn.execute("SHOW server_version_num").fetchone()[0])
        if actual // 10000 != major:
            raise GateError("服务器实际 PostgreSQL 主版本与本轮门槛不符")
        for setting in ("fsync", "synchronous_commit", "full_page_writes"):
            if conn.execute("SHOW " + setting).fetchone()[0] != "on":
                raise GateError("并发恢复验收要求持久化设置开启")
        if roles:
            for key, login in LOGIN_ROLES.items():
                row = conn.execute(
                    "SELECT rolcanlogin,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolinherit FROM pg_roles WHERE rolname=%s",
                    (login,),
                ).fetchone()
                if not row or not row[0] or any(row[1:]):
                    raise GateError("测试登录角色不满足最小权限")
                memberships = {
                    row[0]
                    for row in conn.execute(
                        "SELECT parent.rolname FROM pg_auth_members m JOIN pg_roles child ON child.oid=m.member JOIN pg_roles parent ON parent.oid=m.roleid WHERE child.rolname=%s",
                        (login,),
                    )
                }
                if memberships != CAPABILITIES[key]:
                    raise GateError("测试登录角色的能力成员关系不符合门槛")
            for capability in set().union(*CAPABILITIES.values()):
                row = conn.execute(
                    "SELECT rolcanlogin,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=%s",
                    (capability,),
                ).fetchone()
                if not row or any(row):
                    raise GateError("能力角色必须 NOLOGIN 且无特权")
    return actual


def with_database(url, name):
    target = urlsplit(url)
    return urlunsplit((target.scheme, target.netloc, "/" + name, "", ""))


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        if os.name != "nt":
            os.chmod(path, 0o600)
        json.dump(value, stream, indent=2)
        stream.write("\n")


def prepare(source, major, destination, *, allow_create=False, groups=None):
    if not allow_create:
        raise GateError("创建隔离库必须显式提供 --allow-create-local-databases")
    # Domain gates may reuse preparation for one group; the AK05 CLI still
    # prepares and requires all four frozen groups, without changing its gate.
    groups = tuple(GROUPS) if groups is None else tuple(groups)
    if not groups or len(set(groups)) != len(groups) or any(g not in GROUPS for g in groups):
        raise GateError("隔离库分组必须来自既有门禁且不得为空或重复")
    validate_config(source, admin_only=True)
    assert_server_and_roles(source, major, roles=False)
    sys.path[:0] = [str(ROOT), str(ROOT / "nexus_backend")]
    from scripts.migrate_accounting import (
        migrate,
        provision_login,
        provision_worker_login,
        provision_projection_login,
        status,
    )
    from psycopg import sql

    with local_connection(source["admin"]) as conn:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT rolname FROM pg_roles WHERE rolname=ANY(%s)", (list(LOGIN_ROLES.values()),)
            )
        }
    if existing and existing != set(LOGIN_ROLES.values()):
        raise GateError("隔离实例已有不完整测试账号；不自动修改既有权限")
    if existing:
        validate_config(source)
        assert_server_and_roles(source, major)
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    run = uuid4().hex[:12]
    configs = {}
    for group in groups:
        name = f"ak01_local_ak05_ci_pg{major}_{run}_{group}"
        admin = with_database(source["admin"], name)
        with local_connection(source["admin"], autocommit=True) as conn:
            conn.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name))
            )
        migrate(admin)
        if existing:
            config = {key: with_database(source[key], name) for key in ["admin", *LOGIN_ROLES]}
        else:
            config = {"admin": admin}
            target = urlsplit(admin)
            for key, provision in [
                ("app", provision_login),
                ("worker", provision_worker_login),
                ("projection", provision_projection_login),
            ]:
                password = secrets.token_urlsafe(32)
                provision(admin, LOGIN_ROLES[key], password)
                config[key] = (
                    f"postgresql://{LOGIN_ROLES[key]}:{quote(password)}@127.0.0.1:{target.port}/{name}"
                )
            source.update(config)
            existing = set(LOGIN_ROLES.values())
        assert_server_and_roles(config, major)
        for value in config.values():
            with local_connection(value) as conn:
                conn.execute("SELECT 1")
        if migrate(admin) or any(state != "已应用" for _, state in status(admin)):
            raise GateError("隔离库迁移重入或校验和失败")
        path = destination / f"{group}.json"
        private_json(path, config)
        configs[group] = str(path)
    manifest = {"version": 1, "major": major, "groups": configs}
    private_json(destination / "manifest.json", manifest)
    return destination / "manifest.json"


def sanitized(text, config):
    for value in config.values():
        target = urlsplit(value)
        for secret in (value, target.password, unquote(target.password or "")):
            if secret:
                text = text.replace(secret, "[local-test-secret-redacted]")
    return text


def candidate_fingerprint():
    files = []
    for folder in (
        "nexus_backend/app",
        "nexus_backend/migrations",
        "nexus_backend/tests",
        "scripts",
        ".github/workflows",
    ):
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and path.suffix in {".py", ".sql", ".json", ".yml"}:
                files.append(
                    (
                        path.relative_to(ROOT).as_posix(),
                        hashlib.sha256(path.read_bytes()).hexdigest(),
                    )
                )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True
    ).stdout.strip()
    migration_files = [item for item in files if item[0].startswith("nexus_backend/migrations/")]
    return {
        "head": head,
        "source_files": len(files),
        "source_sha256": hashlib.sha256(json.dumps(files).encode()).hexdigest(),
        "migration_files": len(migration_files),
        "migration_sha256": hashlib.sha256(json.dumps(migration_files).encode()).hexdigest(),
    }


def run_gate(manifest, output):
    configs = validate_report_manifest(manifest)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "major": manifest["major"],
        "status": "failed",
        "groups": {},
        "candidate": candidate_fingerprint(),
    }
    sys.path[:0] = [str(ROOT), str(ROOT / "nexus_backend")]
    from scripts.migrate_accounting import status

    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(
            ("ACCOUNTING_", "SUPABASE_", "OPENAI_", "DEEPSEEK_", "ZEABUR_", "VERCEL_", "PG")
        )
        and key.upper()
        not in {
            "DATABASE_URL",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "PYTEST_ADDOPTS",
            "PYTEST_PLUGINS",
        }
    }
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    for group, files in GROUPS.items():
        config = configs[group]
        actual = assert_server_and_roles(config, manifest["major"])
        if any(state != "已应用" for _, state in status(config["admin"])):
            raise GateError("迁移未全部应用或校验和不符，禁止继续验收")
        xml = output / f"{group}.xml"
        command = [
            sys.executable,
            str(ROOT / "scripts/test_accounting_postgres_isolated.py"),
            "--config",
            manifest["groups"][group],
            *(str(ROOT / "nexus_backend/tests" / file) for file in files),
            "-q",
            "--junitxml=" + str(xml),
        ]
        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=600,
            )
            log = sanitized(result.stdout + result.stderr, config)
            (output / f"{group}.log").write_text(log, encoding="utf-8")
            if xml.exists():
                xml.write_text(sanitized(xml.read_text(encoding="utf-8"), config), encoding="utf-8")
            checked = validate_junit(xml, group)
            if result.returncode != 0:
                raise GateError("pytest 返回非零状态")
            report["groups"][group] = {"status": "passed", "server_version_num": actual, **checked}
        except (GateError, subprocess.TimeoutExpired) as error:
            report["groups"][group] = {
                "status": "failed",
                "reason": str(error) if isinstance(error, GateError) else "验收进程超过 600 秒",
            }
        print(f"PG{manifest['major']} {group}: {report['groups'][group]['status']}", flush=True)
    report["status"] = (
        "passed"
        if all(value["status"] == "passed" for value in report["groups"].values())
        else "failed"
    )
    if candidate_fingerprint() != report["candidate"]:
        report["status"] = "failed"
        report["reason"] = "验收期间候选源码或迁移发生变化，结果不可合并为同一候选通过"
    report["passed"] = sum(value.get("passed", 0) for value in report["groups"].values())
    report["required_cases_sha256"] = hashlib.sha256(
        Path(__file__).with_name("ak05_postgres_required_cases.json").read_bytes()
    ).hexdigest()
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report["status"] == "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--admin-config", required=True)
    prep.add_argument("--major", type=int, choices=[16, 17], required=True)
    prep.add_argument("--out", required=True)
    prep.add_argument("--allow-create-local-databases", action="store_true")
    run = sub.add_parser("run")
    run.add_argument("--manifest", required=True)
    run.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            path = prepare(
                read_json(args.admin_config),
                args.major,
                args.out,
                allow_create=args.allow_create_local_databases,
            )
            print("已准备四个独立本地数据库；manifest: " + str(path))
            return 0
        return 0 if run_gate(read_json(args.manifest), args.out) else 1
    except Exception:
        print(
            "PG 门禁失败；请检查隔离目标、角色、迁移和报告。驱动连接详情及密码已隐藏。",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
