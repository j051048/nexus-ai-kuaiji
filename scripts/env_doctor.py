"""开发环境体检：把 requirements 与真实安装对齐，并复现历史红名的根因。

用法：python scripts/env_doctor.py
退出码 0=环境可用于全量测试；1=存在阻塞项（会给出修复命令）。
"""

from __future__ import annotations

import importlib
import importlib.metadata as metadata
import inspect
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENT_FILES = ("nexus_backend/requirements.txt", "nexus_backend/requirements-dev.txt")
# 依赖名 → import 名；缺失时按 FAIL 报告，不抛栈。
MODULES = {
    "fastapi": "fastapi",
    "starlette": "starlette",
    "uvicorn": "uvicorn",
    "pydantic": "pydantic",
    "httpx": "httpx",
    "pytest": "pytest",
    "psycopg": "psycopg",
    "psycopg_pool": "psycopg_pool",
    "defusedxml": "defusedxml",
    "PyJWT": "jwt",
    "python-dotenv": "dotenv",
    "python-multipart": "multipart",
    "Pillow": "PIL",
    "pypdfium2": "pypdfium2",
}
SPECIFIER = re.compile(r"^(==|>=|<=|~=|!=|>|<)\s*([0-9][0-9A-Za-z.+-]*)$")
REQUIREMENT = re.compile(r"^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?\s*(.*)$")


def parse_requirement(line):
    """把一行 requirement 解析为 (发行名, [(操作符, 版本), ...])；注释/空行返回 None。"""
    text = line.split("#", 1)[0].strip()
    if not text:
        return None
    if text.startswith("-"):  # pip 指令行（-r/-e/--hash 等）不参与版本对齐
        return None
    matched = REQUIREMENT.match(text)
    if not matched:
        raise ValueError(f"无法解析依赖行：{line.strip()}")
    name, _, rest = matched.groups()
    specifiers = []
    for part in rest.split(","):
        part = part.strip()
        if not part:
            continue
        found = SPECIFIER.match(part)
        if not found:
            raise ValueError(f"无法解析版本约束：{part}（{line.strip()}）")
        specifiers.append((found.group(1), found.group(2)))
    return name, specifiers


def version_tuple(value):
    parts = []
    for segment in re.split(r"[.+-]", value):
        digits = re.match(r"^(\d+)", segment)
        parts.append(int(digits.group(1)) if digits else 0)
    return tuple(parts)


def satisfies(installed, specifiers):
    current = version_tuple(installed)
    for operator, target in specifiers:
        wanted = version_tuple(target)
        matched = {
            "==": current == wanted,
            "!=": current != wanted,
            ">=": current >= wanted,
            "<=": current <= wanted,
            ">": current > wanted,
            "<": current < wanted,
            "~=": current >= wanted and current[: len(wanted) - 1] == wanted[: len(wanted) - 1],
        }[operator]
        if not matched:
            return False
    return True


def format_specifiers(specifiers):
    return ",".join(operator + target for operator, target in specifiers) or "任意版本"


def inspect_environment():
    """Machine-readable dependency probes, with no credentials, paths or DB I/O."""
    checks = []
    for relative in REQUIREMENT_FILES:
        for line in (ROOT / relative).read_text(encoding="utf-8").splitlines():
            try:
                parsed = parse_requirement(line)
                if not parsed:
                    continue
                name, specifiers = parsed
                installed = metadata.version(name)
                checks.append(
                    {
                        "name": name,
                        "version": installed,
                        "passed": satisfies(installed, specifiers),
                        "code": "dependency_version",
                    }
                )
            except Exception:
                checks.append(
                    {"name": "dependency", "passed": False, "code": "dependency_probe_failed"}
                )
    for distribution, module in MODULES.items():
        try:
            importlib.import_module(module)
            checks.append({"name": distribution, "passed": True, "code": "import"})
        except Exception:
            checks.append({"name": distribution, "passed": False, "code": "import_failed"})
    try:
        from starlette.testclient import TestClient

        supported = "client" in inspect.signature(TestClient.__init__).parameters
    except Exception:
        supported = False
    checks.append({"name": "TestClient", "passed": supported, "code": "http_fixture_capability"})
    return {
        "schema": "local-environment-v1",
        "scope": "local_dependencies_only",
        "status": "passed" if all(c["passed"] for c in checks) else "blocked",
        "python_version": sys.version.split()[0],
        "checks": checks,
        "not_checked": [
            "postgres16",
            "postgres17",
            "joint_recovery",
            "browser",
            "real_channels",
            "production",
        ],
    }


def main():
    if "--json" in sys.argv[1:]:
        report = inspect_environment()
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0 if report["status"] == "passed" else 1
    try:  # Windows 控制台默认 GBK，中文说明按 UTF-8 输出以免出现乱码
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - 老控制台不支持 reconfigure 时保持原样
        pass
    problems, warnings = [], []
    print("== 解释器 ==")
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    print(f"可执行文件: {sys.executable}")
    print(f"虚拟环境: {'是' if in_venv else '否'}")
    print(f"Python: {sys.version.split()[0]}（CI 固定 3.11）")
    if not in_venv:
        warnings.append("当前不在虚拟环境内；建议使用仓库 .venv，避免污染全局解释器。")

    print("== 依赖对齐 ==")
    for relative in REQUIREMENT_FILES:
        for line in (ROOT / relative).read_text(encoding="utf-8").splitlines():
            parsed = parse_requirement(line)
            if not parsed:
                continue
            name, specifiers = parsed
            try:
                installed = metadata.version(name)
            except metadata.PackageNotFoundError:
                problems.append(f"{name} 未安装（要求 {format_specifiers(specifiers)}）")
                print(f"FAIL {name}: 未安装  要求 {format_specifiers(specifiers)}")
                continue
            if satisfies(installed, specifiers):
                print(f"PASS {name}: {installed}  要求 {format_specifiers(specifiers)}")
            else:
                problems.append(f"{name} 版本 {installed} 不满足 {format_specifiers(specifiers)}")
                print(f"FAIL {name}: {installed}  要求 {format_specifiers(specifiers)}")

    print("== 关键能力探针 ==")
    for distribution, module in MODULES.items():
        try:
            importlib.import_module(module)
        except Exception as error:  # noqa: BLE001 - 体检脚本需要给出可读结论
            problems.append(f"{distribution} 无法导入：{type(error).__name__}")
            print(f"FAIL import {module}: {type(error).__name__}")
    try:
        from starlette.testclient import TestClient

        supports_client = "client" in inspect.signature(TestClient.__init__).parameters
        print(
            ("PASS" if supports_client else "FAIL")
            + " TestClient(client=...) 可用（HTTP 夹具依赖；旧版本会导致 106 个夹具错误）"
        )
        if not supports_client:
            problems.append("starlette 过旧：TestClient 不支持 client 参数，HTTP 夹具将大面积报错")
    except Exception as error:  # noqa: BLE001
        problems.append(f"starlette 探针失败：{type(error).__name__}")
        print(f"FAIL starlette 探针: {type(error).__name__}")

    print("== 仓库状态 ==")
    migrations = {
        dialect: len(list((ROOT / "nexus_backend" / "migrations" / dialect).glob("*.sql")))
        for dialect in ("sqlite", "postgres")
    }
    print(f"迁移文件：sqlite {migrations['sqlite']} / postgres {migrations['postgres']}")
    lock = ROOT / "nexus_backend" / "requirements.lock"
    print(f"锁文件：{'存在' if lock.exists() else '缺失（在干净环境 pip freeze 生成）'}")

    for warning in warnings:
        print(f"WARN {warning}")
    if problems:
        print("\n存在阻塞项，建议重建环境：")
        print("  python -m venv .venv")
        print("  .venv\\Scripts\\python -m pip install -r nexus_backend/requirements.lock")
        print("  .venv\\Scripts\\python scripts/env_doctor.py")
        return 1
    print("\n本地依赖探针通过；数据库、身份、浏览器与恢复环境仍须分别验收。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
