# N02-R1 税务底稿本地收口（2026-10-28）

## 结论

本轮要求的数据库复核资格、来源版本绑定、旧稿导出阻断和不支持场景阻断已完成本地验收。基线是 `bc354080e9b0db41e3d86b4f92ed9c8c11d4a7a5` 加此前未提交的 N02/N03 修改；本轮在已有 N02 实现上修复，没有重置工作区。

- 本地组合回归：**215 passed，0 failed，0 skipped**，336.86 秒；1 条既有 Starlette TestClient 弃用警告。
- PostgreSQL 16.15 / 17.11：**每版 45 passed，0 skipped**，包含 32 项原生受限角色守卫、13 项服务及双会话验证。
- 两版本最终空库全量迁移至 173、再次 migrate、status 全部已应用：通过。
- 本轮 11 个 Python 文件 Ruff 与格式检查、git diff --check：通过。

未连接真实模型、真实税务服务或线上环境；未 commit、push、部署。此结论不是税务申报认证，也不替代 C01 完整恢复和其他批次的发布门槛。

## 已修复的行为

### 1. 权限和数据库约束

税务档案创建需要企业管理资格；底稿复核使用实际的账套复核资格，编辑权不再等同复核权。数据库约束同时检查真实 actor、制单/复核身份、企业/账套/期间、生效档案，以及独立复核。

新增 PG 171–173、SQLite 108–109。历史 169/170 和 106/107 的字节不变。171/172 应用到隔离库后发现的补充问题使用下一条前向迁移，未修改已登记 checksum。

SQL 守卫保证授权、归属、不可改写的快照和存储数值一致；服务层仍必须重新读取实际来源计算，不把任意手写快照当作真实财税依据。SQLite 无 PG 角色/RLS，不宣称其结构触发器提供相同的数据库登录权限隔离。

### 2. 来源是版本化事实，而不只是汇总金额

底稿新增冻结的 `source_snapshot_json`，版本标识 `n02-tax-v2`，包含：

- 事务所、企业、长期账套、月度账套、期间；
- 生效税务档案；
- 明确声明的稳定科目 ID、版本、用途及定义；
- 原件 SHA、批次 ID、行号、完整原始发票行与行指纹；
- 本账期全部正式凭证头和分录，包括未计入 VAT 的分录；
- 计算结果。

同金额换票号、原件 SHA 变化、增加抵消凭证、科目版本变化，都不能靠“汇总数没变”继续使用旧复核。未审核、已审核或已导出的普通草稿均不算正式账。

不再通过 2221 编码猜税种。生成请求可声明 `vat_accounts`，每项为 `account_id/account_version/role`，role 为 `vat_payable` 或 `excluded`。必须覆盖当期已过账分录使用的所有科目版本；未发生的明确声明也保留在快照。缺项、错版本或他企科目受控拒绝。用途声明须经独立会计复核，不自动提升为全企业通用规则。

### 3. 复核和导出保持同一事实版本

- 复核/导出在企业写锁内重新授权并重算来源；同总额来源变化也拒绝。
- 当前导出只接受最新底稿的精确版本、摘要及已复核状态；旧稿仍可只读查看，不自动升级，也不伪装成当前可交付版本。
- 旧空快照不补写成新口径，必须重新生成并独立复核。
- 存储金额、差额、发票明细与真实重算逐项比较；不能插入“正确来源快照＋错误税额/明细”再复核。
- 导出要求 `export` 权限，read/edit 权限不足；审计绑定复核人、版本、来源摘要及实际 CSV 字节 SHA。
- CSV 应纳税额固定落在税额列，元数据与发票明细分区，文本字段防止表格公式执行。

CSV API 的新必填参数：

```text
GET /api/tax/workpapers/{id}/csv
    ?book_id={book_id}
    &expected_version={reviewed_version}
    &source_digest={reviewed_source_digest}
```

响应保留 `X-Content-Sha256`，新增版本/来源摘要头及 `Cache-Control: no-store`。仓库没有已接入此接口的税务前端，因此本轮没有改动 src。

### 4. 支持范围与金额边界明确

当前税务底稿只计算**小规模、按月、受支持的正常发票**，税率来自人工登记的档案；始终 `filing=not_filed`。

季度、一般纳税人的进项采用/抵扣与期初留抵、跨期发票、红字/作废/重复/非法行等，不再静默忽略或给出不完整结果，返回受控阻断和人工处理提示。档案仍可以登记 general/quarterly，但这不代表计算路径已经支持。

金额使用共享 `MAX_CENTS=99_999_999_999_999`；原始单项和汇总在写入前检查。PG 金额列改为 BIGINT，避免 SQLite 能保存而 PG int32 溢出。非法期间返回 422，损坏来源 JSON 返回 409，不提交坏稿。真实导入的 buyer/seller 字段按购销方向展示，不丢失对方名称。

## 先失败再修复的证据

| 组 | 修复前有效失败 | 修复后 |
|---|---|---|
| 来源/导出 HTTP | 14 失败：缺绑定、错绑定、旧源、正式事实变化、支持范围等 | 包含在最终组合回归 |
| 原件 SHA/票号等额变化 | 2 失败 | 包含在最终组合回归 |
| 科目与正式快照接口 | 10 失败为新契约尚未实现；另有未映射业务路径 | 补强为 12 项通过，真实手工复核/过账 |
| 输出字段与真实来源不一致 | 4 项未抛错误 | 逐字段/明细重检通过 |
| CSV 税额列 | 1 项五列而非七列 | 列位置断言通过 |
| 期间及原始 JSON 形状 | 8 项 HTTP500 | 422/409 且零新增底稿 |
| 金额 MAX 边界 | 单项/合计超 MAX 两项 HTTP500 | 409，合法 MAX 和超过 int32 的合法金额通过 |
| PG 原生权限/范围 | PG16/17 各 5 失败 | 两版最终原生守卫各 32 通过 |
| PG 快照/金额/BIGINT | 10 + 7 + 1 项分阶段失败 | 两版完整服务/并发均通过 |

最早输入测试错误地把 int32 当作产品上限，其中两项“失败”已撤回，不计缺陷证据。最终依据共享 MAX_CENTS 重写测试并真实复现，未通过降低上限规避两库差异。

## 最终回归范围及命令

本地通过的 215 项包括税务原测试、N02 新合同测试、金额/原生 SQLite 守卫、迁移结构与幂等检查，以及 C01 的辅助实体、银行证据和锁期依赖邻近回归。

```text
.venv/Scripts/python.exe scripts/test_accounting_offline.py
  nexus_backend/tests/test_tax_workpaper.py
  nexus_backend/tests/test_tax_workpaper_guards.py
  nexus_backend/tests/test_tax_workpaper_scope_sqlite.py
  nexus_backend/tests/test_tax_api.py
  nexus_backend/tests/test_tax_estimate.py
  nexus_backend/tests/test_n02_source_contract.py
  nexus_backend/tests/test_n02_voucher_snapshot.py
  nexus_backend/tests/test_n02_snapshot_consistency.py
  nexus_backend/tests/test_n02_input_boundaries.py
  nexus_backend/tests/test_migration_structure.py
  nexus_backend/tests/test_qs01_migration_integrity.py
  nexus_backend/tests/test_migration_idempotency_scanner.py
  nexus_backend/tests/test_migration_idempotency_ledger.py
  nexus_backend/tests/test_p0_close_dependencies.py
  nexus_backend/tests/test_c01_auxiliary_entities.py
  nexus_backend/tests/test_c01_bank_evidence.py -q
```

PG 详细角色、会话交错、日志和原始文件 SHA 见 `docs/N02_POSTGRES_VERIFICATION_20261028.md`。两版最终45项的关键源码 SHA 与本地最终文件一致。

本机证据（不提交敏感运行产物）：

- `tmp_test/n02-fix-20261028/local-final.xml`：215 项。
- `tmp_test/n02-pg-hardening/final-pg16.xml`、`final-pg17.xml`：每版45项。
- `tmp_test/n02-fix-20261028/fresh-migrations.json`：两版新空库至173及重复迁移。
- `tmp_test/n02-fix-20261028/ownership-verification.json`：既有文件修改归属。
- `tmp_test/n02-fix-20261028/pg-cleanup.json`：本轮两个验证库已清理；两空库迁移验证库也已清理。

## 协作及剩余边界

已有 N03 的 `delivery/followups.py`、相关测试及报告原样保留，C01 和历史迁移未改，Git 索引为空、HEAD 不变。只清理本轮创建的四个测试数据库，没有停止共享 PG 集群或改动原库/角色。

本轮没有重跑全项目浏览器、真实 OCR/LLM、真实税务政策验签、完整生产恢复；没有推进 N03–N06。一般纳税人/季度能力以及更广泛的税务业务，需要后续独立业务口径与测试，不把本轮保守阻断描述成已实现全部税务。
