# FSC16 验收：包干维修合同与额度冲回

## 范围

- 版本化包干维修合同：草稿 → 独立确认 → 生效；合同号/版本/覆盖范围/车辆/有效期/额度/累计策略。
- 额度台账：登记占用、释放；按合同累计（跨月累计），占用/释放均保留历史。
- 超额度、超释放、未生效合同、车辆不在覆盖范围、跨账套/跨事务所访问均拒绝。
- 同一 `idempotency_key` 同内容重放；异内容 409。
- 包干额度只作管理台账，不生成费用凭证、不改变实际成本、不替代费用核定。

## 实现

- 迁移：
  - `nexus_backend/migrations/sqlite/198_fsc16_repair_package_contracts.sql`
  - `nexus_backend/migrations/postgres/283_fsc16_repair_package_contracts.sql`
- 服务：`nexus_backend/app/tax/packages.py::RepairPackageService`
- 路由：`nexus_backend/app/tax/router.py`（`/repair-packages`）
- 前端：`src/TaxCostBatchPanel.tsx` + `src/tax-cost.css`
- 测试：
  - `nexus_backend/tests/test_fsc16_packages.py`
  - `nexus_backend/tests/test_fsc16_packages_postgres.py`
  - `nexus_backend/tests/test_tax04_recovery.py::test_fsc16_package_contract_and_usage_survive_database_restore`
  - `e2e/fsc16-packages.spec.ts`

## 验证结果

| 环境 | 命令/范围 | 结果 |
| --- | --- | --- |
| SQLite | `test_fsc16_packages.py + test_tax09_costs.py` | 15 passed / 24.35s |
| PG17 | 隔离 runner `test_fsc16_packages_postgres.py` | 4 passed / 2.09s，`tmp_test/ak05-pg-runs/1b1e415383764fecb40213c7234ef246` |
| PG16 | 隔离 runner 同文件 | 4 passed / 2.10s，`tmp_test/ak05-pg-runs/a5bcc71e107f4428b952b1cbc68af265` |
| 浏览器 | `e2e/fsc16-packages.spec.ts` | 2 passed / 38.0s（1280/390） |
| 恢复 | SQLite 全库备份/verify/restore | 1 passed / 7.44s |
| 类型 | `npm run typecheck` | 0 |

PG 覆盖：独立确认、生效、占用累计、超额 409、超释放 409、幂等重放、跨账套 404、跨事务所 404、两线程并发占用仅一笔成功。浏览器覆盖：创建草稿 → reviewer 确认 → 生效 → 占用 30 → 释放 10 → 余额 80 与两条 usage 历史。

截图：`tmp_test/fsc16-packages-1280.png`、`tmp_test/fsc16-packages-390.png`。

## 候选

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source：`2352 / 7817238a6ae338b85c0090fdd2edd96cea9601f15fb2a947f952cde4a5597277`
- migration：`467 / 50fb3cc398b1683c9f662d02a2a9baea8cf7e3ffc92450a17d47607ab434fe21`

## 未验证/边界

- 真实客户包干合同、真实维修工单/发票、T+ 外部回执与归档未接入。
- 包干合同跨两家企业、跨年浏览器全链未做；PG 已做跨事务所拒绝。
- 包干额度与月度税费/凭证生成的联动不在本批；额度不直接生成凭证。
- 未提交、未推送、未部署。
