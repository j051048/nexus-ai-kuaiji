# 当前状态索引

更新：2026-10-20  
用途：只记录当前候选可复现事实；历史阶段报告不作为当前验收。

## 当前可信结论

| 能力 | 状态 | 证据 |
|---|---|---|
| 前端展示契约 | 通过 | `workspace-presentation.test.ts` 10 |
| 401/403 | 通过 | `test_http_auth_origin_and_loopback_boundaries` 1 |
| MFA/管理 403 | 通过 | `stage18-admin` 8 |
| 首页 bootstrap 单次 | 通过 | `p0-request-count.spec.ts` 1 |
| 批量部分失败状态机 | 通过 | `test_batch_operations.py` 2 |
| 批量失败前端展示/重试 | 通过（前端 mock） | `p0-batch-partial-ui.spec.ts` 1 |
| 迁移 status 分类 | 通过 | `test_ag00_migrate_status.py` 1 |
| 迁移幂等 lint | 通过 | `test_ag00_migration_idempotency.py` 3 |
| PG 假绿守卫 | 通过 | `test_p0_pg_target.py` 1 |
| 合成收票到导出 | 通过（API/服务） | `test_intelligence_workflow` 2 |
| 独立月结 | 通过（独立夹具） | `test_stage10_closing` 1 |
| 同客户导出→回执→月结 | 通过（合成 usage/价格） | `test_same_customer_invoice_export_delivery_and_close` |
| 小规模/一般纳税人税额估算 | 纯计算通过，未接 UI/申报 | `test_tax_estimate.py` 9 |
| AG04 预算/证据/快照 | 通过（离线） | `test_ag04_investigation.py` |
| AG05 身份/分母/H01 薄适配 | 通过（离线） | `test_ag05_examples.py` |

## 未验证

- 同一客户：导出 → 银行对账 → 月结 → 报表 → 交付；
- 真实 PostgreSQL `migrate` / `status --strict` / PG 对等套件；
- 线上自动迁移和回滚；
- 一般纳税人进销项/税负；
- 真实模型效果；
- 真实客户试点；
- 生产多 worker 恢复；
- 财务软件直连。

## 不应再引用的旧数字

- 旧 UX 报告的 351 项前端测试；
- 旧 AG 报告的 42/124 项；
- 旧 DL/OCR 报告的历史候选数量。

以本文件和各包最新报告为准。

## 下一步

1. 用同一合成客户串联导出与月结；
2. 补银行对账与交付；
3. 一般纳税人分支；
4. 真实 PG 环境后跑对等套件。
