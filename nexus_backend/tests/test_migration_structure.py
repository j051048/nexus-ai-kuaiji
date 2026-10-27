"""迁移纪律：兼容头、RLS/写门覆盖与版本唯一性（面向 088/070 之后的新迁移）。

三类事实分开断言，避免用一个正则冒充「全部幂等」：

* 静态覆盖：新表必须同时具备 RLS ENABLE/FORCE 与一条租户写门（或带租户判定的
  写策略）。历史不可改的文件登记在 ``migration_structure_debt.json``，逐条带
  sha256 与理由，且登记项一旦被修复就会让测试失败（防止登记表变成垃圾桶）。
* 识别等价：动态安装（``EXECUTE format`` + ``VALUES`` 列表）与直接语句一视同仁，
  用真实已知表做等价性用例，防止扫描器「看不见就放行」。
* 执行与重复应用属于 ``migrate_accounting.py`` 与 PG 门禁的职责，不在本文件声称。
"""

import hashlib
import json
import os
import re
from pathlib import Path

import migration_gate_scan as gate_scan

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "nexus_backend" / "migrations"
DEBT = Path(__file__).with_name("migration_structure_debt.json")
HEADER = re.compile(r"^\s*--\s*compatibility\s*:\s*(incremental|breaking)\s*$", re.M)
POSTGRES_DISCIPLINE = 88
SQLITE_DISCIPLINE = 70

GLOBAL_TABLES = {
    "ai_endpoint_capabilities": "全局供应商能力表，无租户列，仅超管代码路径读写",
    "ai_trusted_endpoints": "全局可信端点白名单，无租户列，由部署脚本维护",
    "recovery_release_audit": "全局恢复审计流水，仅 worker 追加，按 GRANT 限制读写",
    "report_templates": "全局报表模板目录，事务所只读引用，不存租户数据",
    "restore_state": "全局恢复开关状态，单行表，仅 worker 维护",
}

RECOGNIZED_GATES = {
    "company_write_table_guard",
    "close_dependency_write_guard",
    "monthly_delivery_guard",
    "<dynamic>",
}


def migration_files(dialect):
    files = {}
    for path in sorted((MIGRATIONS / dialect).glob("[0-9][0-9][0-9]_*.sql")):
        files[int(os.path.basename(path).split("_", 1)[0])] = path.read_text(encoding="utf-8-sig")
    return files


def normalized_digest(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    return hashlib.sha256(text.encode()).hexdigest()


def load_debt() -> list[dict]:
    return json.loads(DEBT.read_text(encoding="utf-8"))["entries"]


def test_versions_are_unique_per_dialect():
    for dialect in ("sqlite", "postgres"):
        versions = [
            int(os.path.basename(path).split("_", 1)[0])
            for path in (MIGRATIONS / dialect).glob("*.sql")
        ]
        assert len(versions) == len(set(versions)), dialect


def test_new_migrations_declare_compatibility_header():
    debt = {(entry["dialect"], entry["file"]): entry for entry in load_debt()}
    for dialect, threshold in (("sqlite", SQLITE_DISCIPLINE), ("postgres", POSTGRES_DISCIPLINE)):
        for version, source in migration_files(dialect).items():
            if version < threshold:
                continue
            path = next((MIGRATIONS / dialect).glob(f"{version:03d}_*.sql"))
            head = "\n".join(source.splitlines()[:5])
            if HEADER.search(head):
                assert (dialect, path.name) not in debt, (
                    f"{path.name} 已补兼容头，请从 debt 台账移除该条"
                )
                continue
            entry = debt.get((dialect, path.name))
            assert entry is not None, f"{dialect} {path.name} 缺少 compatibility 头"
            assert entry["sha256"] == normalized_digest(path), (
                f"{path.name} 内容已变，历史债登记失效；请修复而不是更新登记"
            )


def test_debt_entries_still_exist_and_still_fail():
    for entry in load_debt():
        path = MIGRATIONS / entry["dialect"] / entry["file"]
        assert path.exists(), f"登记的历史文件不存在：{path}"
        assert (
            HEADER.search("\n".join(path.read_text(encoding="utf-8-sig").splitlines()[:5])) is None
        ), f"{entry['file']} 已有兼容头，登记应移除"
        assert entry["reason"].strip(), "历史债必须写明原因"


def test_new_tables_carry_rls_and_write_gate():
    """结构扫描的唯一权威断言：租户表必须同时具备 RLS 与公司写入围栏。

    三类问题由 ``structure_problems`` 分别给出 ``missing_rls``、
    ``tautological_policy``、``missing_write_gate``；这里只断言真实仓库为空，
    负向能力由 ``test_qs01_migration_integrity.py`` 的构造用例保证。
    """

    facts = gate_scan.load(ROOT)
    problems = gate_scan.structure_problems(facts, since=POSTGRES_DISCIPLINE)
    assert not problems, [
        f"{problem['table']}: {problem['kind']} ({problem['detail']})" for problem in problems
    ]
    assert set(GLOBAL_TABLES) <= set(facts), "全局表清单里有不存在的表"
    for table in GLOBAL_TABLES:
        entry = facts[table]
        assert "company_id" not in entry.columns and "firm_id" not in entry.columns, (
            f"{table} 登记为全局表却带租户列"
        )


def test_scanner_recognizes_dynamic_and_direct_gates():
    facts = gate_scan.load(ROOT)
    # 098 的表由 115 用 EXECUTE format + VALUES 动态补门，静态必须与线上一致。
    assert "<dynamic>" in facts["batch_operations"].gate_functions
    # 105 的表用直接 CREATE TRIGGER 装 P0 依赖写门。
    assert "close_dependency_write_guard" in facts["vouchers"].gate_functions
    # 全局模板表必须不被误判为公司表，否则豁免会掩盖真实缺口。
    assert "company_id" not in facts["report_templates"].columns
    assert not facts["report_templates"].rls


def test_import_wizard_large_file_migration_covers_every_row_bound():
    postgres = migration_files("postgres")[96]
    sqlite = migration_files("sqlite")[73]
    assert postgres.count("jsonb_array_length") >= 3
    assert postgres.count("BETWEEN 1 AND 5000") == 2
    assert "size_bytes BETWEEN 1 AND 10485760" in sqlite
    assert "row_number BETWEEN 2 AND 10000000" in sqlite


def test_company_write_scope_wave4_maps_each_single_company_table():
    postgres = migration_files("postgres")[97]
    for table in (
        "bank_duplicate_decisions",
        "migration_clearance_commands",
        "migration_clearance_allocations",
        "migration_clearance_reversals",
    ):
        assert f"WHEN '{table}'" in postgres
