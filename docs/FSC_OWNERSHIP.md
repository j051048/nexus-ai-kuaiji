# FSC 修改归属基线（2026-11-16）

## 仓库状态

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- 工作区：FSC01 相关 10 项未提交（见下），其余 A–E、P0/P1 改动已被其他智能体提交到 HEAD。
- 本任务不提交、不推送、不 reset/clean/stash、不覆盖其他智能体修改。

## FSC01 本次新增/修改

| 路径 | 归属 | 说明 |
| --- | --- | --- |
| `nexus_backend/migrations/sqlite/162_fsc_company_profiles.sql` | FSC01 | 公司行业档案表 |
| `nexus_backend/migrations/postgres/246_fsc_company_profiles.sql` | FSC01 | 同上，PG RLS |
| `nexus_backend/app/fsc/__init__.py` | FSC01 | 模块导出 |
| `nexus_backend/app/fsc/profiles.py` | FSC01 | 12 栏档案服务 |
| `nexus_backend/app/fsc/router.py` | FSC01 | 档案 API |
| `nexus_backend/app/main.py` | 共享 | 只加 FSC 服务/路由注册 |
| `nexus_backend/tests/test_fsc01_profiles.py` | FSC01 | 五公司档案测试 |
| `src/FscProfilesPanel.tsx` | FSC01 | 档案矩阵前端 |
| `src/FirmObservability.tsx` | 共享 | 只挂载档案矩阵 |
| `src/firm-observability.css` | 共享 | 只加档案矩阵样式 |
| `e2e/tasks0712.spec.ts` | 共享 | 只加档案矩阵断言 |

## 已有能力复用

- 身份/权限：`nexus_backend/app/accounting/access.py`
- 企业/账套/期间：`companies`、`books`、`periods`
- 价值基线：`nexus_backend/app/observability/value.py`
- 关账雷达：`nexus_backend/app/observability/closing.py`
- 月度预检：`nexus_backend/app/monthly/batch.py::preview`（只作参考，不作为关账权威预检）
- 税务试点边界：`tax_pilot_profiles`（仅税务，不能替代五行业档案）

## 并行修改保护

- HEAD 提交由其他智能体完成，包含 FSC 计划、迁移 244–246、租赁红冲、费率卡、OA 材料请求等；本任务只继续未提交的 FSC01 档案部分。
- 后续若发现同文件继续变化，先停下核对差异，不覆盖。


## FSC13（2026-11-17）

| 路径 | 归属 | 说明 |
| --- | --- | --- |
| `nexus_backend/migrations/sqlite/194_fsc13_closing_authority.sql` | FSC13 | 外部结转证据表 |
| `nexus_backend/migrations/postgres/279_fsc13_closing_authority.sql` | FSC13 | 同上，RLS/FORCE RLS |
| `nexus_backend/app/periodic/authority.py` | FSC13 | 权威模式/证据核对/汇总 |
| `nexus_backend/app/periodic/models.py` | FSC13 | `ClosingEvidenceWrite` |
| `nexus_backend/app/periodic/service.py` | FSC13 | 结转 fingerprint 纳权威、外部模式禁内部结转 |
| `nexus_backend/app/periodic/router.py` | FSC13 | authority/summary/evidence API |
| `nexus_backend/app/main.py` | 共享 | 只注册 closing authority 服务 |
| `nexus_backend/tests/test_fsc13_closing_authority.py` | FSC13 | 权威/幂等/冲突/旧草稿失效 |
| `nexus_backend/tests/test_fsc13_closing_authority_postgres.py` | FSC13 | PG16/17 证据/RLS |
| `nexus_backend/tests/test_tax04_recovery.py` | 共享 | 只加 FSC13 恢复用例 |
| `src/ClosingAuthorityPanel.tsx` | FSC13 | 结转权威/证据面板 |
| `src/TaxWorkpaperPanel.tsx` | 共享 | 只挂载面板 |
| `src/tax-workpapers.css` | 共享 | 只加面板/移动样式 |
| `e2e/fsc13-closing.spec.ts` | FSC13 | 1280/390 浏览器验收 |


## FSC14（2026-11-17）

| 路径 | 归属 | 说明 |
| --- | --- | --- |
| `nexus_backend/migrations/sqlite/195_fsc14_lease_draft_bindings.sql` | FSC14 | 合同版本绑定 |
| `nexus_backend/migrations/postgres/280_fsc14_lease_draft_bindings.sql` | FSC14 | 同上，RLS/FORCE RLS |
| `nexus_backend/migrations/postgres/281_fsc14_lease_plan_delete_grant.sql` | FSC14 | lease_plan_rows DELETE 前向授权 |
| `nexus_backend/migrations/sqlite/196_fsc14_lease_plan_delete_grant.sql` | FSC14 | SQLite 空操作 |
| `nexus_backend/app/tax/lease_guards.py` | FSC14 | 审核/记账/导出统一版本守卫 |
| `nexus_backend/app/tax/leases.py` | FSC14 | 写绑定、PG datetime 指纹修复 |
| `nexus_backend/app/accounting/service.py` | 共享 | 审核/导出调用守卫 |
| `nexus_backend/app/posting/service.py` | 共享 | 正式记账调用守卫 |
| `nexus_backend/tests/test_tax13_leases.py` | FSC14 | 旧草稿审核/记账拒绝 |
| `nexus_backend/tests/test_tax_f_postgres.py` | FSC14 | PG 版本围栏 |
| `src/TaxFPanel.tsx` | FSC14 | 重复动作/乐观创建/导出链 |
| `e2e/tax-f.spec.ts` | FSC14 | 1280/390 浏览器链 |


## FSC15（2026-11-17）

| 路径 | 归属 | 说明 |
| --- | --- | --- |
| `nexus_backend/migrations/sqlite/197_fsc15_repair_rate_lifecycle.sql` | FSC15 | 费率生命周期列/历史回填 |
| `nexus_backend/migrations/postgres/282_fsc15_repair_rate_lifecycle.sql` | FSC15 | 同上，PG |
| `nexus_backend/app/tax/costs.py` | FSC15 | 草稿/确认/生效/续期/停用/历史/影响/守卫 |
| `nexus_backend/app/tax/router.py` | 共享 | 只加费率生命周期 API |
| `src/TaxCostBatchPanel.tsx` | FSC15 | 费率生命周期 UI 与竞态修复 |
| `nexus_backend/tests/test_fsc15_rate_lifecycle.py` | FSC15 | SQLite 生命周期/守卫 |
| `nexus_backend/tests/test_fsc15_rate_lifecycle_postgres.py` | FSC15 | PG16/17 生命周期/跨年 |
| `nexus_backend/tests/test_tax04_recovery.py` | 共享 | 只加 FSC15 恢复用例 |
| `e2e/fsc15-rates.spec.ts` | FSC15 | 1280/390 浏览器验收 |


## FSC16（2026-11-17）

| 路径 | 归属 | 说明 |
| --- | --- | --- |
| `nexus_backend/migrations/sqlite/198_fsc16_repair_package_contracts.sql` | FSC16 | 包干合同/额度台账 |
| `nexus_backend/migrations/postgres/283_fsc16_repair_package_contracts.sql` | FSC16 | 同上，RLS/FORCE RLS |
| `nexus_backend/app/tax/packages.py` | FSC16 | 合同生命周期/占用/释放/额度 |
| `nexus_backend/app/tax/router.py` | 共享 | 只加包干 API |
| `src/TaxCostBatchPanel.tsx` | 共享 | 只加包干区块/占用释放操作 |
| `nexus_backend/tests/test_fsc16_packages.py` | FSC16 | SQLite 生命周期/额度/越界 |
| `nexus_backend/tests/test_fsc16_packages_postgres.py` | FSC16 | PG16/17 生命周期/并发/跨所 |
| `nexus_backend/tests/test_tax04_recovery.py` | 共享 | 只加 FSC16 恢复用例 |
| `e2e/fsc16-packages.spec.ts` | FSC16 | 1280/390 浏览器链 |


## FSC17（2026-11-17）

| 路径 | 归属 | 说明 |
| --- | --- | --- |
| `nexus_backend/migrations/sqlite/199_fsc17_asset_vehicle_links.sql` | FSC17 | 资产—车辆关联/撤销历史 |
| `nexus_backend/migrations/postgres/284_fsc17_asset_vehicle_links.sql` | FSC17 | 同上，PG RLS/FORCE |
| `nexus_backend/app/periodic/links.py` | FSC17 | 关联/撤销/摘要/按车辆查询 |
| `nexus_backend/app/periodic/router.py` | 共享 | 只加车辆关联 API |
| `nexus_backend/app/periodic/service.py` | 共享 | 折旧 aux/source_ref、输入指纹 |
| `src/AssetVehicleLinkEditor.tsx` | FSC17 | 资产卡片关联/撤销/stale |
| `src/FinanceOperationsPanel.tsx` | 共享 | 挂载关联编辑器 |
| `e2e/finance-operations.spec.ts` | 共享 | 只加关联断言与车辆主数据 |
| `nexus_backend/tests/test_fsc17_links.py` | FSC17 | SQLite 关联/折旧 |
| `nexus_backend/tests/test_fsc17_links_postgres.py` | FSC17 | PG16/17 关联 |
| `nexus_backend/tests/test_tax04_recovery.py` | 共享 | 只加 FSC17 恢复 |
