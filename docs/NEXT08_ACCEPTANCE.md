# NEXT08 验收记录：批量导出与交付

- 工程状态：**本地验收通过**（后端 SQLite + PG16/17 + 真实浏览器批量开期）。
- 外部验收：不适用（不接生产 worker/对象存储；本轮未触发真实通知、发布或签收）。
- 候选：head `df3fface…`，source `1816`/`dddef1a88eee0bfdc2c2db37d09560dc0290b434fe800993c05b52b7ba2a963e`，migration `325`/`48fc7b47…`（ddb 最终候选；含并发任务的税务 UI 修改）。
- 环境：Windows 本地 `.venv`（Python 3.14.1）；WSL PG16/17 隔离集群 25496/25497，私有配置不公开；fresh SQLite 浏览器由 Playwright 自建；受限身份，不经临时 SQL 补业务状态。

## 实际缺陷与修复

1. **恢复注册表漏税局回执附件**：`test_p0_f10_manifest_routes.py` 6 项失败（consumer 返回不一致，备份/恢复注册表缺 `tax_filing_attachment`）。修复 `scripts/backup_accounting.py`，登记 `tax_filings.tax_filing_attachments.filing_id`；测试同时校验六类 consumer 与 tax 项两侧存在。
2. **旧测试夹具越权**：`test_batch_review.py` 给 operator/viewer 的 `book_access` 直接写 `reviewer`，触发数据库“正式复核人必须是授权员工/复核角色”约束。按 membership 映射 `operator→editor, viewer→viewer, reviewer→reviewer`，保留“非复核人不能打复核页”的原断言。属夹具缺陷，不是放宽产品权限。
3. **PG 复核包夹具违反独立复核**：`test_stage11_review_packages_postgres.py` 用制单人自己预览并选择，产品正确返回 `ready_count=0`；改为另一 reviewer 预览/确认，独立复核门槛不降低。

## 命令与结果

| 套件 | 命令要点 | 结果 | 证据 |
| --- | --- | --- | --- |
| SQLite 全量 | `pytest test_batch_operations test_batch_preview test_batch_review test_dcp04_batch_control test_dcp06_review_export test_q05_export_consistency test_p0_f10_manifest_routes` | **69 passed, 1 warning, 112.96s，exit 0（ddb 最终候选）** | `tmp_test/next/next08-final3.log` |
| PG17 | `scripts/test_accounting_postgres_isolated.py --config …/pg17-recovery_source.json test_batch_review_postgres.py test_stage11_review_packages_postgres.py` | **10 passed, 11.94s，exit 0（ddb 最终候选）** | `tmp_test/ak05-pg-runs/03e38034f9b84ad0bdd02dc68c1b6e5c/results.xml` |
| PG16 | 同上，`pg16-recovery_source.json` | **10 passed, 12.38s，exit 0（ddb 最终候选）** | `tmp_test/ak05-pg-runs/fcd4974f170a47bcb1fc877ef96b9e7f/results.xml` |
| 浏览器 | `E2E_FRONTEND_MODE=preview npx playwright test e2e/monthly-batch.spec.ts` | **1 passed, 26.6s，exit 0（最终候选 54d9）** | Playwright 输出；截图/trace 在 `test-results/` |

说明：SQLite 与 PG 的部分用例名重叠，69 与 10 不是相加的独立用例总量；PG 只重跑批量/复核包相关文件。浏览器 1 项是真实 UI 批量开期主链，业务成功响应未用 `route.fulfill`。

## 场景对照

| 要求 | 覆盖 |
| --- | --- |
| 已复核凭证批量导出 | `test_cross_company_preview_checks_and_confirm_keeps_export_explicit`、`test_execute_exports_the_pinned_drafts_and_records_reference` |
| 交付包草稿批量生成 | `test_preview_lists_approved_drafts_and_reports_blockers`、`test_create_requires_the_confirmed_manifest` |
| 部分企业缺前提、逐户失败 | `test_monthly_open_partial_failure_retries_only_failed_item`、`test_same_book_blocked_item_rolls_back_all_and_other_book_succeeds` |
| 排队/运行中/提交后取消 | `test_cancel_running_item_records_intent_and_keeps_committed_result`、`test_skip_and_cancel_require_reason_and_terminal_items_are_rejected` |
| 重试不清零 attempt、不复活 cancelled | `test_retry_keeps_attempts_and_never_revives_cancelled`、`test_retry_without_failed_items_is_rejected` |
| 来源版本变化、撤权、错 manifest | `test_execute_refuses_after_the_source_changes`、`test_manifest_changes_when_another_approved_draft_appears`、`test_preview_is_private_to_creator_and_revoked_grant_hides_it` |
| 提交后响应丢失查既有结果 | `test_idempotency_key_cannot_switch_preview_and_preview_cannot_switch_key`、`test_concurrent_confirm_produces_one_approval_one_sample_and_stable_receipt` |
| worker 重启、租约、竞争 | `test_parent_command_can_resume_unfinished_books_after_process_restart`、`test_unfinished_command_expires_and_records_failure_before_resume` |
| CSV/附件绑定对象版本 | `test_failure_csv_escapes_formula_and_lists_only_authorized_items`、`test_execute_exports_the_pinned_drafts_and_records_reference` |
| 保存对象/版本/来源/结果 | batch receipt、manifest 与失败清单列；SQLite/PG 均校验不可变和伪造成功拒绝 |

## 边界

- 批量不自动复核、不发布、不通知、不签收；复核/导出仍需独立授权。
- 未验证：真实对象存储大附件下载、真实 worker 多进程租约、生产 PG 容量。不能据此宣称生产批量能力已验收。
