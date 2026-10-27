# Q04 验收：客户移动端查看与签收月度资料包（2026-10-26）

本报告只覆盖用户下发的 Q04 工作包：让「客户联系人」能在手机上查看本月资料包内容、留下查看痕迹、完成签收。
不重建交付链路、不改动其他模块的权限判定、不新增首页入口。

- 起点：`HEAD 3fc1ab6e`（`git log -1`）；工作区另有其他智能体的未提交后端文件，本次未触碰。
- 结论：**本地可重复候选通过**；PostgreSQL 16/17 专项门禁与远端 CI 未执行（见 §7）。
- 与 Q03 的关系：Q03 只做「账簿连续查询」；Q04 只做「客户侧查看与签收」。两者共用 `delivery` 模块，但改动文件不重叠。

## 1. 契约（客户能做什么、不能做什么）

1. **只有当前客户联系人能看、能开、能签**：企业管理员若没有该账套授权，连账套都读不到（404）；事务所复核人能读账套但不是本版联系人，打开即 403。
2. **查看不等于签收**：客户打开资料包写入 `monthly_delivery_opens`（一包一行，幂等），只证明「看到过这一版」，回执仍在 `monthly_delivery_acknowledgements`。
3. **签收的四道硬条件**：本人是当前联系人、本人不是生成人、提交的 `manifest_sha256` 与本版一致、说明至少 3 字；缺一条即拒绝，绕过服务直接写库由数据库触发器再拒一次。
4. **签收不扩权**：客户签收后仍是账套 `viewer`，不会获得编辑或复核能力。
5. **旧版本仍可查看与签收**：新版本产生后旧版本标记为 `superseded`，但不隐藏、不删链，避免「客户刚签完旧版，新版又覆盖」的争议。
6. **失败态说清「谁来做、去哪做」**：阻断项带 `owner` 与 `entry`（例如「事务所管理员或服务会计 / 客户服务单 · 客户联系人」），不再只有一句原因。
7. **一人兼两职时不死锁**：若联系人同时拥有复核权，**生成照旧放行**，给出明确告警，并在签收边界拒绝本人自签。

## 2. 后端实现

### 2.1 迁移（新增两个文件，既有迁移零改动）

- `nexus_backend/migrations/postgres/162_monthly_delivery_opens.sql`（39 行）
  - `accounting.monthly_delivery_opens(package_id PRIMARY KEY, book_id, firm_id, company_id, opened_by, opened_at)`；
    复合外键指回 `monthly_delivery_packages(id, book_id, firm_id, company_id)`，一包最多一条查看记录。
  - `accounting.monthly_delivery_open_guard()`（`SECURITY DEFINER`）：要求写入者 = 包的 `contact_user_id` = `accounting.actor_user()`，
    且 `monthly_delivery_contact_eligible(book, opened_by)` 仍为真，否则 `ERRCODE 42501`；通过后取企业级写锁。
  - 读策略 `monthly_delivery_open_read`（本事务所 + `book_allowed`）、写策略 `monthly_delivery_open_insert`（本人 + 联系人仍有效）、
    `delivery_immutable` 触发器（只增不改不删）、`GRANT SELECT,INSERT TO accounting_app`。
- `nexus_backend/migrations/sqlite/100_monthly_delivery_opens.sql`（20 行）：同等约束的 SQLite 版本（本地测试与开发库）。
- 幂等门禁：`python scripts/check_migration_idempotency.py` → **exit 0**，新迁移均使用 `IF NOT EXISTS` / `DROP TRIGGER IF EXISTS` / `CREATE OR REPLACE`。

### 2.2 服务（`nexus_backend/app/delivery/monthly_package.py`，+167 行）

- `_can_review(conn, book, user_id)`：用与生成完全相同的成员规则（`access.book(...)["permissions"]["review"]`）判定「此人能否生成」，
  查不到授权一律按「不能」处理（fail closed）。
- `_inspect()` 新增 `warnings`：`contact_can_review` 只告警不阻断，理由写进代码注释——一人事务所仍要能出包，签收边界已拒本人自签。
- `_view(..., with_files=False)`：新增 `superseded`（是否已被更高版本取代）、`can_open`、`customer_opening_status`
  （`not_opened|opened`）、`opening{opened_at,opened_by}`；详情路径附 `files[]`（`path/size_bytes/sha256`）与 `file_count`。
- `open(actor, book_id, package_id)`：在 `write_scope="company"` 事务内重取包与联系人，幂等写入一次查看记录，
  返回带文件清单的视图；**任何情况下都不写回执**。
- `readiness()` 把 `warnings` 一并返回。

### 2.3 路由（`nexus_backend/app/delivery/router.py`，+6 行）

- `POST /api/books/{book_id}/delivery/monthly-package/{package_id}/open` → 记录查看并返回包视图。

## 3. 前端实现

- `src/monthly-package-types.ts`（+75 行）
  - 类型：`MonthlyPackageFile`、`MonthlyPackage` 新增 `superseded/can_open/customer_opening_status/opening/files/file_count`；
    `MonthlyPackageOverview` 新增 `warnings`；`MonthlyPackageBlocker` 带可选 `owner/entry`。
  - 校验：`validateBlocker`、`validateFiles`（拒绝 `..`、坏 hash、负数大小）；`opening` 必须与 `opened_by === contact_user_id` 同时出现；
    `file_count` 必须等于 `files.length`。
  - 文案：`packageOpeningLabel`（待客户查看 / 客户已查看 · 待签收 / 客户已签收）、`formatBytes`。
- `src/MonthlyPackagePanel.tsx`（+126 行）
  - 客户视角加载后自动 `POST .../open`（每包一次），并用返回的文件清单渲染「本版包含 11 个文件」；
    记录失败只吞掉留痕，不阻断查看与签收（注释说明取舍）。
  - 客户视角：签收说明、确认勾选文案改为客户语言；新增提示「签收仅表示收到资料，不代表税务机关已受理，也不代表财务软件已入账」；
    下载与签收按钮在窄屏变为主要按钮。
  - 事务所视角：阻断项显示 `处理人 / 入口`，并单独渲染「建议在交付前确认」告警区。
- `src/monthly-package.css`（+70 行）：文件清单、留痕时间、告警区样式；`<=640px` 下清单改为纵向排列。

## 4. 先写失败测试，再改实现

`nexus_backend/tests/test_q04_client_signing.py`（225 行，10 个用例，全部先红后绿）：

| 用例 | 断言的核心事实 |
| --- | --- |
| `test_readiness_explains_missing_contact_and_who_configures` | 缺联系人时阻断项给 `owner/entry`，不是只说「未配置」 |
| `test_revoked_contact_points_at_the_grant_that_is_missing` | 联系人被撤权后，阻断指向缺的那条授权 |
| `test_contact_with_review_rights_is_warned_and_cannot_self_sign` | 一人兼两职：生成放行 + 告警 + 自签 403 |
| `test_package_detail_lists_contents_month_and_receipt_states` | 详情含月份、11 个文件、查看/签收状态、`superseded=false` |
| `test_contact_opening_is_recorded_once_and_is_not_a_signature` | 重复打开只有一条记录，且不产生回执 |
| `test_only_the_configured_contact_can_open_a_package` | 管理员 404、复核人 403 |
| `test_package_of_another_company_is_not_reachable` | 跨企业详情/打开均 404 |
| `test_previous_version_stays_visible_and_signable` | 旧版本标记 `superseded` 但仍可签 |
| `test_retry_after_a_lost_response_is_idempotent` | 断网重试不产生第二份回执 |
| `test_signing_never_widens_the_clients_book_role` | 签收后仍是 `viewer` |

另有既有用例被新契约影响，已按新语义修正而不是放宽：
`tests/test_monthly_delivery_package.py::test_reviewer_who_is_also_contact_cannot_self_acknowledge` 保持原意（生成放行、自签拒绝），
我的第一版实现把它改成「生成前就阻断」，跑回归时该用例失败，**判定是我的设计越权**，于是退回「告警 + 签收边界拒绝」。

## 5. 门禁证据（本机实跑）

| 项 | 结果 |
| --- | --- |
| 后端 Q04 | `pytest tests/test_q04_client_signing.py` → **10 passed** |
| 后端回归 | `pytest tests/test_q04_client_signing.py tests/test_monthly_delivery_package.py` → **30 passed**（101.9s） |
| 迁移幂等 | `python scripts/check_migration_idempotency.py` → **exit 0**；`pytest tests/test_migration_idempotency_ledger.py` → **11 passed** |
| 静态检查 | `ruff check nexus_backend/app nexus_backend/tests scripts` → **All checks passed**；改动文件 `ruff format` 通过 |
| 类型检查 | `npx tsc -p tsconfig.app.json --noEmit` → **0 错** |
| 前端单测 | `npx vitest run` → **93 files / 944 tests passed** |
| 前端构建 | `npm run build` → **✓ built in 3.15s**（主包 605.81 kB，既有体积告警） |
| 格式 | `npm run format:check` → All matched files use Prettier code style |
| e2e 新用例 | `npx playwright test e2e/q04-client-signing.spec.ts` → **2 passed**（桌面 1280×900、手机 390×844） |
| e2e 既有用例 | `npx playwright test e2e/formal-books-monthly-package.spec.ts` → **7 passed**（含 desktop/mobile 两份） |
| 核心 e2e 集 | `E2E_CORE_ACCEPTANCE=1 npx playwright test` → **105 passed / 1 failed（6.8m）**；失败项见 §6.4，是既有竞态，与本包无关 |

既有红灯（**HEAD 上就红，非本次引入**，已用 `git worktree add .head-check HEAD` 复跑确认）：
- `tests/test_migration_structure.py::test_new_migrations_declare_compatibility_header`（`sqlite 088` 缺 compatibility 头）
- `tests/test_migration_structure.py::test_new_tables_carry_rls_force_and_write_gate`（`batch_operations` 等既有表缺 `company_write_table_guard`）

## 6. e2e 抓到的真实缺陷

1. **客户端状态语义漂移**：客户视角原本复用事务所的「待客户签收 / 确认收到 V1」文案，客户看到的是「别人要签」的语气。
   新契约改为「待客户查看 / 客户已查看 · 待签收 / 客户已签收」与「确认收到第 1 版」，既有 e2e 的三处断言随之同步（不是放宽，是同一事实的新表述）。
2. **生成响应不是联系人视图**：`POST /monthly-package` 返回的是生成人视角（`can_open=false`），若前端直接拿它渲染客户按钮会永远灰掉。
   e2e 首轮正是卡在 `frozen.can_open === true` 的断言上，改为「生成响应给生成人、联系人视图另取」后才通过。
3. **文件清单不在概览里**：`overview` 不带 `files`，客户端要点开才有内容清单；实现上由 `open` 返回，避免多一次请求。

### 6.4 核心 e2e 的那 1 个失败（既有竞态，不是本包引入）

`e2e/v09-navigation-history.spec.ts:410`（mobile「子页面…只有一个导航高亮」）在核心集里失败：

```
Error: expect(locator).toHaveCount(expected) failed
Locator: getByRole('navigation', { name: '主导航' }).locator('[aria-current="page"]')
Expected: 1  Received: 0
```

- 单独重跑 3 次：失败、失败、通过（flaky，非确定性）；失败现场的快照里侧栏只剩「打开工作空间导航」按钮，说明**抽屉在打开后又被关掉了**。
- 根因定位在 `src/AgentNavigation.tsx:64-66`：

  ```ts
  useEffect(() => {
    setMobileOpen(false);
  }, [session.scope_key, current]);
  ```

  移动端抽屉把「当前导航项解析完成」也当成「应该关闭」。进入 `/workspace/service?book=…` 时 `current` 会先为 `null` 再解析成 `monthly`，这一跳就足以把刚打开的抽屉关上；慢机器上更容易撞上。
- 证据链：Q02 的 dev 核心集里同一条用例就以同样方式失败过（当时回退我的改动仍失败）；本次改动只碰 `delivery` 模块与 `MonthlyPackagePanel`，不回写 `current` / `scope_key`。
- 建议修法（未在本包内改，避免动导航）：用 ref 记住上一次 `current`，只在该值**由用户动作改变**时关闭抽屉，或干脆只在 `onNavigate` 里关闭；并给这条用例加一次「等 `aria-current` 出现后再开抽屉」的等待。

## 7. 未覆盖与边界（不得当成已完成）

- **PostgreSQL 16/17 专项门禁未执行**：本机无 docker/psql，`scripts/ak05_postgres_gate.py` 跑不起来；162 的 RLS/策略/触发器只在 SQLite 侧被测试覆盖。
- **远端 CI 未执行**：本轮全部证据来自本机；`git push` 后的流水线结果未知。
- **未做真实通知**：查看与签收不触发邮件/短信，事务所只能通过页面状态看到。
- **没有多联系人**：一个账套一个联系人；客户换人需先改服务单再签。
- **留痕不含 IP/UA**：只记 `opened_by` 与时间，属隐私取舍；如需取证需另加。
- **移动端只验了 390×844 与 1280×900**，未覆盖平板与超长文件名。
- **未做限流**：同一客户可反复打开/关闭页面，每次都会命中一次 `GET detail`（幂等写不会重复）。
- **核心 e2e 只跑了 dev 模式**：preview（生产构建）只跑了两条定向 spec，没有跑全量核心集。
- **e2e 报告器崩溃**：核心集结束时 `scripts/e2e_reporter.mjs:87` 报 `TypeError: The "path" argument must be of type string`（未设置输出路径时 `writeFileSync(undefined)`），属既有测试脚手架缺陷，不影响用例结果，但会让 CI 里看起来像失败。

## 8. 变更清单

新增：
- `nexus_backend/migrations/postgres/162_monthly_delivery_opens.sql`
- `nexus_backend/migrations/sqlite/100_monthly_delivery_opens.sql`
- `nexus_backend/tests/test_q04_client_signing.py`
- `e2e/q04-client-signing.spec.ts`
- `docs/Q04_ACCEPTANCE_20261026.md`

修改：
- `nexus_backend/app/delivery/monthly_package.py`（+167/−?）
- `nexus_backend/app/delivery/router.py`（+6）
- `src/MonthlyPackagePanel.tsx`（+126）
- `src/monthly-package-types.ts`（+75）
- `src/monthly-package.css`（+70）
- `src/monthly-package.test.ts`（+59）
- `e2e/formal-books-monthly-package.spec.ts`（同步 3 处客户端断言）

边界声明：**本地完成 / PG 未执行 / 远端 CI 未执行 / 真实通知未接 / 生产未验证**。
