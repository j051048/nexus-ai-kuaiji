# FSC15 维修费率生命周期验收（2026-11-17）

## 范围

- 费率卡草稿 → 独立确认 → 生效/续期/替代/停用。
- 适用车型/期间/覆盖范围/单位/币种/含税口径与版本依据。
- 历史版本保留、影响预览、批次生成/复核按当前费率重新校验。
- 跨年有效期、重叠拒绝、停用后复核拒绝、数据库恢复。

## 实现

- 迁移 `sqlite/197_fsc15_repair_rate_lifecycle.sql`、`postgres/282_fsc15_repair_rate_lifecycle.sql`：状态、版本、逻辑卡、确认/生效/替代/停用时间、原因、单位/币种/含税、指纹。
- `RepairRateService`：草稿、独立确认、生效、续期、停用、历史、影响；旧 `add` 保留为 legacy active v1，并写 `tax.rate.legacy_active`。
- 复核/正式记账：`_rate_card_guard` 仅接受 active/superseded，来源携带 `rate_version` 时必须与卡版本一致；停用/变化使旧来源复核 409。
- UI `TaxCostBatchPanel`：创建草稿、独立确认、生效、续期、停用、影响；修复 `loadSequence`，避免旧列表响应覆盖新状态。
- 浏览器 `e2e/fsc15-rates.spec.ts`：1280/390 真实填写、自确认 409、独立确认、激活、停用。

## 验证

| 项目 | 结果 |
| --- | --- |
| SQLite `test_fsc15_rate_lifecycle + test_tax09_costs + test_tax04_recovery` | **20 passed / 37.65s** |
| PG17 `test_fsc15_rate_lifecycle_postgres` | **3 passed / 1.61s**，artifact `4e247e0a9a8a4252b04d4f3b741ac764` |
| PG16 同文件 | **3 passed / 1.58s**，artifact `697f821c687f4ca3af7f8cfe8777736d` |
| 浏览器 1280/390 | **2 passed / 38.5s** |
| 恢复 | `test_fsc15_repair_rate_versions_survive_database_restore` **1 passed / 7.54s** |
| 静态检查 | typecheck 0、Prettier、ruff check/format 通过 |

PG 用例覆盖：自确认 409；reviewer 确认；激活；历史；停用；重叠 active 409；跨年 2026-12..2027-02 可 resolve 2027-01。

## 未完成 / 未验证

- 两企业同时存在费率的 PG 隔离链未单独覆盖（单企业 PG + 账套范围已覆盖）。
- 真实客户费率合同、真实维修工单/供应商回执未验证。
- 跨年度批次连续生成/复核/导出的完整业务链未做浏览器级验证。
- 与 FSC16 包干维修结算的冲回联动未做。

## 结论

本地/PG16/17/浏览器/恢复核心能力通过；因两企业隔离与真实客户条件未满足，标为**部分通过**，不宣称生产发布就绪。
