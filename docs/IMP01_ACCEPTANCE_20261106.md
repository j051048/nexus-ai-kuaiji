# IMP01 验收记录：候选与验证状态统一

- 包：IMP01-A（基线/门禁）
- 日期：2026-11-06
- 工程状态：**本地组合通过**（A 阶段基线 + B 阶段最终组合）；同候选 P01 恢复已通过；外部门槛仍未验证。
- 外部验收：远端 CI / 生产 / 真人 / 真机均 pending 或 not_authorized
- 运行目录：`tmp_test/imp/imp-20261106-a/`

## 1. 基线

| 项 | 值 |
|---|---|
| HEAD | `143d4aea7154b1905b96ab757ecd14865354b641` |
| 分支 | `main` |
| source files | 1905 |
| source sha256 | `b5959882e70a04beea56bfc3ed9a6efdab725cd4c1b90497b8193a4db227300f` |
| migration files | 338 |
| migration sha256 | `2c96acd52de191347a2365962e879340918fad013786faa260b4b13140646116` |
| PG 迁移 | 205，最大 `217_client_task_notifications.sql` |
| SQLite 迁移 | 133，最大 `133_client_task_notifications.sql` |
| 未跟踪文件 | `docs/AUDIT_SYNTHESIS_RECOMMENDATIONS_20261105.md`、`docs/DEEPSEEK_IMP01_IMP10_EXECUTION_20261106.md` |
| 已跟踪修改 | 无 |

原始指纹：`tmp_test/imp/imp-20261106-a/candidate-before.json`

## 2. 本轮影响矩阵

| 包 | 代码域 | 需要浏览器 | 需要 PG | 需要恢复 | 说明 |
|---|---|---|---|---|---|
| IMP02 | 前端壳层/身份 | 是（4 视口） | 否 | 否 | 不改后端权限 |
| IMP03 | 前端待办/文案 | 是 | 否 | 否 | 不改任务状态机 |
| IMP04 | 前端录入/核验 | 是（375/390） | 否 | 否 | 真机另列 |
| IMP05 | 试用/协同 | 是 | 视协同链 | 否 | 本地夹具与替身 |
| IMP06 | 关键页面 CSS/a11y | 是 | 否 | 否 | 不全局替换 |
| IMP07 | 离线评测 | 否 | 否 | 否 | 纯回放 |
| IMP08 | 助手只读分析 | 是（证据/入口） | 是 | 否 | 版本/撤权需 PG |
| IMP09 | 性能/容量 | 是（预算/LCP） | 是 | 否 | 本机多进程，不称多节点 |
| IMP10 | 通知读侧/DB 职责 | 否 | 是 | 是 | 窄域，保留 DB 防线 |
| IMP01-B | 全部门禁 | 是 | 是 | 是 | 同候选冻结 |

## 3. 已有能力与复用判断

| 能力 | 结论 |
|---|---|
| `release_candidate.py` | 已覆盖应用、前端、依赖、迁移、测试、CI；本轮直接复用 |
| `check_release_candidate.py` | 已能拒绝候选不一致、假 summary、伪造/截断 JUnit、PG16/17 串证、脏工作树发布 |
| `run_e2e.mjs` | 已有浏览器聚合入口；本轮不另建 |
| `p01_recovery_gate.py` | 已有三阶段恢复门禁；本轮按需启动 |
| PG 隔离运行器 | 已有；PG16/17 真实受限角色 |
| `frontend_budget.mjs` | 已有闭包测量；当前旧 `dist` 仅 `initialGzip` 超 291 字节，待重建 |
| 角色导航/身份 | 已有；IMP02 只压缩与补测 |
| 员工短清单/草稿/退回 | 已有；IMP03/04 复用 |
| 客户服务端分页 | 已有；IMP09 不重建 |
| React.lazy | 已有；IMP09 不重复“代码分割”改造 |
| H01/AG05/RSI/replay | 已有；IMP07 做增量 |
| 通知 unknown/attempt | 已有；IMP10 先盘点再决定是否迁移 |

## 4. 门禁负向测试映射

命令：

```bash
.venv/Scripts/python.exe -m pytest -q \
  nexus_backend/tests/test_release_candidate.py \
  nexus_backend/tests/test_next_harness.py \
  --junitxml=tmp_test/imp/imp-20261106-a/imp01-tools.xml
```

结果：**69 passed in 83.38s**，exit 0。

| 要求 | 现有覆盖 |
|---|---|
| 01-T1 证据候选与当前候选不一致，拒绝 | `test_two_pg_versions_must_bind_the_same_complete_candidate`、`test_frontend_dependency_and_deployment_changes_invalidate_candidate` |
| 01-T2 子任务失败/取消/跳过、JUnit 缺失/截断/只收集不执行，拒绝 | `test_untrusted_summary_cannot_claim_pass`、`test_raw_junit_is_revalidated_even_when_hash_and_summary_claim_pass`、`test_missing_pg_or_missing_raw_junit_blocks` |
| 01-T3 PG16 冒充 PG17、缺恢复阶段，拒绝 | `test_two_pg_versions_must_bind_the_same_complete_candidate`、`test_release_plan_requires_bound_artifacts_and_recorded_checks` |
| 01-T4 测试期间修改应用/配置/夹具，不能仍通过 | `test_source_changes_after_pg_runs_invalidate_evidence`、`test_source_change_while_reading_reports_is_blocked` |
| 01-T5 重复运行不覆盖旧配置/证据；失败可定位阶段/测试 ID/脱敏原因 | `test_duplicate_json_keys_are_rejected`、`test_dirty_worktree_can_pass_technical_but_never_release`；运行目录隔离见 `tmp_test/imp/` |
| 01-T6 文档补记不得标为应用验证；不能自写 `passed=true` | `test_untrusted_summary_cannot_claim_pass`、`test_complete_manual_checklist_is_never_online_verification_or_approval` |

结论：现有聚合器已能拒绝上述假绿，本轮**不修工具**。

## 5. 前端预算基线（非验收）

```bash
node scripts/frontend_budget.mjs dist tmp_test/imp/imp-20261106-a/frontend-budget-before.json
```

- exit 1
- `initialJs=565001`、`initialGzip=176291`、`initialCss=99677`
- `homeJs=592823`、`homeGzip=186975`、`homeCss=143834`
- 唯一违规：`budget:initialGzip`（176291 > 176000，超 291 字节）
- 说明：`dist` 可能为旧构建；IMP06-A 先重建，不能把本数字直接当最终候选性能

## 6. 当前结论

- IMP01-A：基线、归属、影响矩阵、负向门禁映射完成。
- IMP01-B：最终候选 **1927 / `e6a94703…` / 338 / `2c96acd5…`** 已冻结；Python 90 passed、浏览器 21 passed、typecheck/build/budget 通过；详见 `docs/IMP01_B_ACCEPTANCE_20261106.md`。
- 同候选 P01 恢复已通过：1927 上 regression 183、joint 2、identity 4，PG16/17 八项全 true；证据 `tmp_test/imp/imp-20261106-a/imp01b-p01-1927/reports/summary.json`。
- 未提交、未推送、未部署；未调用真实模型/通知。
- 远端 CI、生产、真人、真机：pending / not_authorized。

## 7. 剩余问题

- IMP08 正向票据差异只有 SQLite；PG 仅 smoke。
- IMP09 同机双 worker，不是多节点；PG16 结束 pool 3/4 快照不能证明长期无泄漏。
- 全量 Accounting foundation 仍有未迁移夹具，另案处理。
- 真人试用、真机、真实供应商、真实模型未验证。
