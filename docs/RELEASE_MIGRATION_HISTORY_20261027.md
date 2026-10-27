# 148–150 历史迁移恢复：本地验收记录

日期：2026-10-27。工作目录为仓库主目录，起点提交为
`32e3042d17a771ccd3209a561ea59d7f9169bc4c`。

## 范围与修复

PG148–150 曾在已提交的迁移开头追加 `-- compatibility: incremental`。
虽然仅新增注释，运行时按完整 SQL 文本计算的校验和仍发生变化，不能以更新已应用数据库的
`schema_migrations.checksum` 来消除差异。

本轮仅删除这三个新增首行，恢复提交
`3fc1ab6e11761ba36490f1858b85fcff356441cc` 中的原内容；缺少兼容头作为历史声明债，
按文件名和原摘要追加到 `nexus_backend/tests/migration_structure_debt.json`。
不新增迁移，不改历史数据库记录，不改 PG153–167，也不修改业务算法或授权规则。

新增 `nexus_backend/tests/test_release_migration_history.py` 固定以下三个常量。
测试直接计算文件摘要，并核对运行时 `migration_digest()`；运行时不调用 Git，也不从当前债台账
生成期望值。摘要使用 UTF-8 文本、CRLF 转 LF，保留其他所有内容。

| 文件 | 固定原 SHA-256 |
| --- | --- |
| `148_projection_company_guard.sql` | `25fdd7d0130767833aba38a2984e32ce915a93f5643b3208299bae30758b4dbe` |
| `149_projection_actorless_lock.sql` | `b2c895897a8bd715052034754f5c8836f985919a1ebf99e35258e086bec5441a` |
| `150_projection_scope_guard.sql` | `f77ea8509a8246a624ae83ebc068a6b6977a64e35f1d225bd73a231b5906f8c0` |

## 先红后绿

所有 pytest 均使用主仓库 `.venv/Scripts/python.exe` 和 `scripts/test_accounting_offline.py`。
该入口清除继承的数据库、模型和平台配置，使用独立临时 SQLite/原件目录并阻止外部网络连接。
测试数据均为合成数据，没有执行真实模型或线上业务。

恢复前执行：

```powershell
./.venv/Scripts/python.exe scripts/test_accounting_offline.py nexus_backend/tests/test_release_migration_history.py -q -p no:cacheprovider --junitxml=tmp_test/release-migration-history-red-20261027.xml
```

结果：退出码 **1**，**3 failed、0 errors、0 skipped**。三个失败均为新增兼容头导致的
真实摘要差异。红测完成并通知负责恢复的代理之后，才删除三个首行并追加声明债。

恢复后执行：

```powershell
./.venv/Scripts/python.exe scripts/test_accounting_offline.py nexus_backend/tests/test_release_migration_history.py nexus_backend/tests/test_migration_structure.py nexus_backend/tests/test_migration_idempotency_ledger.py nexus_backend/tests/test_migration_idempotency_scanner.py nexus_backend/tests/test_migration_compatibility.py nexus_backend/tests/test_accounting.py nexus_backend/tests/test_posting_service.py nexus_backend/tests/test_review_policy_service.py -q -p no:cacheprovider --junitxml=tmp_test/release-migration-history-green-20261027.xml
```

结果：退出码 **0**，**110 passed、0 failed、0 errors、0 skipped**，耗时 **191.50 秒**。
唯一警告为 Starlette TestClient 使用 httpx 的弃用提示。

| 回归文件 | 通过数 |
| --- | ---: |
| `test_release_migration_history.py` | 3 |
| `test_migration_structure.py` | 7 |
| `test_migration_idempotency_ledger.py` | 15 |
| `test_migration_idempotency_scanner.py` | 13 |
| `test_migration_compatibility.py` | 6 |
| `test_accounting.py` | 45 |
| `test_posting_service.py` | 5 |
| `test_review_policy_service.py` | 16 |

另执行：

```powershell
./.venv/Scripts/python.exe scripts/check_migration_idempotency.py
./.venv/Scripts/python.exe -m ruff check nexus_backend/tests/test_release_migration_history.py
./.venv/Scripts/python.exe -m ruff format --check nexus_backend/tests/test_release_migration_history.py
```

以上均退出 **0**。迁移检查器仍输出动态 DDL 需专项检查的提示；该退出码仅是当前静态规则结果。

## 证据绑定

以下 XML 保存在忽略目录，提交时只提交测试与报告，不提交本地运行数据库或原件。

| 证据文件 | 原始字节 SHA-256 |
| --- | --- |
| `tmp_test/release-migration-history-red-20261027.xml` | `c699894696e768c07299244c03aa725ca20381534c29f8ce9b0aed742bae6570` |
| `tmp_test/release-migration-history-green-20261027.xml` | `d80386c09b3e9166ed250de7c774dbe892e7e3ec6c9e4139b2e0b90de39c494a` |

验收时测试文件规范化 SHA-256 为
`0b6679f4538dfcac74c06fa1b43ab72ceb10544b9a791068f15fa260ae609406`；
声明债台账规范化 SHA-256 为
`18f6ddd73b0dbef73da393d42cfb4fb1518ef9e87bfd0722bd7641237b683d2a`。
三个迁移的验收摘要与上表固定原值一致。

## 结论边界

本记录证明这三份迁移恢复原内容，以及列出的本地静态和 SQLite 业务回归通过。
本轮未运行 PostgreSQL 16/17 动态测试、既有线上数据库升级、浏览器或真实云端联调；
也未执行提交、推送、部署、重启、权限变更或模型请求。
历史例外批准边界、结构扫描器反例等独立问题不由此次三个摘要测试解决，不能据此宣称全部门禁或生产发布已通过。
