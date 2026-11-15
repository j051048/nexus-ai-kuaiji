# 出租车代账试点｜批次 C 验收记录（TAX05–TAX06）

执行日期：2026-11-11
适用规范：`docs/TAXI_DEEPSEEK_BATCH_C_20261110.md`
结论级别：**本地工程通过 / PG16、PG17 通过 / 浏览器通过 / P01 通过；外部 T+、真实客户资料、真机与生产未验证**。

## 1. 基线与归属

| 项目 | 值 |
| --- | --- |
| 仓库 | `C:\Users\Fei\Desktop\AI应用\nexus-ai-kuaiji` |
| HEAD | `781fd93499dcba7bb76056f0febe19e372202af2` |
| 候选源文件 | 2101 |
| 候选源指纹 | `1ecf01e8f1432c068ebb179b52c0beeeab5bed551066122ba65cccac780ae64e` |
| 迁移文件 | 365 |
| 迁移指纹 | `e5d9e52b4f838476c99bfdc5ab7e56bbf7259b7df4a452a2e07c60ea044cc39f` |
| 工作区 | 94 项未提交（含本记录 3 个文档）；未提交、未推送、未部署 |
| 本轮新增迁移 | sqlite `147_tax_fee_batches.sql`；postgres `230_tax_fee_batches.sql` |

候选由 `scripts/release_candidate.py` 计算，文档不在候选的 `PREFIXES` 内，因此写本记录不改变候选。批次 A/B 的未提交改动仍保留，未覆盖、未回退。

## 2. 复用与修改范围

复用：既有 `sources/drafts/draft_lines`、草稿版本、`AccountingService.approve` 独立复核、导出、RLS、原件、batch operations、队列重试、B 的 `tax_source_files` 采用行与 transport 主数据/班次。

修改：
- `nexus_backend/app/accounting/models.py`：`DraftUpdate.lines` 上限 100→2400。
- `nexus_backend/app/accounting/service.py`：对 `external_id` 以 `tax-fee:` 开头的应计来源跳过银行分录方向检查（规费收入确认没有银行行）。
- `nexus_backend/app/tax/fees.py`：规费批次生成、汇总、独立复核、版本失效。
- `nexus_backend/app/tax/router.py`：fee-batches 生成/列表/详情/复核/校验/失效。
- `nexus_backend/app/main.py`：注册 `state.tax_fees`。
- `nexus_backend/app/tax/sources.py`：驾驶员/车辆/优惠/调整列改为可选列映射。
- `src/JournalEntryGrid.tsx`、`src/journal-grid.css`：分页、行定位。
- `src/TaxFeeBatchPanel.tsx`、`src/tax-fee.css`：规费批次办理面板。
- 测试与 E2E：`test_large_voucher.py`、`test_tax06_fees.py`、`test_tax06_fees_postgres.py`、`test_tax06_recovery.py`、`e2e/tax05-large-voucher.spec.ts`、`e2e/tax06-fees.spec.ts` 及两个 Playwright 配置。

## 3. TAX05 受控大凭证

### 3.1 方案选择

先复现既有上限：`DraftUpdate.lines` 原来最多 100 行，258/575/1001 个借贷对（516/1150/2002 行）均无法通过。比较后采用**受控大凭证**，不机械拆成多张凭证：

- 单张凭证保留完整业务平衡与来源绑定，避免拆分母凭证、重复复核和跨凭证对账。
- 上限显式设为 **2400 行**；超过仍拒绝，不取消上限。
- 每行仍只允许借方或贷方一侧非零；借贷合计必须等于来源金额。
- 前端采用 **50 行/页**、绝对行号、上一页/下一页和“定位行”，避免一次渲染全部 2002 行。
- 不使用“关闭同科目/辅助去重”来绕过约束。

### 3.2 证据

| 场景 | 结果 |
| --- | --- |
| 258 对（516 行） | 生成、平衡、`issues=[]`、独立复核通过 |
| 575 对（1150 行） | 同上 |
| 1001 对（2002 行） | 同上 |
| 同来源第二次 suggestions | `created=0`，仍只有 1 张草稿（幂等） |
| 2401 行请求 | Pydantic 422 拒绝；2400 行通过 |
| 1201 个采用行（2402 行） | 规费批次 413，不生成半批 |
| 真实浏览器 516 行 | 首屏 50 行；定位 516 后显示第 501–516 行；独立复核后“已审核，待导出” |

命令与结果：

```text
cd nexus_backend
python -m pytest tests/test_large_voucher.py -q --disable-warnings
# 4 passed / 6.13s

npx playwright test --config playwright.tax05.config.ts
# 2 passed / 15.9s（1280、390）
```

截图：`tmp_test/tax05-large-voucher-1280.png`、`tmp_test/tax05-large-voucher-390.png`、`tmp_test/tax05-pager-1280.png`、`tmp_test/tax05-pager-390.png`。

## 4. TAX06 规费批次

### 4.1 成功路径

- 策略必须显式包含 `version`、驾驶员应收科目、单车收入科目；没有默认税率或默认科目。
- 只读取 B 已采用的来源行和当前期间；来源必须 active、同账套、同期间。
- 驾驶员应收借方、单车收入贷方；双班两人一车各自生成行；优惠、调整、零额、负额均有确定处理。
- 净额 = 毛额 − 优惠 + 调整；零额只记录 `zero`，不生成分录；负额、缺主档、无有效班次、调整缺理由记 `rejected` 并阻止复核。
- 生成合成来源、1 张草稿、每条 adopted 行 2 条分录，并写 `draft_line_sources`（源文件/行/驾驶员/车辆/角色/金额）和 `tax_fee_batch_rows.line_ordinal`。
- 批次指纹相同则幂等返回旧批次；并发生成只落 1 条。
- 复核在同一写事务内调用 `_approve_in_transaction`，失败整体回滚；制单人自审 409。
- 来源 fingerprint 变化后 review/validate 409；已复核批次显示 `stale` 和“旧审批与导出已失效”。
- 人工改稿后再次生成不覆盖草稿 description/version。
- 批次汇总含 `by_driver`/`by_vehicle`，并保留来源文件名、版本、外部 ID、sha256 前 12 位和调整理由。

### 4.2 证据

| 场景 | 结果 |
| --- | --- |
| 正常 2 行：100−10、80+5 | 总额 175.00；2 adopted、1 rejected；4 行草稿；借贷各 175.00 |
| 零额/负额/未知驾驶员/无班次/缺调整理由 | 零额/负额不生成；未知/无班次/缺理由 rejected；总额 425.00，adopted 2、skipped 4 |
| 336/335 差异 | 保留源行 `adjustment_reason`；不按序号固定排除 |
| 双班两人一车 | D1 白班、D2 夜班同车 V1，两行均 adopted，总额 180.00，4 行草稿 |
| 并发生成 | 2 线程同源同策略，同一 batch id，库中 1 条 |
| 人工改稿 | 修改 description 后重新生成，仍返回同批，description/version 不变 |
| 自审 | 409“制单人与复核人必须不同” |
| 来源升级 | 旧批 review/validate 409；已复核批 stale |
| 跨期来源 | 期间与账套不一致 409，不生成上月批次 |
| 跨期更正 | 100−20=80，保留“更正上期多计规费” |
| 外部事务所 RLS | 404 |

命令与结果：

```text
cd nexus_backend
python -m pytest tests/test_large_voucher.py tests/test_tax06_fees.py \
  tests/test_tax06_recovery.py tests/test_tax04_sources.py -q --disable-warnings
# 33 passed / 41.83s

npx playwright test --config playwright.tax06.config.ts
# 2 passed / 22.9s（1280、390）
```

PG：

```text
python scripts/test_accounting_postgres_isolated.py --config tmp_test/tax-c/pg17.json \
  nexus_backend/tests/test_tax06_fees_postgres.py -q
# 3 passed / 2.36s

python scripts/test_accounting_postgres_isolated.py --config tmp_test/tax-c/pg16.json \
  nexus_backend/tests/test_tax06_fees_postgres.py -q
# 3 passed / 2.37s
```

截图：`tmp_test/tax06-fees-1280.png`、`tmp_test/tax06-fees-390.png`。

## 5. 构建与类型

```text
npm run typecheck
# 0

TEMP="$PWD/tmp_test/esbuild-tmp" TMP="$PWD/tmp_test/esbuild-tmp" npm run build
# success
# index JS 565.54 kB / gzip 175.19 kB
# CSS 99.72 kB / gzip 20.27 kB
# TaxWorkpaperPanel 51.27 kB / gzip 16.69 kB
```

`npm run build` 直接使用系统 TEMP 时曾报 esbuild 临时文件 `Access is denied`；改用仓库内 TEMP 后成功，属于本机临时目录问题，不是 TypeScript 错误。

## 6. 恢复（P01）

环境：`environment_id=8cf97847e94c4702ae41632cdbfbfc11`，run `298392bfcd744d57b53ea688de175a08`。
报告：`tmp_test/tax-c/p01-final/reports/summary.json`。

| 项目 | PG16 | PG17 |
| --- | --- | --- |
| 版本 | 16.15 | 17.11 |
| migrations | 218 | 218 |
| originals | 4 | 4 |
| restored users | 6 | 6 |
| capability grants | 973 | 973 |
| column grants | 55 | 55 |
| database/originals/identity/permissions/hold/review_export/tax_delivery/no_replay/finance_resume | 全 true | 全 true |

阶段：regression 188 passed / 152.134s；joint 2 / 87.428s；identity 4 / 55.768s；0 skipped。P01 源/目标均已应用到 `schema_migrations` 的最大版本 **230**，`tax_fee_batches`、`tax_fee_batch_rows`、`draft_line_sources` 在源与目标均存在。

C 行级恢复由 `nexus_backend/tests/test_tax06_recovery.py` 覆盖：SQLite 全库备份→verify→restore 后，批次行、草稿分录、`draft_line_sources` 源行关系、状态和指纹保留。P01 的通用业务检查不专门比对 C 表行数，故不能把 P01 说成“C 业务行级恢复已验证”；这里分别陈述。

## 7. 明确未完成 / 外部门槛

1. **真实 T+**：未生成或导入 T+ 文件；T+ 属批次 D。
2. **真实客户资料**：全部为合成 xlsx/虚构驾驶员、车辆、金额；未使用客户真实原始资料。
3. **真实规则版本**：策略由调用方显式提供并冻结在批次内，没有可动态切换的客户规则目录；因此“规则版本改变自动失效”没有外部规则源可验，只验证了来源版本变化。
4. **大凭证队列/取消**：大凭证生成/复核走同步 API + 原子事务，没有新增“大凭证批次取消/恢复”状态机；既有 batch/queue 回归通过，但不能宣称已实现大凭证取消语义。
5. **575/1001 浏览器**：浏览器只办理 516 行（258 对），575/1001 仅在服务层验证；未做 2002 行浏览器性能测试。
6. **真机/通知/CI/生产**：未验证；未提交、未推送、未部署。

## 8. 结论

- 本地工程：**通过**（TAX05/06 成功路径、负向、并发、幂等、版本失效、人工改稿保护）。
- PG16/17：**通过**（动态权限、RLS、复核）。
- 浏览器桌面/手机：**通过**（1280/390）。
- P01：**通过**（同一候选 2101/`1ecf...`）。
- 批次 C 的本地可执行范围已完成；上述 7 项外部/边界事项不得写成已验证。
