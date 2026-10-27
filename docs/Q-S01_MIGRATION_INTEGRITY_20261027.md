# Q-S01 迁移不可变性与门禁 — 本地验收记录（2026-10-27）

- 候选目录：`tmp_test/qr01-integration`（git worktree，detached）
- 基线 HEAD：`32e3042d17a771ccd3209a561ea59d7f9169bc4c`
- 范围：只动迁移门禁与扫描器，不新增业务功能；未提交、未推送
- 环境：Windows + 本地 SQLite 临时库；PostgreSQL 16.15（端口 25531）、17.11（端口 25532）由 WSL 二进制直接 `initdb`（`/tmp/qr01/pg16|pg17`，本地 trust，`fsync`/`synchronous_commit`/`full_page_writes=on`）

## 1. 历史迁移字节恢复

Q-R01 曾给三个既有迁移插入首行 `-- compatibility: incremental`，这改写了已应用迁移的字节。本轮删除该行，恢复原文件：

| 文件 | 行数 | sha256（前 8） |
| --- | --- | --- |
| `nexus_backend/migrations/postgres/148_projection_company_guard.sql` | 47 | `25fdd7d0` |
| `nexus_backend/migrations/postgres/149_projection_actorless_lock.sql` | 44 | `b2c89589` |
| `nexus_backend/migrations/postgres/150_projection_scope_guard.sql` | 26 | `f77ea850` |

三者与 `nexus_backend/tests/migration_history_baseline.json` 逐条相符。`migration_structure_debt.json` 由 3 条增至 6 条，新登记的三条 `reason` 写明「在兼容头约定之前已被隔离库应用，字节不可改，登记为历史债」。

负向验证（真实隔离库）：对已应用 155 个版本的 `qr01_gate`（PG16、PG17 各一次）执行 `migrator.migrate()`，均抛
`ValueError: 迁移 148 校验不符，禁止修改已应用的迁移文件`。

## 2. 扫描器（`nexus_backend/tests/migration_gate_scan.py`）

- `TableFacts` 暴露 `columns/rls_enabled/rls_forced/policies/tautological_policies/gate_functions/tenant_isolation/company_isolation/created`。
- `mask_sql()` 掩码注释/单引号/美元引用；`mask_comments()` 只掩注释（保留字符串里的动态 DDL 模板）。
- `policy_verdict()` 三分类：`tenancy` / `tautology`（`true`、`1=1`、`company_id = company_id`）/ `other`，`book_allowed(` 等真实谓词例外。
- `_dynamic_installs()` 识别 `format('… accounting.%I …')` + 参数列表（`VALUES` / `ARRAY[...]`）。
- `structure_problems(facts, since=…)` 输出 `missing_rls` / `tautological_policy` / `missing_write_gate` 三类缺口。
- 修过两个实现缺陷：列名解析需 `(?:^|,)\s*IDENT`（原实现只认行首，漏判同行的 `company_id`）；`created` 改懒设，避免先建后 `ALTER` 被降版本。

## 3. 台账批准集硬化（`scripts/check_migration_idempotency.py`）

- `HISTORY_MAX_VERSION = 161`；`APPROVED_HISTORICAL` 固化 7 个文件逐个 sha256；`APPROVED_DIGEST = 557bf0e0…`（排序后 `file:sha256` 行的 sha256）。
- `evaluate()` 开头调用 `_approved_set_problems()`：未登记文件报 `not an approved historical file; the approved historical set cannot be extended from the ledger`；缺条目、摘要不符、版本 ≥ 161 均报错。
- 效果：台账不能再通过「新增 approved + entries」扩大豁免。
- `python scripts/check_migration_idempotency.py` → exit 0；`--strict-review` 输出 16 条动态 DDL 专项检查（`138/148/153/154/155/157/158/159/160/162/163/167` 等）。

## 4. 先红后绿的测试

新增 `nexus_backend/tests/test_qs01_migration_integrity.py`（9 例）：

1. 三个历史文件字节/行数不变且无兼容头；
2. `migrator.classify_migrations` 对已应用库判「已应用」而不是「校验不符」；
3. 新文件同时进 `approved` 与 `entries` 仍被拒；
4. `FORCE ROW LEVEL SECURITY` 不算 `ENABLE`；
5. `company_id = company_id` 不算隔离；
6. 注释里的动态安装不计；
7. 有 RLS 无写门须报 `missing_write_gate`；
8. 真实仓库 `structure_problems` 为空；
9. 真实动态安装仍被承认。

首轮 **6 failed / 3 passed**（列名漏判、动态目标未命中、台账无批准集），修后全绿。

`test_migration_structure.py` 的 `test_new_tables_carry_rls_force_and_write_gate` 改写为 `test_new_tables_carry_rls_and_write_gate`：直接断言 `structure_problems(load(ROOT), since=POSTGRES_DISCIPLINE)` 为空，并保留全局表白名单无租户列的断言。

四个文件合计 **53 passed**：

```
PYTHONPATH=nexus_backend .venv/Scripts/python.exe -m pytest \
  nexus_backend/tests/test_qs01_migration_integrity.py \
  nexus_backend/tests/test_migration_idempotency_ledger.py \
  nexus_backend/tests/test_migration_idempotency_scanner.py \
  nexus_backend/tests/test_migration_structure.py -q
```

## 5. PostgreSQL 16 / 17 实证

驱动脚本 `tmp_test/qs01_pg_replay.py`（临时，不入库）。对 `127.0.0.1:25531|25532` 的**新建空库** `qs01_gate`：

| 项目 | PG16 | PG17 |
| --- | --- | --- |
| `migrate` 应用版本数 | 155（1→167） | 155（1→167） |
| `status` 非已应用条数 | 0 | 0 |
| 重复 `migrate` | `[]`（无新版本） | `[]` |
| 异常 | 无 | 无 |

迁移摘要：PostgreSQL 155 个文件聚合 `6be57f56ba68ec11645e9a1fd44b1f9dfa9374d31adb9a944f8f3bc88f605c47`；SQLite 104 个文件 `b50a15144460410ec440dd39bfcaa12fcf745d6b0448044a5996f1730ae51aa8`。

## 6. 未验证与边界

- 远端 CI 未跑。
- 「启动不重复应用」只在 `migrate` 语义层验证（重复调用返回空集），未做进程重启实验。
- 动态 DDL（`DO $$ … EXECUTE format …`）只被标记为需专项检查，未逐条证明幂等。
- Windows 写文件会把 LF 转 CRLF；本轮改动文件已 `ruff format`，但未加 `.gitattributes` 强制行尾。

## 7. 本轮改动文件

已跟踪修改 6 个：三个 `148/149/150` 迁移、`nexus_backend/tests/migration_gate_scan.py`、`nexus_backend/tests/migration_structure_debt.json`、`nexus_backend/tests/test_migration_structure.py`、`scripts/check_migration_idempotency.py`。

未跟踪 3 个：`nexus_backend/tests/migration_history_baseline.json`、`nexus_backend/tests/test_qs01_migration_integrity.py`、本文件。
