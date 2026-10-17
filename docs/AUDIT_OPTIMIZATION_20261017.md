# 优化审计报告（2026-10-17）

审计人：Codex（在本仓库连续完成第一、二、三批改造后的复盘审计）
审计方式：通读代码 + 亲手改造 + 实测数据（全量 pytest / vitest / Playwright / 构建产物 / 计数取证）
基线：截至 `a8e0f57d`（第三批全部落地）

---

## 一、总体结论

前三批（界面收敛与角色减负 → 所长统筹与大批量上传/发票导入 → 并发、运维与投影）已经把审计里
最尖锐的"阶段堆叠"问题处理掉了：路由收敛为单一事实来源、五步流水线落地、写锁开始按企业并行、
待办物化为投影、后台进程可合并为 1 个、迁移可向前兼容回滚。

**当前的主要矛盾已经从"功能堆叠"转向"规模化运营 + 开发体验"**：

1. 本机开发环境与 `requirements.txt` 严重漂移（fastapi 0.109/starlette 0.35 vs 声明的 ≥0.133），
   直接导致本机 17 个失败 + 106 个夹具错误——所有"红"都来自环境，而不是产品缺陷。这会持续
   消耗每一轮改造的排查时间。
2. 写锁只有约 17% 的写事务迁到了企业级（199 处 `write=True`，仅 33 处 company scope），
   会计复核/导出、银行导入、交付、月结这些高频域仍在全局串行。
3. 第三批新落地的投影与统一 Worker 只是"机制就绪"，还缺运营化（指标、批量预热、失败告警、
   文档），生产启用前需要补齐。

三个仍然领先行业水平的资产没有变化，且本轮审计没有发现新的削弱：整数分记账与内控、
FORCE RLS 纵深防御、可审计的交付闭环。

---

## 二、优先级矩阵（建议按此顺序推进第四批）

| # | 事项 | 价值 | 工作量 | 风险 |
|---|------|------|--------|------|
| 1 | 开发环境一致性（锁版本 + env doctor + 文档） | 高 | 小 | 低 |
| 2 | 写锁第二批：复核/导出、银行导入、交付、月结等 | 高 | 中 | 中（需按域+回归） |
| 3 | 投影运营化：批量重建入口、命中率指标、失败告警 | 高 | 中 | 低 |
| 4 | CI/e2e 提速：Playwright 分片 + smoke 集 | 中 | 小 | 低 |
| 5 | 前端请求样板收敛：useScopedQuery / useUpload | 中 | 中 | 低 |
| 6 | 文档补齐（上传与发票导入 / 写锁与迁移兼容 / 统一 Worker 与投影运维） | 中 | 小 | 低 |
| 7 | 巨石拆分：App.tsx、LegacyMigrationWorkspace、后端 top 文件 | 中 | 大 | 中 |
| 8 | 构建与样式治理：vendor 分块、tokens.css | 低 | 中 | 低 |
| 9 | 产品增量：发票红字/部分匹配、>10MB 分片上传、告警触达 | 中 | 中 | 中 |
| 10 | 迁移模板 CI 检查：新表必附 RLS/guard/授权/兼容头 | 中 | 小 | 低 |

---

## 三、逐域发现

### A. 并发与写锁（继续兑现规模目标的关键）

**现状（取证）**：`nexus_backend/app` 全部 `write=True` 共 199 处，显式 `write_scope="company"`
约 33 处——即约 83% 的写事务仍在 `lock_legacy_write()` 全局串行。第一批已迁移：
员工录入、业务事项、异常补件、资料清单、发票导入。

**优先迁移的第二批候选（按并发收益排序，含当前全局事务数）**：

| 域 | 文件 | 全局写事务 | 说明 |
|----|------|-----------|------|
| 凭证复核与导出 | `accounting/service.py`(7)、`review.py`(4)、`catalog.py`(3) | 14 | 会计主战场，单账套单企业，收益最大 |
| 银行导入与流水处置 | `import_wizard/service.py`(3)、`dispositions/service.py`(1)、`reconciliation/service.py`(1) | 5 | 批量导入最怕被别的企业阻塞 |
| 交付与回执 | `delivery/service.py`(3)、`closing/service.py`(3) | 6 | 月结高峰期的串行点 |
| 月度与资料服务 | `monthly/service.py`(7)（批量开期除外）、`settlements/service.py`(4) | 11 | 需区分单户与批量 |
| 质量与学习 | `quality/service.py`(3)、`learning/service.py`(4) | 7 | 低风险顺手迁 |
| 助手交互写 | `assistant/service.py`(3)、`assistant/actions.py`(2) | 5 | 对话式操作为单企业 |

**明确保持全局锁（不要迁移）**：`accounting/directory.py`、`admin_identity.py`、
`admin_service.py`（事务所级配置）、`monthly/batch.py`（跨企业批量）、
`accounting/handover.py`（跨企业转派）、`legacy_migration/*`（多企业迁移调度）、
`jobs/*`（调度域，已有独立 dispatch 门）、`uploads.py`（企业与用户级通道，无企业字段）。

**建议**：沿用第一批的节奏——每域同时改 Python（写前一次公司预读）、升级 `089` 的
`company_write_table_guard` 逐表映射、每域补一组"事务确实带 company scope"的回归用例
（第一批的 `test_company_write_scope_wave1.py` 可直接套模板）。**一域一个提交**，便于独立回滚。

### B. 待办投影运营化（第三批机制已就绪，缺运营）

**现状（取证）**：
- 干净时单表索引查询、脏时回退实时 14 路（正确性不依赖新鲜度）——已实现并有一致性测试；
- 但**重建只发生在交互读取的 API 进程**：每次最多 5 家企业（`PROJECTION_REFRESH_PER_REQUEST`）。
  一个管理 200–500 家企业的所长，首次部署后要经过几十次请求才能全部预热；期间每次读取
  都多两条查询（新鲜度检查）并回退实时路径。
- 重建失败被 `logger.warning` 吞掉，没有指标、没有告警（`workspace/service.py` 的
  `_prepare_projection`），生产上"投影一直不生效"可以无声无息。
- Worker 身份拿不到 `accounting_system`（这是有意的安全约束），所以统一 Worker 无法直接预热。

**建议**：
1. 给 `accounting_worker` 增加一个 **SECURITY DEFINER 的 `refresh_workspace_projection(firm, company)`**
   函数（沿用全仓既有的 definer 函数模式），统一 Worker 在 maintenance 里批量预热 dirty 企业，
   交互读取从此基本只做"检查 + 单表查询"。
2. 增加三个计数器：`projection_served / projection_fallback / projection_rebuilt`（挂在
   `transaction_metric_buckets` 同级的指标通道或 ops 快照），并把"连续回退"接进现有
   `ops_alerts`（alert kind 已有骨架）。
3. 管理端加一个"批量重建本所待办投影"按钮（复用第三批的刷新函数，分页执行、写审计）。
4. 把"投影只加速、绝不成为正确性来源"的约束写进迁移与代码注释（已部分完成），并保留
   对 14 条支线的 live/投影一致性测试作为长期护栏。

### C. 开发环境与测试基建（当前最大的工程效率黑洞）

**现状（取证）**：
- 本机解释器环境：`fastapi 0.109.0 / starlette 0.35.1`；`nexus_backend/requirements.txt`
  声明 `fastapi>=0.133,<0.134`（starlette 未直接钉版本，走传递依赖）。
- 全量 pytest 结果：**1816 passed / 653 skipped / 17 failed / 106 errors**；其中 17+106
  已用 `git stash` 逐个对照证实**与代码无关，全部来自旧版本依赖**（例如
  `TestClient(client=...)` 在 0.35 不存在，直接导致 106 个夹具错误）。
- CI（`.github/workflows/accounting.yml`）是完整的：npm format/lint/test/build、
  ruff check + format、Postgres service + 全量 migrate、全量 pytest、PaddleOCR 边界测试、
  全量 Playwright——也就是说 CI 的质量门槛很高，**问题只在本机**。
- 本地 ruff 0.15.12 与锁定 `ruff==0.17.0` 不一致；按 CI 同口径（app/services/scripts）本地
  仍有 3 个文件格式不过（其中 `service_plans/service.py` 在基线即不过，需用锁定版本复核后清零）。
- e2e 共 48 个 spec、单 worker 串行；本机实测单批 3–4 个 spec 需要 5–12 分钟，且 business/
  stage8/stage20/batch1 存在 3–7 个**基线同败**的用例（stash 对照证实），每次回归都要靠
  人工对照，非常消耗。

**建议**：
1. **锁版本**：requirements 里显式钉 `starlette`（与 fastapi 0.133 匹配的区间），并生成
   `requirements.lock`（`pip freeze`），CI 与本机都用锁文件安装；`ruff` 用锁定版本执行。
2. **env doctor**：新增 `scripts/env_doctor.py`（或 npm script），打印/校验 关键包版本、
   迁移数量、`ACCOUNTING_*` 配置，一条命令判断"本机能否跑全量测试"，并输出"请重建 venv"的
   明确指引。
3. **本机已知红名单**：把"stale env 下 106+17"与"e2e 基线失败清单"写进 `docs/`（或 README），
   避免每轮都重新做 stash 对照。
4. **e2e 提速**：Playwright 分片（`--shard`，配置里每个进程天然拿自己的时间戳库，可直接用）；
   再拆一个 10 分钟以内的 smoke 集用于日常与本地回归。

### D. 前端架构债（审计的第一批只完成了一半）

**现状（取证）**：
- `src/` 219 个文件仍然全部平铺在根目录；`App.tsx` 仍有 **2572 行**（当时目标是 ≤400），
  `LegacyMigrationWorkspace.tsx` **3128 行**，`BusinessWorkspace.tsx` 1682 行。
- 作用域竞态样板（`epoch.current` / `selectionEpoch` / AbortController / 撤权清空）全库
  **312 处**，正是审计里"可提炼 useScopedQuery/useScopedMutation、减少约 20% 样板"的对象。
- `styles.css` 90KB + 39 个分散 CSS，无 tokens 层。
- 构建产物：入口 `index-*.js` **527KB（gzip≈164KB）**，只有工作台被懒加载；
  `dist` 无 vendor 手动分块（react/react-dom/supabase-js/lucide 全在入口）。

**建议（分三步，不要一次大爆炸）**：
1. 先提炼 `useScopedQuery`（自动中止、过期丢弃、撤权清空）与 `useUpload`（FormData/
   两段式直传/失败重试统一），替换 312 处样板中的前 1/3（`WorkspaceHome`、
   `BusinessWorkspace`、`AgentNavigation` 周边），用 vitest 补两个 hook 测试。
2. `LegacyMigrationWorkspace` 按向导五步拆成子组件（这是全仓最大单体，且周边测试齐全）。
3. 最后再做 `src/app|domains|shared` 目录重排 + `tokens.css` + vendor 分块（`manualChunks`）；
   目录搬迁放到最后，避免与在飞功能互相打架。

### E. 后端模块规模

**现状（取证）**：最大文件 `legacy_migration/service.py` 81KB、`intelligence/service.py` 74KB、
`accounting/service.py` 58KB、`learning/service.py` 57KB、`workspace/service.py` 56KB
（第三批后含投影读取约 +500 行）。

**建议**：`legacy_migration/service.py` 拆成「项目/文件/解析/映射/验收」五个模块（与前端同一批）；
`workspace/service.py` 建议把"投影读取"迁到 `workspace/projection_read.py`，保持 service 只做编排。
后端拆分没有前端那么急，可以随写锁第二批顺路进行。

### F. 部署、文档与运维口径

**现状（取证）**：`docs/` 79 篇，最后一次文档提交停在 stage25 发布（第一二三批新增的
上传直传、发票导入、迁移兼容、统一 Worker、写锁范围、投影机制都没有文档）；
`STAGE25_PRACTICE_WORKSPACE.md` 仍按"4 个常驻进程"口径描述生产部署。

**另外发现一个真实部署缺口**：网关硬编码单文件 3 MiB / 请求 4 MiB
（`services/api_gateway/main.py`）。>2 MiB 的文件现在会走两段式直传：
- 配了 Supabase：浏览器直传对象存储，完全绕开网关 ✓；
- **没有 Supabase 的自建部署**：会落到同源 `PUT /api/uploads/{id}/content`，>4 MiB 时仍会被
  网关 413 拦住（最高 10MB 的文件在这类部署上失败）。要么文档明确"大文件部署必须配 Supabase"，
  要么给网关加一条针对该路径的流式放行（有内存上限约束，需要评估）。

**建议**：补三篇文档 + 更新两篇：
- 新增《OPS_UNIFIED_WORKER.md》（统一 Worker 变量、拆分策略、心跳与停止语义）
- 新增《WORKSPACE_PROJECTIONS.md》（机制、回退语义、预热与指标、故障处理）
- 新增《UPLOADS_AND_INVOICE_IMPORTS.md》（阈值、用途校验、发票列容错、发票导入与银行预匹配口径）
- 更新《POSTGRES_SETUP/DEPLOY》：迁移兼容机制（哪些迁移必须标 breaking）、写锁范围矩阵、
  "大文件部署必须 Supabase"或网关方案

### G. 产品增量机会（按客户价值排序）

1. **发票导入的匹配升级**（第三批只做了第一版）：
   - 红字发票与负数金额目前不参与银行预匹配（代码有意跳过），可加"红蓝对开"对冲线索；
   - 对手方目前是"精确或包含"匹配，可加常用别名/税号匹配（同号优先，"名称近似+税号相同"次之）；
   - 部分付款（银行金额 < 价税合计）目前不会命中，可加"金额小于票面且合计匹配"的分笔线索；
   - 批次可加"异常台账导出"（作废/红字/重复/未勾稽四类）。
2. **>10MB 分片上传**：目前单文件上限 10MB、无断点续传；旧账迁移包与影像扫描 PDF 会撞墙。
   现有 upload intent 状态机（pending→stored→consuming→consumed）可直接扩展
   `part_count/parts`，前端复用 `upload-client.ts`。
3. **告警触达**：`ops_alerts` 已有完整骨架，但目前只有列表，没有企业微信/邮件/短信通道；
   至少给"模型预算耗尽、任务连续失败、投影持续回退"三类接一条通知。

### H. 数据与安全（继续保持，无需大动）

- 新表（upload_intents、invoice_import_batches、workspace 投影两张）均已附 RLS、FORCE RLS、
  写门触发器与最小授权；投影的 RLS 逐行调用与源表相同的可见性函数，没有放宽暴露面。
- **建议固化**：把"新表必须同时提交 RLS + batch2 写门触发器 + 最小 GRANT + 兼容头"
  写成迁移模板注释 + 一个 CI 检查脚本（扫描最新迁移里每张新表是否三者齐备），
  防止下一批迁移漏项。
- 备份：`scripts/backup_accounting.py` + 恢复用例（`test_stage10_backup_recovery`）已存在；
  建议加"生产季度恢复演练"的日程化条目与演练记录模板。

### I. 可观测性

**现状**：`transaction_metric_buckets` 已记录每个事务所/企业的连接、锁等待、事务耗时；
`worker_heartbeats` / `assistant_worker_heartbeats` 已有。**缺的是投影与写锁范围的视图**：
现在没有任何指标能回答"投影命中率多少、哪些企业长期回退、全局锁等待是否下降"。

**建议**：写锁第二批上线时同步加"按域的锁等待"分组（`lock_wait_ms` 已有原始数据，只差
按 write_scope 打标），投影加命中/回退/重建计数（见 B），并给 `ops` 页加一个"并发与投影"
小节。标准很简单：**每个优化都必须有上线后可验证的数字，否则不做**。

---

## 四、本周不建议做的事（防止过度工程）

- 不要为了目录重排做一次性大迁移；等 `useScopedQuery/useUpload` 落地后再搬。
- 不要动 ActivityTimer 的人工计时口径（用户已明确）。
- 不要放开网关 4 MiB 限制换通用大请求；大文件走存储直传是对的方向，缺的只是部署口径。
- 不要给投影加"写事务内同步重建"：全局锁代价大于收益，worker 预热 + 读取回退已经够。
- 不要把 e2e 全量在每次本地改动里跑；smoke 集 + CI 全量即可。

---

## 五、建议的第四批任务单（可直接开工）

1. `env_doctor` + 锁版本 + 本机红名单文档（0.5 天）
2. 写锁第二批·甲：凭证复核/导出域（accounting service + review + catalog）（1 天）
3. 写锁第二批·乙：银行导入 + 处置 + 对账域（0.5 天）
4. 投影运营化：worker 预热函数 + 指标 + 告警 + 管理端重建按钮（1 天）
5. CI/e2e：shard + smoke 集（0.5 天）
6. 文档三篇 + 两处更新（0.5 天）
7. 前端：useScopedQuery/useUpload + 首批替换（1 天）

以上每一项的验收标准沿用三批惯例：新增用例 + 全量回归与基线逐字一致 + e2e 关键链路。

---

## 附：本报告数据来源

- 全量 pytest（本机）：`1816 passed / 653 skipped / 17 failed / 106 errors`；
  失败与错误经 `git stash` 对照证实为旧依赖环境所致。
- CI 定义：`.github/workflows/accounting.yml`（含 Postgres 服务、全量迁移、pytest、Playwright）。
- 计数：`write=True` 199 / `write_scope="company"` 33；迁移文件 sqlite 71、postgres 90；
  e2e spec 48；`src` 平铺文件 219；`epoch` 样板 312 处；`styles.css` 90KB + 39 CSS；
  入口包 527KB（gzip≈164KB）。
- 依赖实测：本机 `fastapi 0.109.0 / starlette 0.35.1`（rejects `TestClient(client=...)`），
  声明依赖 `fastapi>=0.133,<0.134`；本地 ruff 0.15.12 vs 锁定 0.17.0。
