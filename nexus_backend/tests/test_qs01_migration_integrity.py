"""Q-S01：历史迁移不可变性、台账豁免边界与结构扫描的证伪能力。

每个用例对应一条可复现缺陷：

* 投影迁移 148–150 被插入兼容注释后，规范化摘要与已应用库不一致；
* 台账把同一个新文件同时写进 ``approved`` 与 ``entries`` 即可自证豁免；
* 只有 ``FORCE`` 没有 ``ENABLE`` 被当作 RLS 覆盖；
* ``WITH CHECK (company_id = company_id)`` 被当作企业隔离；
* 注释里写着的动态安装被当作真实安装；
* 有 RLS 策略、没有公司写入围栏时，缺少写门没有被单独报出。

最后两个用例是反向保护：合法的直接安装与真实的动态安装必须继续被承认，
否则「修严」会变成「全盘误杀」。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import check_migration_idempotency as checker
from scripts import migrate_accounting as migrator
import migration_gate_scan as gate_scan

ROOT = Path(__file__).resolve().parents[2]
POSTGRES = ROOT / "nexus_backend" / "migrations" / "postgres"
BASELINE = Path(__file__).with_name("migration_history_baseline.json")
HISTORICAL = (
    "148_projection_company_guard.sql",
    "149_projection_actorless_lock.sql",
    "150_projection_scope_guard.sql",
)


def baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))["files"]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_historical_projection_migrations_keep_their_applied_bytes():
    """缺陷：为满足兼容头约定改动历史迁移，已应用库的 checksum 随之失效。"""

    for name, expected in baseline().items():
        path = POSTGRES / name
        text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        assert not text.startswith("-- compatibility:"), (
            f"{name} 在进入 git 前已被应用，不得为了兼容头改写首行"
        )
        assert sha(text) == expected["sha256"], f"{name} 与已应用内容不一致"
        assert len(text.splitlines()) == expected["lines"]


def test_database_applied_before_the_change_upgrades_without_checksum_drift():
    """缺陷：同一份历史迁移在升级时被判「校验不符」，即不可变性被破坏。"""

    applied = {
        int(name.split("_", 1)[0]): expected["sha256"] for name, expected in baseline().items()
    }
    files = [
        (int(name.split("_", 1)[0]), checker._file_sha256(POSTGRES / name)) for name in baseline()
    ]
    assert migrator.classify_migrations(applied, files) == [
        (version, "已应用") for version, _ in files
    ]


def _ledger_layout(tmp_path: Path, names: list[str]) -> tuple[Path, Path]:
    migrations = tmp_path / "postgres"
    migrations.mkdir()
    approved, entries = [], []
    for name in names:
        text = "CREATE FUNCTION accounting.f() RETURNS INT AS $$ SELECT 1 $$;\n"
        (migrations / name).write_text(text, encoding="utf-8")
        digest = sha(text)
        approved.append(
            {"file": name, "sha256": digest, "basis": "tests", "approved_on": "2026-10-25"}
        )
        entries.append(
            {
                "file": name,
                "sha256": digest,
                "reason": "tests",
                "violations": [{"rule": checker.RULE_FUNCTION, "object": "accounting.f"}],
            }
        )
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps({"version": 2, "approved": approved, "entries": entries}),
        encoding="utf-8",
    )
    return migrations, ledger


def test_ledger_rejects_a_new_migration_listed_in_both_approved_and_entries(tmp_path):
    """缺陷：新增迁移同时写进两个清单，就能扩大历史豁免。"""

    migrations, ledger = _ledger_layout(tmp_path, ["153_old.sql", "168_new.sql"])
    problems = checker.check(0, migrations=migrations, ledger_path=ledger, require_approvals=True)
    assert problems, "168_new.sql 不属于历史批准集，必须拒绝"
    assert any("168_new.sql" in problem for problem in problems), problems


def test_rls_force_without_enable_is_not_coverage():
    """缺陷：只有 FORCE ROW LEVEL SECURITY 的表被当作已启用 RLS。"""

    source = (
        "CREATE TABLE accounting.synthetic (id TEXT PRIMARY KEY, company_id TEXT NOT NULL);\n"
        "ALTER TABLE accounting.synthetic FORCE ROW LEVEL SECURITY;\n"
    )
    facts = gate_scan.scan({1: ("001_synthetic.sql", source)})
    assert facts["synthetic"].rls is False, "FORCE 不等于 ENABLE，不能算 RLS 覆盖"


def test_tautological_tenant_check_does_not_prove_isolation():
    """缺陷：``company_id = company_id`` 这类恒真条件被当作租户隔离。"""

    source = (
        "CREATE TABLE accounting.synthetic (id TEXT PRIMARY KEY, company_id TEXT NOT NULL);\n"
        "ALTER TABLE accounting.synthetic ENABLE ROW LEVEL SECURITY;\n"
        "CREATE POLICY synthetic_all ON accounting.synthetic FOR ALL\n"
        "  USING (true) WITH CHECK (company_id = company_id);\n"
        "CREATE TRIGGER synthetic_gate BEFORE INSERT ON accounting.synthetic\n"
        "  FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();\n"
    )
    facts = gate_scan.scan({1: ("001_synthetic.sql", source)})
    entry = facts["synthetic"]
    assert entry.rls is True
    assert entry.tenant_write_policy is False, "恒真条件不能证明企业隔离"
    assert entry.tenant_isolation is False


def test_commented_out_dynamic_install_earns_no_credit():
    """缺陷：注释里的 ``format('CREATE TRIGGER ...')`` 被当作真实动态安装。"""

    source = (
        "CREATE TABLE accounting.synthetic (id TEXT PRIMARY KEY, company_id TEXT NOT NULL);\n"
        "ALTER TABLE accounting.synthetic ENABLE ROW LEVEL SECURITY;\n"
        "-- EXECUTE format('CREATE TRIGGER %I BEFORE INSERT ON accounting.%I"
        " FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard()'"
        ", 'synthetic_gate', 'synthetic');\n"
        "CREATE POLICY synthetic_all ON accounting.synthetic FOR ALL\n"
        "  USING (company_id = accounting.actor_firm())\n"
        "  WITH CHECK (company_id = accounting.actor_firm());\n"
    )
    facts = gate_scan.scan({1: ("001_synthetic.sql", source)})
    assert "<dynamic>" not in facts["synthetic"].gate_functions


def test_tenant_table_with_rls_but_no_write_gate_is_reported_separately():
    """缺陷：有 RLS 策略就被当作已有写入围栏，两类保护被混为一谈。"""

    source = (
        "CREATE TABLE accounting.synthetic (id TEXT PRIMARY KEY, company_id TEXT NOT NULL);\n"
        "ALTER TABLE accounting.synthetic ENABLE ROW LEVEL SECURITY;\n"
        "CREATE POLICY synthetic_all ON accounting.synthetic FOR ALL\n"
        "  USING (company_id = accounting.actor_firm())\n"
        "  WITH CHECK (company_id = accounting.actor_firm());\n"
    )
    facts = gate_scan.scan({1: ("001_synthetic.sql", source)})
    problems = gate_scan.structure_problems(facts)
    kinds = {problem["kind"] for problem in problems if problem["table"] == "synthetic"}
    assert "missing_write_gate" in kinds, problems
    assert "missing_rls" not in kinds, "RLS 本身是有的，不能报成缺失"


def test_repository_structure_has_no_unregistered_gaps():
    """真实仓库：任何带 company_id 的新表都必须同时具备 ENABLE 与写入围栏。"""

    problems = gate_scan.structure_problems(gate_scan.load(ROOT), since=88)
    assert not problems, [
        f"{problem['table']}: {problem['kind']} ({problem['detail']})" for problem in problems
    ]


def test_direct_and_real_dynamic_installs_are_still_credited():
    """反向保护：合法的直接安装与真实动态安装必须继续被承认。"""

    direct = (
        "CREATE TABLE accounting.one (id TEXT PRIMARY KEY, company_id TEXT NOT NULL);\n"
        "ALTER TABLE accounting.one ENABLE ROW LEVEL SECURITY;\n"
        "CREATE POLICY one_all ON accounting.one FOR ALL\n"
        "  USING (company_id = accounting.actor_firm())"
        " WITH CHECK (company_id = accounting.actor_firm());\n"
        "CREATE TRIGGER one_gate BEFORE INSERT ON accounting.one\n"
        "  FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();\n"
    )
    dynamic = (
        "CREATE TABLE accounting.two (id TEXT PRIMARY KEY, company_id TEXT NOT NULL);\n"
        "DO $$ BEGIN EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY'"
        ", 'two'); END $$;\n"
        "CREATE POLICY two_all ON accounting.two FOR ALL\n"
        "  USING (company_id = accounting.actor_firm())"
        " WITH CHECK (company_id = accounting.actor_firm());\n"
        "DO $$ BEGIN EXECUTE format('CREATE TRIGGER %I BEFORE INSERT ON accounting.%I"
        " FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard()'"
        ", 'two_gate', 'two'); END $$;\n"
    )
    facts = gate_scan.scan({1: ("001_install.sql", direct), 2: ("002_install.sql", dynamic)})
    assert "company_write_table_guard" in facts["one"].gate_functions
    assert facts["one"].tenant_isolation is True
    assert "<dynamic>" in facts["two"].gate_functions
    assert facts["two"].rls is True
    assert not [p for p in gate_scan.structure_problems(facts) if p["table"] in {"one", "two"}]
