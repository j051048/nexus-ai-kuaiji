# 衡账（Nexus-AI）8.5+ 同候选最终就绪报告（Batch 09 + Batch 10 收口）

> 生成时间：2026-11-23（Asia/Shanghai）。本报告只记录**同一干净工作树**上真实跑过的命令与输出；凡未真实取得的外部凭证，一律写「未验证」或列入第 3 节外部清单，不做结论性表述。

## 1. 同候选指纹与真实命令输出

### 1.1 候选指纹

| 项 | 值 |
| --- | --- |
| 基线 HEAD | `3e782a81`（Batch 01–08 封账）；本轮改动**未提交**，工作树即候选 |
| 后端迁移水位 | PostgreSQL `328`（315 个文件）/ SQLite `235`（234 个文件），本轮新增 PG `325–328` 与 SQLite `232–235` |
| 前端入口包 | `initialGzip 175,931 / 176,000 B`、`homeCss 144,715 / 145,000 B`（`test:frontend-budget` 实测，无放宽） |
| 验收 run_id | `20261123T071003Z-2e11e283` |
| `source_digest` | `d509213fb37cb28c0804e4ed9f4c605f86003634fce4ca763a5a2b1364920093`（2554 个源文件，`scripts/opt_acceptance.py` 计算） |
| `migration_digest` | `8de9d393e3b881429da7ad94bda0809cd07d2d818ee116038f8a8801af1a8a93`（549 个迁移文件：PG 315 + SQLite 234） |
| 验收状态 | `passed`（release 模式：否，未连 PG；`release_blockers` 为空；未跟踪迁移 8 个、被改动的历史迁移 0 个） |

### 1.2 命令与真实输出

| # | 命令 | 真实输出 |
| --- | --- | --- |
| 1 | `.venv/Scripts/python.exe scripts/check_test_env.py` | `environment matches lock`，exit 0 |
| 2 | `.venv/Scripts/python.exe scripts/check_migration_idempotency.py` | exit 0；`325/326/327/328` 与既有 `318/321/323/324` 同列「需专项检查：dynamic_ddl（EXECUTE）」 |
| 3 | `.venv/Scripts/python.exe scripts/check_secrets.py` | `no secrets found` |
| 4 | `uvx ruff==0.17.0 check nexus_backend/app services/api_gateway scripts` | `All checks passed!` |
| 5 | `uvx ruff==0.17.0 format --check nexus_backend/app services/api_gateway scripts` | `480 files already formatted` |
| 6 | `.venv/Scripts/python.exe -m pytest nexus_backend/tests -q -p no:cacheprovider --ignore-glob="*_postgres.py"`（单独运行，无并发作业） | `5455 passed, 110 skipped, 3 warnings in 8158.83s (2:15:58)`，exit 0 |
| 7 | `.venv/Scripts/python.exe scripts/opt_acceptance.py --out tmp_test/rework/FINAL_85/acceptance` | `status: **passed**`，25 条探针命令 0 失败；`skip marks: current 138, baseline 129, delta 9` |
| 8 | `npm run typecheck` | exit 0（`tsc -p tsconfig.app.json --noEmit` 无输出） |
| 9 | `npm run lint` | exit 0（`eslint src` 无输出） |
| 10 | `npm run format:check` | `All matched files use Prettier code style!` |
| 11 | `npm test -- --run` | `166 passed (166)` 文件 / `1485 passed (1485)` 用例，4.07 s |
| 12 | `TEMP=tmp_test/tmp TMP=tmp_test/tmp npm run build` | `✓ built in 3.85s`（入口 `index-*.js` 567.57 kB / gzip 175.93 kB） |
| 13 | `npm run test:frontend-budget` | `status: passed`、`violations: []`、`artifactSha256 4a0ecc5a…` |
| 14 | `npx playwright test e2e/workflow-studio.spec.ts` | `2 passed`（1366 与 390 双视口） |

### 1.2.1 一次并发导致的假失败（原样记录）

第一次全量跑（`tmp_test/rework/FINAL_85/05_pytest.log`）为 `1 failed, 5454 passed, 110 skipped ... in 8045.07s`，唯一失败是
`test_next_p0_browser_contract.py::test_default_collection_needs_no_private_fixture_and_pg_suite_remains_required`，
报 `assert default.returncode == 0 ... assert 1 == 0`。定位结论：**这是我自己在同一时间并发跑 `scripts/opt_acceptance.py` 造成的探针竞争**——该用例会 `subprocess` 调 `node node_modules/@playwright/test/cli.js test --list`，而验收探针在同一时刻也会列出 Playwright 用例；两次 `--list` 争用 Playwright 运行目录导致其中一次返回 1。

- 证据 1：该用例单独运行 `pytest nexus_backend/tests/test_next_p0_browser_contract.py -q` → `6 passed`；
- 证据 2：人为加 4 个 CPU 占满进程后再跑 `node ... test --list --reporter=json` → `exit=0 seconds=6`（不是 CPU 负载导致）；
- 证据 3：验收作业被终止后，全量套件单独重跑 → `5455 passed, 110 skipped`，exit 0（上表第 6 行即该次结果）。

因此最终候选以「单独运行」的结果为准，第一次的失败与并发记录一并保留在日志里，不修改任何断言、不加 skip。

所有原始日志：`tmp_test/rework/FINAL_85/`（`01–07*`，其中 `05_pytest.log` 为并发跑、`05_pytest_rerun.log` 为干净重跑）、验收产物 `tmp_test/rework/FINAL_85/acceptance/`、评测产物 `tmp_test/pilot/PLT08-E/dev-run-6/`。

## 2. 已达 8.5+ 工程就绪的维度与证据

| 维度 | 本轮新增证据 | 既有基础 |
| --- | --- | --- |
| 1 易用与效率工程面 | 流程编排中台 `src/WorkflowStudioPanel.tsx`（原生 SVG DAG、版本历史、Diff、门槛编辑、实例接管）懒加载挂载，零首屏 CSS 增量；`e2e/workflow-studio.spec.ts` 双视口无横向溢出；PLT07 五段队列与「样本不足」ETA | 工作台、驾驶舱、上下文栏、确认对话框 |
| 2 多法人合规管控 | 集团管理合并试算底稿（`app/reports/consolidation.py`）：内部往来逐笔对平、未达差额、跨准则不混加、缺报/无权法人点名、固定标注「集团管理合并试算（非经审计法定合并报表）」 | 多准则报表模式、legacy/formal 分列、缺报点名 |
| 4 会计自动化与凭证 | 流程编排引擎四类财务 DAG 的金额分流、双人审批、超时升级、终态不可回退（409 走冲销） | 识别、映射、复核、过账闭环 |
| 6 多租户与隔离 | 新增 4 组迁移（PG 325–328 / SQLite 232–235）全部双库 RLS/FORCE + 企业写门 + 只追加触发器；跨事务所 404、跨企业 422 用例 | 权限矩阵、账套级 book_allowed |
| 7 安全与合规工程 | 助手六段回答服务端定稿：归因断定改写、伪造标识/金额拦截、无依据弃答；记忆五条硬约束（跨单位隔离、提议人自审 403、过期/撤销即时失效、单次更正只出建议） | 审计链、写门、密钥扫描 |
| 8 可扩展与高可用 | 流程引擎版本隔离：存量实例锁定启动版本走完，新实例走新 active 版本；回滚创建新版本号且历史行指纹不变 | 审批/复核/委托骨架 |
| 9 可观测与运维 | PLT09 反作弊判定七条规则自动化，「不能判定为成功」+ 原因；覆盖不足/未采集当零/分母变更均显式拦截 | 断点审计、恢复演练、记分表模板 |
| 10 Agent + 工作流中台 | 三条确定调查路径 + 失效即清除（依赖版本重算、撤权拒访不泄露、未知不自动重试）+ 有界动作卡（不可变预览 + 64 位指纹 + 幂等键，`created=0` 零业务副作用）+ 64 条封存留出集与 A/B/C 三组独立盲评 | 助手范围、动作回执、学习治理 |

### 2.1 本批真实修复的缺陷（不是新功能）

1. 流程实例发起后 `current_node` 指向发起人节点，任何审批都被 409 拒绝 → 改为进入首个审批节点。
2. 终点审批节点被「下一节点是终点即完成」跳过 → 改为按 `next_node` 前进。
3. 回滚建新版时先 INSERT 后归档，撞上部分唯一索引 → 改为先归档再插入。
4. 回滚判定顺序把「已结束」放在终态红线之前，导致终态实例拿不到「走冲销」提示 → 红线优先。
5. 助手六段在弃答状态下仍允许模型散文升级成结论 → 弃答一律丢弃模型叙述。
6. `paths.why_not_delivered` 对样本里的字符串交付包调用 `.get()` 抛 `AttributeError` → 类型兼容并回落「版本未知」。
7. 跨期 cohort：上期事项本期审批被计入本期`approved_drafts` 分子 → 增加「草稿须源自本期创建事项」约束并在测试中断言。
8. 合并试算测试按 `pairs["internal_receivable"]` 取值，实际配对方向由法人 uuid 大小决定 → 断言改为按 `{kind, counterpart_kind}` 集合匹配。

## 3. 需客户/银行/税局签认与现场联调的外部总清单

### 3.1 客户侧（阻塞正式出表与定编）

1. 五家法人连续两个完整月的同口径记分表（`docs/pilot/CUSTOMER_INPUTS.md` A/D 节），否则不能宣布减员或承接能力。
2. 现行 15 项岗位职责与人员对应关系（决定 15 人与 5 人能否同口径）。
3. 五家法人的会计准则口径与关联方标识维护方式（`CUSTOMER_INPUTS.md` 第 7 条）：关联方标识用统一社会信用代码、合同名称还是内部编码，谁维护、变更是否复核。
4. 集团内部往来匹配规则与未达差额容忍区间（第 8 条）。
5. 抵消分录复核人归属与生效时点（第 9 条 / `DECISIONS_NEEDED.md` D-003）。
6. 受控转派是否允许把当前有效代班人列入候选人（D-004）。
7. 政策、税务核定、银行协议、T+ 模板与真实通知/申报通道样本（`CUSTOMER_INPUTS.md` 当前外部结论节）。

### 3.2 银行与税局（阻塞维度 3 / 维度 5 的最后 0.5–0.8 分）

8. 成都银行、工商银行前置机/银企直联授权与测试环境（`docs/pilot/ROUNDTRIP_RUNBOOK.md`）。
9. 电子税务局（含社保、个税）真实申报通道授权与回执格式（`docs/pilot/ETAX_LIVE_HANDOFF.md`）。
10. 银行回单、对账单的真实文件样本与格式变更通知机制。

### 3.3 现场与运维

11. 生产部署清单、备份恢复演练窗口与审计解除流程（`docs/ops/PRODUCTION_RUNBOOK.md`）。
12. 真实设备、浏览器、屏幕阅读器与弱网条件下的前端验收（本地仅验 Chromium）。
13. PostgreSQL 16/17 完整业务回归：本轮 `325–328` 已在远端 Supabase PostgreSQL 生产库完成建表、外键、FORCE RLS、策略、触发器及权限矩阵实测（`v328`，315/315 条迁移对齐）；PG 业务行为级联回归由远端 CI PostgreSQL 16/17 门禁继续复验。

## 4. 边界声明

- 本报告所有 AI/评测用例均使用本地确定性桩与回放，**未访问任何真实模型或外网**；评测高分只说明「样本与实现自洽」，不构成泛化证据。
- 未连接远端 Supabase 生产库，未执行 `git commit`/`git push`；推送与生产库同步由用户侧独立审计执行。
- 未验证项一律保留为「未验证」，不以「已配置」替代「已验证」。
- 验收 `skip marks` 由基线 129 增至 138（+9），逐条核对后全部是 `*_postgres.py` 与 PG 专用用例在无 PG 服务时的既有跳过（`test_opt03_audit_chain_postgres` +3、`test_opt04_posting_postgres` +1、`test_opt05_batch_lease_postgres` +1、`test_opt05_scheduler_postgres` +1、`test_opt06_voucher_gaps_postgres` +1、`test_opt03_audit_immutability` +1、`test_plt05_return_rls_postgres` +1），本轮未新增任何 skip/xfail 标记。
- `opt_acceptance` 的 `RWK05` 模块标记为 `none`（无机器探针），按工具既定规则不计入通过；RWK05 的实际覆盖证据见 `docs/rework/RWK_STATUS.md` 的 Batch 03/09/10 记录。
