# P02 统一批量作业体验：范围 → 预检 → 执行 → 逐户结果 → 失败重试

日期：2026-10-27　候选：`tmp_test/qr01-integration`（未提交、未推送）　状态：**完成，待验收**

## 1. 目标与边界

工作令要求批量作业具备"范围选择 → 预检 → 执行 → 逐户结果 → 失败重试"，且执行前必须让操作人看到服务端算出的影响范围，不能盲点执行。

本轮只做月度开期这一条真实批量链路（系统里唯一的批量写作业，`batch_ops` 也只有 `CreateMonthlyOpenBatch`），复用既有 `MonthlyOpenBatch`/`batch_operation_items`/重试接口，不新建队列、不引入发送通道、不触线上。

## 2. 改动

### 后端：只读预检（新增，不改执行语义）

- `nexus_backend/app/monthly/batch.py`：`MonthlyBatchService.preview()` 复用与执行完全相同的范围选择 `_companies()`（scope=`companies|awaiting`、`search`、上限 200），逐户算出 `ready|skipped|blocked`：
  - `already_open`：该月账套已存在（执行时会被跳过）；
  - `no_history`：没有任何历史账期，长期账套尚未建立模板；
  - `not_client` / `no_access`：不是本所在册客户或已停用；
  - 逐户带 `from_period`（将沿用的上期账套）。
  - 结果 `{period, ready, skipped, blocked, results[], notes[], truncated}`；只读，不写库、不占锁、不产生审计事件（预检不是决策）。
- `nexus_backend/app/batch_ops/service.py`：`preview_monthly_open(actor, body)` 走现有 `AccessControl.require_manager` 与账期校验，返回同一结构；不信任前端传入的公司清单。
- `nexus_backend/app/batch_ops/router.py`：`POST /api/firm/batch-operations/monthly-open/preview`，请求体 `reason` 仍要求 ≥3 字（防"无声预检"被当作审批凭据）。

### 前端：执行按钮被预检门控

- 新增 `src/batch-preview.ts`（+ `src/batch-preview.test.ts` 4 例）：
  - `validateBatchPreview()`：服务端结构不符时整份丢弃，界面回到"尚未预检"，**绝不显示猜出来的数字**；
  - `previewVerdict()`：`ready=0` 时禁止执行并给出"没有可直接开期的企业（跳过 X 家、阻塞 Y 家）"；
  - `previewLines()`：只列被跳过/阻塞的企业，未知错误码原样显示，不翻译成乐观文案。
- `src/MonthlyBatchDialog.tsx`：
  - 新增"预检范围"按钮与预检面板（`aria-live="polite"`）；执行按钮在未预检或 `canRun=false` 时 `disabled`，并提示"尚未预检：先预检范围，再执行开期。"；
  - 范围（月份/搜索词/是否生成资料模板）任一变化即清空预检结果，避免用旧数字执行新范围；
  - 执行后仍逐户展示结果，失败企业可"重试未完成"，重复提交由既有 `idempotency_key` 保证不重复执行。
- `src/monthly.css`：预检面板样式（被阻塞行用警示底色，不用颜色单独表意，同时保留文字原因）。

### 并发撞车：状态必须说实话（缺陷修复）

- 发现：10 个操作员同时开同一批 60 家时，库里去重是对的（60 条账套、0 重复），但**逐户状态全是 `created`，十份报告加起来 600 次“已创建”**。原因是 `_open_company` 先查“是否已存在”（独立事务），再调 `create_period`；两者之间被别的操作员抢先提交时，`create_period` 复用同一幂等键返回 `replayed=True`，批量层却把这次复用当成自己创建。
- 修复：`nexus_backend/app/monthly/batch.py` 在建期返回 `replayed` 时直接返回 `skipped`（`另一位操作员已创建该企业本月账套，本次未重复创建。`），并且**不再执行服务单继承、资料催收与创建审计**——别人的创建不能被记成自己的。
- 用例：`nexus_backend/tests/test_monthly_batch.py::test_batch_open_reports_a_concurrent_win_as_skipped`（先红：`opened=1`；后绿：`opened=0/skipped=1`，且不带新增创建审计）。
- 数据面：同一脚本复跑后 `created 合计 60`、账套 60、重复 0。

### 用例（先红后绿）

- `nexus_backend/tests/test_batch_preview.py`（6 例，含 200 家上限提示）：已开期=skipped、无历史=blocked、正常=ready、非本所客户不计入、reason 过短拒绝。
- `src/batch-preview.test.ts`（4 例）：结构非法整份拒绝、`ready=0` 不可执行、未知码原样、只列非 ready 行。
- `e2e/monthly-batch.spec.ts`：改为先断言执行按钮 `disabled` → 点"预检范围" → 断言 `将开期 2 家；跳过 0 家、阻塞 0 家` → 按钮转为可用 → 执行 → 逐户结果（新增门控证据，不删既有断言）。
- `e2e/p0-batch-partial-ui.spec.ts`：补 preview 路由桩（真实结构），保留"部分失败逐项展示 + 只重试失败项"的原断言。
- `e2e/r5-acceptance.spec.ts`：移动端 409 用例补 preview 桩与"必须先看到预检"步骤，保留"409 保留输入、关闭回焦、`attempts=1`"原断言。

## 3. 证据

补充说明：两个 PG 脚本都只连 127.0.0.1 的高位端口（25531/25532），使用本机已有的受限登录角色（`accounting_app`，非超级用户、受 RLS 约束），数据库每次 `DROP ... WITH (FORCE)` 重建，不触碰任何既有数据；脚本内所有企业名、用户、账套均为虚构。

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 预检/批量后端 | `pytest test_monthly_batch.py test_batch_operations.py test_batch_preview.py -q` | **10 passed**（32.16s） |
| 前端单测（全量） | `npx vitest run` | **100 文件 / 995 passed** |
| 批量 e2e（真实后端） | `E2E_MODE=dev npx playwright test e2e/monthly-batch.spec.ts` | **1 passed**（14.8s，先 disabled 后 enabled） |
| 部分失败 mock e2e | `... e2e/p0-batch-partial-ui.spec.ts` | **1 passed**（16.5s） |
| 移动端 409 e2e | `... e2e/r5-acceptance.spec.ts -g 手机409` | **1 passed** |
| 同链路回归 | `... e2e/qs04-chain.spec.ts e2e/q04-client-signing.spec.ts` | **3 passed** |
| PG 预检实证（PG16+PG17） | `python tmp_test/p02_pg_preview.py` | **PASS**：空库迁移 156 版本 → 以受限角色 `accounting_app` 在 RLS 下预检，两集群均 `ready/skipped/blocked = 1/1/2`，逐户 `ready / already_open / no_history / no_access` 全部符合预期 |
| PG 执行实证（PG16+PG17） | `python tmp_test/p02_pg_execute.py` | **PASS**：一次性空库 `p02_exec` + 156 迁移；长期账套客户首跑 `created`、已开期客户 `skipped`；**同一请求重跑全部 `skipped`，未产生重复账套**；库内 2026-11 恰为该两家各一条 |
| 200 家规模采样·冷库（PG16+PG17，首次） | `python tmp_test/p02_pg_scale.py` | **PASS**：单批 200 家；预检 **6.99s / 6.94s**；执行 **28.00s / 26.85s**（200 家全部 `created`，无一被回滚）；重跑 **1.62s / 1.52s**（0 家重复）；库内 2026-11 账套恰 200 条 |
| 双操作员并发（PG16+PG17） | `python tmp_test/p02_pg_concurrent.py` | **PASS**：40 家企业，A 开前 30、B 开 20–39 同时执行；墙钟 **2.30s / 2.01s**（A、B 各自 2.20s / 2.13s）；结果 `2026-11 账套 40（重复 0）`；A 全 `created`，B 在重叠区间拿到 `created`+`skipped`；无异常、无死锁 |
| 跨事务所隔离（PG16+PG17） | `python tmp_test/p02_pg_tenancy.py` | **PASS**：同一库里两家事务所各 8 家客户同时开期，墙钟 **0.74s / 0.73s**；`2026-11` 账套按所各 8 条；A 所显式提交 B 所企业号时，开期与预检**都返回 `blocked`**；以 `SET ROLE accounting_app` + `app.firm_id=FA` 直查 B 所账套得 **0 条**，查自己得 16 条 |
| 10 操作员高争用（PG16+PG17） | `python tmp_test/p02_pg_contention.py` | **PASS**：60 家企业、10 个操作员同时开同一批；墙钟 **13.23s / 12.69s**；`created 合计 60`（恰每家一次）、账套 60、**重复 0**、错误 0。修复前同一脚本报 `created 合计 600` |
| 格式/静态 | `prettier --check src`、`eslint`、`tsc --noEmit`、`ruff check` + `ruff format --check nexus_backend/app services/api_gateway scripts`（CI 同口径，310 文件） | 全绿 |

失败项与归因（未修复，属于既有用例建模，不在 P02 范围）：

- `e2e/r5-acceptance.spec.ts` 第 3 例（真实本地批量复核）停在"确认批量复核"不可见：该用例仍用同一会计"制单 + 复核"，与 Q-S03C 落地的独立复核政策冲突；与 P02 无关，未改其业务建模以免掩盖政策本身。

## 4. 未验证 / 遗留

- 同一脚本再跑一次（库已重建但进程/页缓存已热）得到 **PG16 预检 6.96s / 执行 27.23s**、**PG17 预检 4.93s / 执行 10.21s**——差异全部来自预热，不是代码路径分叉；这也是"200 家 30 秒级"这个量级的可靠口径（冷 28s、热 10-27s，单进程串行）。
- 规模采样与一次针对性的去 N+1：预检原按企业逐户查 3 次（已开期 / 上期账套 / 权限），200 家约 600 次往返；现改为两条集合查询（`_period_books` / `_prior_books`，按 400 个 id 分块），逐户只剩一次授权查询。
  实测仅从 7.45s 降到 6.99s——说明瓶颈不是这两条 SQL，而是**每家企业一次、且刻意不缓存的授权查询**（`access.company` 一条语句 5 个 join，约 35ms/家）。这是一处设计取舍（`access.py` 明确「没有进程级/请求级授权缓存」），不应该为了预检速度去加缓存；因此保留逐户授权，阈值只用于发现数量级回退。若未来上限提到 1000 家，真正要改的是把「授权集合」做成一次集合查询，并同步补越权矩阵测试。
- 预检与执行都已在 PG16/PG17 上实证（`tmp_test/p02_pg_preview.py`、`tmp_test/p02_pg_execute.py`、`tmp_test/p02_pg_scale.py`、`tmp_test/p02_pg_concurrent.py`、`tmp_test/p02_pg_tenancy.py`，一次性空库 `p02_gate`/`p02_exec`/`p02_scale`/`p02_conc`/`p02_ten`，跑完即弃）。200 家耗时已采样（冷 28s / 热 10s 量级）；双操作员并发重叠区间也测过：互不阻塞、同一企业只落一条账套、无死锁。仍缺的是**高争用压测**（比如 10 个操作员同时打同一批 200 家）。跨事务所同库的相互影响已用两家事务所各 8 家并发验证：账套按所分账、跨所提交被拒、RLS 反向读取为 0。
- 预检是"点时刻快照"：预检到执行之间若范围变化（客户新增/账套被他人开启），执行仍以服务端重新计算为准，逐户结果会显示实际跳过/失败——这是刻意设计，未加"预检指纹"到执行请求（执行本身已幂等且逐户可解释）。
- 单批上限 200 家：达到上限时预检会追加"请缩小范围分批处理"的提示（`_preview_notes` 有单测），但**没有分页/断点续批**，超过 200 家的客户只能靠搜索词拆分。
- 批量作业仍只有"月度开期"一种；批量复核/批量导出尚未进入统一批量面板（P03 再议）。
- 预检不落审计事件：它不是决策，只有执行落 `batch_operations` 与逐户 `items`；若审计需要"看过预检"，需另加只读留痕（未做，避免把浏览写成审批）。
