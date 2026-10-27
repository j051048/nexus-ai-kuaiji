# Q-R01 整合与工具门槛：验收记录

日期：2026-10-26。基线 HEAD：`3fc1ab6e11761ba36490f1858b85fcff356441cc`（两个来源候选的共同祖先，未提交任何 commit）。
候选工作区：`tmp_test/qr01-integration`（`git worktree`，`tmp*` 已在 `.gitignore`，不入库）。

## 1. 候选与来源

| 来源 | 内容 | 状态 |
| --- | --- | --- |
| A：主工作区 | Q01–Q05 五轮（迁移检查器、角色化工作台、连续期间账簿、客户移动端签收、导出三阶段）+ 27 个其他智能体的后端改动 | 未提交 |
| B：`tmp_test/v08-role-clarity` | 复核责任政策（制单≠复核、逻辑版本、PG 162–166 / SQLite 100–103） | 未提交 |
| C：本次候选 | A 的 Q01–Q05 + B 的复核政策，重编号冲突迁移，修好两个工具缺陷 | 本记录 |

整合清单（`git status --porcelain` 共 144 条，其中 80 个已跟踪文件被修改，`+3460 / −710`；64 个新增文件）：

- 后端：`app/accounting/`（复核政策、逻辑版本、责任模型、路由）、`app/posting/`（手工复核桥、三阶段导出、连续期间账簿）、`app/delivery/`（月度包查看留痕）、`app/periodic/`。
- 迁移：B 的复核政策保留 `162–166`；A 的 `162_monthly_delivery_opens.sql` 与 B 同号，重编号为 `167_monthly_delivery_opens.sql`，对应 SQLite 的 `100_monthly_delivery_opens.sql` 重编号为 `104`（B 的 SQLite 复核政策占 `100–103`）。**两侧均无与既有迁移同号**，既有迁移字节未改（`git status --porcelain nexus_backend/migrations` 只有 11 个新增文件）。
- 前端：`src/ReviewWorkbench.tsx`、`ManualVoucherReviewPanel.tsx`、`MonthPackagePanel.tsx`、`FormalBooksWorkbench.tsx`、`FormalBooksRangePanel.tsx`、`useScopedQuery.ts` 等 43 个文件；e2e 8 个、`docs` 8 个、`scripts` 7 个。

### 冲突裁决（逐条）

1. `src/FormalBooksWorkbench.tsx`：以 B 的「更多操作」入口为基，并入 Q03 的「连续期间账簿」按钮与 `FormalBooksRangePanel` 渲染分支。
2. `src/ReviewWorkbench.tsx`：整体采用 B 的后端裁决版本。A 里由「列表是否读到」推导权限的本地推导被删除——它只是 B 服务端能力的降级替代品。
3. `e2e/formal-books-monthly-package.spec.ts`：采用 B 的 11 个用例，仅把两处断言改到 Q04 的客户语言（`签收说明`、`我已核对月份与版本，并确认收到本版全部文件`、`确认收到第 1 版`），并补一条「首次查看被记录」的断言。
4. 手工凭证夹具：B 把既有用例改为 `review_policy_fixtures.post_reviewed_manual`（提交待审 → 独立复核 → 记账），保留原断言。A 的 Q03/Q05 用例原本直连 `posting.post_manual`，整合后必须走同一路径，已在 `test_q03_continuous_books.py` 的 `post()` 内改为 `post_reviewed_manual`。这是本轮唯一的测试夹具语义变化。
5. 其余文件（`src/App.tsx`、`src/WorkspaceHome.tsx`、`src/ClientPortalShell.tsx`、`nexus_backend/app/main.py`、`app/subledger/*`）属其他智能体的未提交改动，原样带入候选，未改写其内容。

## 2. 迁移检查器：先红后绿

缺陷（本轮实测）：

- **跨平台误判**：`_file_sha256()` 直接哈希原始字节。同一份迁移在 Windows（CRLF）与 Linux（LF）检出下字节不同，摘要也不同，历史台账在 Linux 上必然报 `sha256 mismatch`。实测 `153_c01_auxiliary_entity_contract.sql`：CRLF 原始哈希 `6fd1ddb4…`、LF `b6a82ff5…`，规范化后两者均为 `b6a82ff5…`。
- **解析太粗**：`DROP TRIGGER IF EXISTS t ON other` 被当成名字匹配；`"..."` 引号标识符不识别；`CREATE CONSTRAINT TRIGGER` 不识别；注释与字符串字面量里的 DDL 被计入；一次 `DROP` 可覆盖无限次同名 `CREATE`；函数体内的 `$$...$$` 动态 SQL 被当成顶层语句。

修复（`scripts/check_migration_idempotency.py`）：

- 新增 `read_sql()`：`utf-8-sig` 解码 + `\r\n → \n`，与 `app/accounting/database.py` 的 `migration_digest` 同口径；哈希与扫描都走它。
- 新增 `_mask()`：把行注释、块注释、单引号字面量、`$tag$…$tag$` 体替换为等长空格，注释里的 DDL 与函数体里的动态 SQL 不再参与判定。
- 触发器解析改为「语句范围内取 `ON` 表名 + 触发名」并支持 `CONSTRAINT`、引号标识符；`CREATE` 必须消费一次同名同表的 `DROP IF EXISTS`（多重集配对），多余 `CREATE` 逐条报违规。
- 历史台账 `scripts/migration_idempotency_legacy.json` 重算为规范化摘要，并依据新解析器更新 7 个历史文件的违规清单：清除 3 条 `accounting.%i` 假阳性（来自 `DO` 循环里的 `EXECUTE`），新增被旧解析漏掉的引号/约束触发器项，现登记 **7 个文件 / 61 条**真实历史债。

红绿证据：新增 `nexus_backend/tests/test_migration_idempotency_scanner.py` 8 例，改前 **6 failed / 2 passed**（CRLF 等价、引号标识符、约束触发器、注释内 DROP、字符串内 CREATE、一 DROP 覆盖两 CREATE）；改后连同既有台账用例（11 + 新增 4）与结构用例 7 例，迁移三件套共 **35 passed**。跨平台一致性另做实测：7 个登记文件在 A（CRLF 检出）、C（LF 检出）与台账三处哈希完全相同，其中 1 个文件原始字节确有差异。

### 历史台账的「批准集」（补充条款 2）

旧台账只有 `entries`：谁能改台账，谁就能给任意新迁移登记豁免。现在台账升为 v2：

```json
{ "version": 2,
  "approved": [{"file": "153_c01_auxiliary_entity_contract.sql", "sha256": "…", "basis": "Q01 验收记录 §3"}],
  "entries":  [{"file": "153_c01_auxiliary_entity_contract.sql", "sha256": "…", "violations": [...], "reason": "…"}] }
```

- `approved` 是**文件级白名单**：只认这 7 个历史文件，且声明摘要必须与台账条目逐字一致（`approved digest does not match`），批准文件缺条目同样报错（`approved historical file has no matching ledger entry`）。
- 非 `approved` 的文件即使写了自己的台账条目、哈希完全匹配，也报 `ledger entry is not in the approved historical set` 并失败。
- v1 台账在严格模式下直接拒绝（「ledger must declare the approved historical files」），避免回退到无批准集的旧格式。

红绿证据（`nexus_backend/tests/test_migration_idempotency_ledger.py`，新增 4 例，共 15 例）：`test_new_migration_cannot_buy_exemption_by_writing_its_own_entry`、`test_approved_digest_must_match_the_entry`、`test_approved_file_without_ledger_entry_fails`、`test_v1_ledger_cannot_be_verified_when_approvals_required`。台账实际内容：**version 2、approved 7、entries 7、violations 61**。

## 3. 候选指纹：先红后绿

缺陷：`scripts/run_e2e.mjs:sourceFingerprint()` 对 `git ls-files -c -o` 返回的每一项都 `readFileSync`。当工作区存在嵌套仓库（`.head-check` 工作树、子模块）时，git 返回的是目录本身，读取直接抛 `EISDIR`，核心浏览器门禁无法启动——这正是本轮「候选绑定」的阻断点。

修复：目录项改为绑定其 `git -C <dir> rev-parse HEAD`（无提交则 `<unborn>`），目录内容不参与哈希，避免把临时工作树当源码；删除项仍记 `<deleted>`。

红绿证据：`scripts/e2e_harness.test.mjs` 新增「嵌套仓库不得被当成文件读取」用例，改前 `EISDIR`（`readFileSync` 栈），改后 8 个用例全绿。实测真实仓库：候选 `0ef8b81a…`（540 ms）、主工作区 `1c97aed2…`（466 ms），均不再抛错。

## 4. 门禁结果（全部在候选 C 上实跑）

| 检查 | 结果 |
| --- | --- |
| 前端单测 `vitest run` | 95 文件 / **961 passed** |
| 类型检查 `tsc -p tsconfig.app.json` | 0 错 |
| 构建 `npm run build` | ✓ 3.13 s（主包 605.81 kB，>500 kB 警告为既有） |
| 核心浏览器门禁 `node scripts/run_e2e.mjs --mode dev` | **106 passed / 0 failed，PASS，0 gate issues**（4.9 min） |
| 核心浏览器门禁 `--mode preview` | **106 passed / 0 failed，PASS，0 gate issues**（4.6 min） |
| 后端复核政策 + 迁移检查器 | `test_review_policy_service`、`test_review_responsibility_database`、`test_review_transaction_identity`、`test_review_policy_closing`、`test_review_policy_ai_versions`、两个检查器用例 = **81 passed** |
| 后端记账/账簿/导出/交付回归 | `test_posting_service`、`test_q03_continuous_books`、`test_q04_client_signing`、`test_q05_export_consistency`、`test_monthly_delivery_package`、`test_formal_books_presentation` = **68 passed** |
| `scripts/check_migration_idempotency.py` | **exit 0**（7 个历史文件已登记，新增迁移无违规） |
| 迁移三件套（structure + scanner + ledger） | **35 passed**（含新增 4 例批准集用例） |
| `npm run format:check` | All matched files use Prettier code style |
| `ruff check nexus_backend/app nexus_backend/tests scripts` | All checks passed |
| `node --test scripts/e2e_harness.test.mjs` | 8 passed |

## 5. 整合中发现并修掉的两个真缺陷

1. **客户面板文案断言过期**：合并后客户视角的状态标签由 `packageReceiptLabel`（`待客户签收`）换成 `packageOpeningLabel`（`待客户查看 / 客户已查看 · 待签收`）。B 的用例仍按旧文案断言，且该文本与期间拼在同一个 `<strong>` 节点里，`exact` 全等永远匹配不到——这正是核心门禁暴露的两个 `formal-books-monthly-package` 失败。改为匹配 `本月资料包（第 N 版）` 的局部文本后 11 个用例全绿。
2. **A 的用例绕过复核**：Q03/Q05 的夹具直连 `posting.post_manual`，合并后触发「手工凭证须先提交待审版本并完成复核」409，导致 16 个用例失败。改为走 `post_reviewed_manual`（真实提交 → 独立复核 → 记账）后全绿，未放宽任何断言。

## 6. 迁移纪律：三层事实分开断言（补充条款 3、4）

「静态检查覆盖」「迁移执行成功」「启动时不重复应用」是三件事，本轮分别给证据：

| 层次 | 手段 | 本轮结论 |
| --- | --- | --- |
| 静态覆盖 | `scripts/check_migration_idempotency.py` 精确规则 + `--strict-review` | 新增迁移 0 条违规；**16 条需专案检查**（`DO $$`、`EXECUTE`、`format()` 动态 DDL） |
| 执行成功 | 真实 PG16/17 空库 `migrate` 1–167 | 两个版本均 exit 0，`status --strict` exit 0 |
| 重复应用 | 同一库再次 `migrate` | 两个版本均输出「无新版本」，不改动任何已应用迁移 |

检查器不做「正则即证明」的宣称：动态 DDL 明确输出为 `needs_review`，配合真实 PG 复放，而不是静默当作已验证安全。`review_items` 落在 `--strict-review` 的退出码里，可被 CI 升级为硬门禁。

### 结构测试逐项分类（补充条款 4）

`nexus_backend/tests/test_migration_structure.py` 从「2 failed」改为 **7 passed**，但每一项都按性质处理，没有整条 skip：

| 项 | 分类 | 处理 |
| --- | --- | --- |
| `batch_operations` 等 3 张表「缺写门」 | 扫描器误报（触发器由 `EXECUTE format` + `VALUES` 动态安装） | 新增 `nexus_backend/tests/migration_gate_scan.py` 识别动态安装；真实库 `pg_trigger` 对照确认一致 |
| `monthly_delivery_packages` 等「缺 RLS/策略」 | 同上（`FOREACH ... ARRAY[...]` 循环安装） | 同上；动态 RLS 与动态策略一并识别 |
| `088`（SQLite）、`128`/`138`（PostgreSQL）缺 compatibility 头 | 历史不可变文件的声明债 | 精确登记到 `nexus_backend/tests/migration_structure_debt.json`：逐条 `sha256` + 原因；另加反向断言——文件一旦补头，登记必须移除 |
| `104`（SQLite）、`148/149/150`（PostgreSQL）缺 compatibility 头 | 本轮新增缺陷 | 直接补 `-- compatibility: incremental`（仅注释，不改语义） |
| `subledger_control_settings` 无公司写门 | 合法形态：`FOR ALL` 策略带 `book_allowed(...)` 的 `USING`+`WITH CHECK` 租户判定 | 测试接受「写门 **或** 带租户判定的写策略」，并要求 `company_id` 表二选一，否则失败 |
| 5 张全局表无 RLS（`ai_endpoint_capabilities`、`ai_trusted_endpoints`、`recovery_release_audit`、`report_templates`、`restore_state`） | 全局目录/开关表，无租户列 | 白名单带理由，并断言它们**确实**没有 `company_id`/`firm_id`，防止把租户表塞进豁免 |

负向控制：把一份合法但「带 `company_id`、无 RLS、无写门」的 `168_negative_control.sql` 放进临时副本，扫描器立刻报出该表——测试不会因为「看不见」而静默通过。

## 7. PostgreSQL 16/17：真实空库举证（补充条款 5）

不把「默认 PATH 没有 psql / 没有 Docker」当作阻断，改用 WSL 里已存在的 16/17 二进制（`/home/flydao/.local/share/nexus-local-tests/monthly-25dba6f42e/extracted/usr/lib/postgresql/{16,17}/bin`），各自 `initdb` 到 `/tmp/qr01/pg16|pg17`，端口 25531/25532，本地 trust 认证，`fsync`/`synchronous_commit`/`full_page_writes` 全 `on`。**未连接、未借用任何生产库**。

| 项 | PG 16.15 | PG 17.11 |
| --- | --- | --- |
| 空库 `migrate` 1–167 | exit 0 | exit 0 |
| `status --strict` | exit 0 | exit 0 |
| 再次 `migrate` | 「无新版本」 | 「无新版本」 |
| 迁移条数 / 版本范围 | 155 / 1–167 | 155 / 1–167 |
| 迁移摘要（version+checksum 的 md5） | `b39289b7db7aac602a85663c2efa997b` | 同左（完全一致） |
| 复核政策 + 逻辑版本 + PG 门禁用例 | **53 passed** | **53 passed** |

说明：WSL 里的 `psql` 客户端因 `PQchangePassword` 符号缺失不可用（服务器端正常），所以用 Windows 侧 `psycopg` 连 `127.0.0.1:2553x` 执行；这是工具链限制，不影响服务器端证据。

## 8. 仍不声称的部分

- 远端 CI 未运行，本轮全部为本地门禁（含本地真实 PG16/17）。
- 未做 200 家并发压测，不报告「生产容量提升 X%」。
- 未在云上（Zeabur）与 Supabase 上复跑；Supabase 的连接池与 RLS 角色仍需单独验收。
- `ruff format --check nexus_backend/app services/api_gateway scripts` 仍是 **27 个文件待重排**（含从未改动的 `app/tax/estimate.py`、`app/reports/service.py`），是 HEAD 既有红灯；我改动的文件已格式化。`nexus_backend/tests` 不在 CI format 范围（219 个文件不符）。
- Windows 写文件会把 LF 变 CRLF；检查器已按规范化解码消除误判，但仓库仍建议统一 `.gitattributes`。
- 本轮补强中 `scripts/e2e_harness.test.mjs` 曾因手工编辑未被 Prettier 覆盖，已格式化；`npm run format:check` 现为 **All matched files use Prettier code style**。

## 9. 候选用法与下一步

- 候选在 `tmp_test/qr01-integration`，可作为 Q-R02/Q-R03 的唯一基线；三个候选的修改清单/补丁/未跟踪源码已固化在 `tmp_test/qr01-preserve/`（`manifest.json` + `tracked.patch` + `untracked/`），untracked 文件不按 Q01–Q05 机械划分归属。
- 本轮仍不提交、不推送；待提交清单 = 80 个已跟踪修改 + 64 个新增 + 4 个新增测试/台账文件（本次补强追加）。
- 进入 Q-R02 的前置已满足（PG16/17 已实证），但 Q-R02 的跨年验收要新建「12 月 + 次年 1 月」真实账套与正式期初，不能用月份字符串单测替代。
