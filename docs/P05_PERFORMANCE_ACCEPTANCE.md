# P05 性能验收：阻断（仅完成测量）

- 结论：**阻断**。本轮只建立可复现基线、定位根因、做前后对照，**未修改任何产品代码**。
- 原因：工作令规定「P01–P04 前置未通过时只允许准备测量工具，不开始影响业务规则的优化」。实际状态为 P01 报告缺失、P02 部分完成、P03/P04 阻断，进入条件不满足。
- 候选：`8a21224d test: add recovery inventory coverage and baseline docs`（本轮未提交、未推送、未部署）。
- 设备：Windows 11 + WSL2（Ubuntu 22.04），PostgreSQL 16.15 / 17.11，Python 3.14（`.venv`）。

## 一、基线与测量环境

| 项 | 值 |
| --- | --- |
| HEAD | `8a21224d` |
| PG16 | `16.15 (Ubuntu 16.15-1.pgdg22.04+1)`，端口 **25531**，cluster `p02pg16` |
| PG17 | `17.11 (Ubuntu 17.11-1.pgdg22.04+2)`，端口 **25532**，cluster `p02pg17` |
| 数据库 | 一次性库 `p02_chain`，迁移 001–178（跳过 91/136/137/139–147 的历史空档） |
| 受限角色 | `p02_app_pg16`（API）、`p02_wrk_pg16`（worker），密码 20 字符，见 `tmp_test/p02-pg.local.json`（gitignored） |
| 合成数据 | 1 事务所 / 1 企业 / 1 账套 / 7 个科目 / 若干事件，由 `tmp_test/p05_measure.py` 自建 |
| 并发 | 单进程顺序请求（`rounds=15`），无并发压测 |

数据集只有一家企业，因此**不能**用本轮数字外推 200–500 家产能；本轮所有结论只用于「同一路径前后对照」。

## 二、已有能力与测量工具

仓库既有的性能相关资产：`scripts/release_candidate.py`（候选指纹）、`nexus_backend/app/operations/efficiency.py`（计量口径）、`app/observability`（只读快照）、`app/operations/meter.py`。**没有**请求级 SQL 计时、没有慢查询面板、没有前端 Web Vitals 采集。本任务新增（全部在 `tmp_test/`，未入库）：

- `tmp_test/p05_measure.py`：以受限角色起 `create_app(...)` + stub 身份，用 HTTP 中间件按 SQL 指纹枚举每条语句的耗时，输出 `tmp_test/p05-measure.json`（每次请求的 statement 数、distinct 指纹数、db p50/p95、wall p50/p95）。支持 `P05_ONLY` / `P05_VERBOSE` / `P05_DUMP`。
- `tmp_test/p05_profile_bootstrap.py`：对 `/api/workspace/bootstrap` 逐语句剖析，产出 `tmp_test/p05-slow.json` / `p05-slow.sql`。
- `tmp_test/p05_explain.py`：在隔离库对只读语句采集 `EXPLAIN (ANALYZE, BUFFERS)`，产出 `tmp_test/p05-plan.txt`。
- `tmp_test/p05_rls_time.py`：单独计时 RLS 辅助函数。
- `tmp_test/p05_jit_ab.py`：同一 SQL 文本在 `jit=on` / `jit=off` 下的 A/B。

采样不含原件内容、提示词、令牌或客户数据；SQL 文本只写本地 `tmp_test/`。

## 三、瓶颈证据与本轮选择

冷/热、成功/失败分开统计；模型替身本轮未参与（全部端点不触达模型）。

### 基线（PG16，`jit` 默认开启，`rounds=15`）

| 端点 | 语句数 | distinct 指纹 | db p50 | wall p50 |
| --- | --- | --- | --- | --- |
| `GET /api/me` | 3 | 3 | 1.94 ms | 5.18 ms |
| `GET /api/workspace/bootstrap` | 6 | 6 | **521.43 ms** | **525.40 ms** |
| `GET /api/workbench` | 3 | 3 | 7.20 ms | 11.09 ms |
| `GET /api/books/{id}` | 9 | 8 | 26.07 ms | 31.72 ms |
| `GET /api/books/{id}/vouchers` | 4 | 4 | 6.66 ms | 10.79 ms |
| `GET /api/tax/context` | 5 | 5 | 6.63 ms | 10.60 ms |
| `GET /api/firm/members` | 3 | 3 | 2.84 ms | 6.63 ms |

两次独立运行（`--rounds 15`）的 p50 偏差在 ±10% 内，故下表按最近一次取值。

`GET /api/books` 在基线里返回 405、`/trial-balance` 返回 409（本期无可用账套期间），据此不计入对照。

### 选定 A：`/api/workspace/bootstrap` 的 JIT 编译开销（唯一主瓶颈）

`tmp_test/p05-plan.txt` 显示该请求的清单聚合 CTE 计划代价 **127,944.63**，超过 PostgreSQL 默认 `jit_above_cost = 100000`，于是每次请求都触发 JIT：

```
Sort  (cost=127944.63..127944.63 rows=1 width=572) (actual time=406.601..406.648 rows=1 loops=1)
...
Planning Time: 19.888 ms
JIT:
  Functions: 1312
  Options: Inlining false, Optimization false, Expressions true, Deforming true
  Timing: Generation 52.559 ms, Inlining 0.000 ms, Optimization 27.605 ms,
          Emission 375.980 ms, Total 456.143 ms
Execution Time: 476.675 ms
```

即单次请求 476 ms 中有 **456 ms 花在 JIT 编译**，真正的执行只有约 20 ms。根因是这条 CTE 按「企业 + 账套 + 期间」取全量 OCR/建议/银行活动并保留 `raw_json`，计划代价高但结果集小——典型的「编译成本 ≫ 执行收益」。

A/B（同一 SQL 文本，各 8 次，`tmp_test/p05_jit_ab.py`）：

| 设置 | p50 | p95 | min |
| --- | --- | --- | --- |
| `jit=on` | 446.21 ms | 532.90 ms | 433.52 ms |
| `jit=off` | **62.95 ms** | **64.26 ms** | 59.62 ms |

端到端复核：把 `jit` 在**库级**关掉后重跑全端点面板（`rounds=15`），`/api/workspace/bootstrap` 变成 db p50 **79.70 ms** / wall p50 **84.20 ms**（含其余 5 条语句），其余端点无变化（`/api/me` 1.95 ms、`/api/workbench` 6.97 ms、`/api/books/{id}` 25.16 ms、`/vouchers` 6.07 ms、`/api/tax/context` 6.34 ms、`/api/firm/members` 2.68 ms）。同一路径 **≈ 525 ms → ≈ 84 ms**，样本内无回退。

### 选定 B（未实施，仅取证）：`GET /api/books/{id}` 的同请求重复查询

一次请求 9 条语句、8 个不同指纹，db 合计 **37 ms**：

| 序号 | 指纹 | 耗时 | 说明 |
| --- | --- | --- | --- |
| #1 | `<access-book-sql>` | 5.42 ms | 访问控制器 |
| #2 | `<access-book-sql>` | 4.01 ms | **与 #1 完全相同** |
| #3 | `WITH visible_books ...` | 9.44 ms | 账套可见性 |
| #4 | `WITH visible_books ...` | 5.81 ms | **与 #3 相同** |
| #7 | `WITH visible_books ...` | 5.69 ms | **与 #3 相同** |

重复部分约 15 ms（占该请求 db 时间的 40%）。这是典型的 N+1 式重复读取，而非计划问题。

### 排除项（有证据但不构成本轮瓶颈）

- RLS 辅助函数本身极快（`p05_rls_time.py`）：`actor_active` 1.13 µs、`firm_role` 0.57 µs、`firm_visible` 1.07 µs、`firm_clients` 0.71 µs；每次请求调用几十次也只有亚毫秒量级，**不是**瓶颈。
- 前端轮询：全仓 9 处 `setInterval`（`src/ActivityTimer.tsx:90`、`src/AgentActivityWorkspace.tsx:154`、`src/EfficiencyWorkbench.tsx:191`、`src/GlobalAccountingAssistant.tsx:663`（活跃任务 1 s / 空闲 15 s）、`src/IntakeWizard.tsx:138`、`src/IntelligencePanel.tsx:233`（1 s，仅本地时钟）、`src/LegacyMigrationWorkspace.tsx:423`、`src/OperationsPerformance.tsx:78`（15 s）、`src/ReviewWorkbench.tsx:120`（15 s）），其中 7 处已做 `document.visibilityState` 保护；无「卸载后继续高频轮询」证据。
- 写锁持有：本轮端点均为只读，未测锁内序列化；不把「全局锁调用点数量」当作性能证据。

## 四、选择表与最小改法（均未应用）

| 瓶颈 | 测量证据 | 用户影响 | 最小改法 | 正确性风险 | 验证方法 |
| --- | --- | --- | --- | --- | --- |
| A. bootstrap JIT | 计划代价 127,944.63 > 100,000；JIT 456 ms / 476 ms | 每次进入工作台多等 ~0.4 s，冷启动更明显 | 在 `Database.begin()` 的会话内对 PG 执行 `SET LOCAL jit = off`（仅当 CTE 型只读聚合，或全局只读会话）；或把 `raw_json` 摘出主 CTE，二次按需取 | 低：JIT 只影响执行速度，不改变语义；但若对写事务全局关闭，长分析型查询可能变慢 | 同数据集 A/B（p50/p95、statement 数）+ PG16/17 回归 + `test_p02_business_flow_postgres.py` |
| B. 账套详情重复查询 | 9 语句 / 8 指纹；#1=#2、#3=#4=#7；重复 ~15 ms | 打开账套多 ~15 ms，规模上去后线性放大 | 同请求内复用 `AccessControl.book()` 结果与 `visible_books` 物化结果（请求级 memo，不跨请求、不缓存授权结论） | 中：必须保证「每次请求重新判定授权」，不可跨请求复用，撤权后不得命中旧结果 | 断言 statement 数 9→6 且授权判定仍逐请求执行；撤权用例 |

选择理由：A 是唯一「单点、量级显著、改动可逆」的问题（≈ 6 倍）；B 收益小一个数量级，且触及授权路径，须等 P01–P04 通过后与安全回归一起做。

## 五、未执行修复的原因

工作令明确规定：P01–P04 未通过时只允许准备测量工具，不开始影响业务规则的优化。当前：

- `docs/P01_RECOVERY_ACCEPTANCE.md` **不存在**（只有 `docs/P01_RECOVERY_COVERAGE_20261029.md`）。
- P02 结论「部分完成」（`docs/P02_BUSINESS_FLOW_ACCEPTANCE.md`，卡在 b04 月结 `bank_reconciliation` / `subledger_control`）。
- P03 结论「阻断」（`docs/P03_BATCH_ACCEPTANCE.md`：D1 批量项落在全局咨询锁、D3 无批量导出/交付、D5 批量项不进执行记录）。
- P04 结论「阻断」（`docs/P04_EXCEPTION_HANDOFF_ACCEPTANCE.md`：E1 执行记录无责任人/下一步、E2 同步 D5、E4 交付 409 不带版本信息）。

因此本轮**不提交任何 `nexus_backend/` 或 `src/` 改动**，只提交测量报告与 `tmp_test/` 取样脚本（未入库）。

## 六、正确性与业务回归

- 本轮零产品代码改动，故未跑业务回归；`git status --porcelain` 仅保留既有 P03 测试修复 `M nexus_backend/tests/test_batch_review_postgres.py` 与四个未跟踪文件。
- 测量期间对隔离库做过一次 `ALTER DATABASE p02_chain SET jit = off` 实验，**已 `RESET jit` 并验证 `pg_db_role_setting` 为空**，未影响其他任务。
- 建议在 P05 正式实施时补：PG16/17 双版本同套件回归（`test_batch_operations`/`test_batch_preview`/`test_monthly_batch`/`test_batch_review`/`test_batch_review_postgres`，基线 43 passed×2）、`test_p02_business_flow_postgres.py`（1 passed）、前端 `vitest`/`tsc`/`build`。

## 七、无收益尝试与未验证边界

**无收益/放弃**
- 曾尝试用集合查询消除预警期取数（P02 批量的 N+1）：7.45 s → 6.99 s，收益不足一个量级，真正成本是每企业一次不可缓存的授权查询（~35 ms × N）。
- 未对 RLS 函数做「优化」：实测已是微秒级，改了只会增加风险。

**未验证**
- PG17 只做了连通性与迁移一致性（001–178、`status --strict`），未跑同一端点面板；跨版本性能差异未知。
- 只有单企业合成数据、单进程顺序请求；无并发、无 200/500 家规模、无长稳（>1 h）。
- 无浏览器侧指标（FCP/TTI、重复请求数、传输量）：本轮没有前端性能采样工具。
- 模型与 OCR 未参与，故不含任何模型延迟结论。
- `GET /api/books`、`/trial-balance` 因无可用期间未取得基线。

## 八、结论与进入条件

- **本轮结论：阻断（仅测量）**。已证明 `/api/workspace/bootstrap` 的 456 ms/请求耗在 JIT 编译上，库级关闭后同路径 525 ms → 84 ms；账套详情存在 ~15 ms 重复查询。两项均**未实施**。
- 进入正式优化需满足：P01 报告产出并通过、P02 完成全部九步链路、P03 批量办理通过、P04 异常接手通过；届时基于**同一候选**复测基线并只应用 A（必要时加 B），随后跑 PG16/17 与业务回归，确保企业隔离、撤权、独立复核、复核后导出、费用/attempt/公平调度不变。
- 复现命令（PG16）：

```bash
./.venv/Scripts/python.exe tmp_test/p05_measure.py --rounds 15
./.venv/Scripts/python.exe tmp_test/p05_profile_bootstrap.py
./.venv/Scripts/python.exe tmp_test/p05_explain.py
./.venv/Scripts/python.exe tmp_test/p05_rls_time.py
./.venv/Scripts/python.exe tmp_test/p05_jit_ab.py
```

- 原始样本：`tmp_test/p05-measure.json`、`tmp_test/p05-slow.json`、`tmp_test/p05-slow.sql`、`tmp_test/p05-plan.txt`、`tmp_test/p05-errors.log`（均在 gitignored 目录，未提交）。
