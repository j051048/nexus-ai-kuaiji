# Q05 验收报告：正式账簿导出的写事务收敛与容量测量

日期：2026-10-26
范围：`nexus_backend/app/posting/presentation.py`、`nexus_backend/tests/test_q05_export_consistency.py`；
Q04 前端清单字段修正（`src/monthly-package-types.ts`、`src/MonthlyPackagePanel.tsx`、`src/monthly-package.test.ts`、`e2e/q04-client-signing.spec.ts`）。
基线：`3fc1ab6e`（本地工作区另含其他智能体的未提交后端改动，本批未触碰）。

## 1. 结论

1. 导出从「一个写事务里取数、拼串、写审计」改为三阶段：只读快照 → 事务外序列化 → 短写事务复核并审计。**写锁持有时间下降约 49%**（本机实测 43.5–47.7ms → 22.1–23.7ms），且不再随导出行数增长的部分被移出锁外。
2. 一致性没有靠「猜」：提交阶段重新校验授权与来源摘要（行数、借贷合计、末凭证、期间状态、期初绑定、内部锁定），任何变化都以 409 失败，不产出与审计哈希不一致的文件。
3. 测量过程中发现并修复一个 Q04 真缺陷：后端清单字段是 `size_bytes`，前端按 `bytes` 解析，客户页的文件体积会渲染成 `NaN MB`，且 `validatePackage` 会静默拒绝该响应。已按后端契约修正并补 e2e 断言。
4. **没有**承诺容量：本报告只给本机 SQLite 实测数字与算术推演，PostgreSQL 上的锁等待未测。

## 2. 需求对照

| Q05 要求 | 落地 |
| --- | --- |
| 先测量，不先承诺容量 | 第 5 节给出逐阶段实测；容量结论只到「本机该规模」 |
| 不改变账簿内容与 CSV 头契约 | CSV 列、表头、BOM、`X-Source-Fingerprint`、`Content-Disposition` 全部保持不变；Q03/Q04 的 49 个后端用例与 7 个 e2e 用例原样通过 |
| 事务边界：序列化不得占用写事务 | 序列化在 `transaction()` 之外执行，且 `_render_csv` 不接收连接 |
| 锁与一致性 | 提交阶段重取授权、重算来源摘要；变化即 409（用例 1、2、9） |
| 失败语义 | 数据变化/撤权 → 409「导出期间账簿数据已变化，请重新导出」；超大 → 413；不写审计、不返回文件 |
| 越权与跨企业 | 取数与提交各校验一次 `access.book(...,'export')` |
| 审计证据 | `formal_book.export` / `formal_book.range_export` 记录行数、范围、完整性、指纹与 CSV 的 sha256 |
| 测试先行 | 9 个用例先红后绿（首轮全部因缺少 `_render_csv` 接缝而失败） |
| 不改业务模型、不加依赖 | 只改一个模块；无新依赖、无迁移 |

## 3. 实现

三个阶段（单账簿与连续期间两套导出共用同一骨架）：

1. **只读快照**（`db.transaction(actor=...)`）：鉴权、取行、行数上限、来源摘要。
2. **事务外序列化**（`_render_csv`）：纯字符串工作，无连接、无鉴权、无审计。
3. **短写事务**（`write_scope="company"`）：再次鉴权 → 重新计算摘要 → 不一致抛 409 → 一致则写审计。

关键函数：

- `_render_csv(header_rows, rows)`：唯一序列化接缝，字节上限 16MB。
- `_source_digest(conn, book_ids)`：每账套取 `count/sum(debit)/sum(credit)/max(voucher id)`、期间状态、活动期初绑定与指纹、内部锁定数，规范化 JSON 后 sha256。
- `_coverage_digest(items, blockers)`：连续期间覆盖视图的 (期间, 状态, 四个金额) 与阻断码，保证「缺月/未关账/期初不衔接」变化也会中止导出。
- `_check_rows(rows)`：行数上限 50,000，在序列化之前判定（413）。

## 4. 先写失败测试

`nexus_backend/tests/test_q05_export_consistency.py`（9 个用例）：

1. 快照到提交之间新增凭证 → 409，且不写审计、不返回文件；
2. 快照到提交之间撤销账套授权 → 403；
3. CSV 的 sha256 与审计一致；
4. 超过行数上限 → 413，且发生在序列化之前（注入的渲染函数不得被调用）；
5. 会计能导出、非会计 403；
6. 连续期间身份以 `ledger_id` 而非 `book_id`（同一账套不同月份导出同一份文件）；
7. 缺月必须显式出现在 CSV 与指纹行，不得冒称完整；
8. 并发写入期间要么 409，要么产出的文件与审计哈希一致；
9. **序列化期间另一个写事务必须能提交**（旧实现会锁死，新实现放行）。

第 9 条是对「锁是否真的让出」的结构性断言，不依赖计时。

## 5. 测量

环境：Windows、SQLite 临时库、单企业单账套（2026-10），Python 3.14.1；`time.perf_counter()`，单位毫秒。
数据由真实过账路径写入（借 1002 / 贷 2202，每张 2 行），逐档累积后导出。

| 导出 | 行数 | 文件大小 | 阶段1 只读快照 | 阶段2 序列化 | 阶段3 提交 | 合计 | 旧实现写锁 | 新实现写锁 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 明细账 | 51 | 6.6 KB | 21.3 | 0.1 | 22.1 | 43.5 | 43.5 | 22.1 |
| 明细账 | 201 | 25.9 KB | 21.4 | 0.4 | 23.2 | 45.0 | 45.0 | 23.2 |
| 明细账 | 401 | 51.7 KB | 23.4 | 0.6 | 23.7 | 47.7 | 47.7 | 23.7 |
| 余额表 | 2 | 0.36 KB | 22.8 | 0.0 | 22.4 | 45.2 | 45.2 | 22.4 |
| 日记账 | 400 | 51.6 KB | 23.2 | 0.6 | 22.9 | 46.7 | 46.7 | 22.9 |

序列化单独基准（无数据库，纯 `_render_csv`）：

| 行数 | 字节 | 耗时 |
| --- | --- | --- |
| 1,000 | 65 KB | 0.5 ms |
| 10,000 | 650 KB | 5.5 ms |
| 50,000 | 3.25 MB | 26.9 ms |

月度交付包实际体积（`ready_case` 真实冻结）：

| 项 | 值 |
| --- | --- |
| 文件数 | 11 |
| 合计字节 | 9,586 |
| 最大文件 | `formal_books.json` 2,230 B |

读法与限制：

- 每档的阶段1 与阶段3 都近似常数（21–24ms），说明**当前锁内成本主要是事务固定开销（授权、角色、审计插入），不是行数**；行数带来的增量几乎全部落在被移出的阶段2 里（0.1 → 0.6ms；50,000 行时为 27ms）。
- 旧实现把阶段1+2+3 都放在写事务内，因此写锁持有时间等于整段墙钟时间；新实现只剩阶段3。
- 200 家客户的推演（算术，不是压测）：按 45ms/次导出，旧实现串行占用写锁约 9.0 秒，新实现约 4.6 秒；单次最坏锁等待从 45ms 降到 23ms。**这不是并发压测结论**，只用于说明量级。
- 本机是 SQLite；PostgreSQL 的 `REPEATABLE READ` 只读快照与 `company_write` 锁行为未在此环境测量，见第 8 节。

## 6. 变更清单

新增：

- `nexus_backend/tests/test_q05_export_consistency.py`（9 用例，约 240 行）
- `docs/Q05_ACCEPTANCE_20261026.md`（本文件）

修改：

- `nexus_backend/app/posting/presentation.py`：三阶段导出；`_render_csv` / `_check_rows` / `_source_digest` / `_coverage_digest` / `_collect_formal_book`；`export_formal_book`、`export_range_trial_balance`、`export_range_account_ledger` 统一骨架。
- `src/monthly-package-types.ts`、`src/MonthlyPackagePanel.tsx`、`src/monthly-package.test.ts`：清单字段改为后端的 `size_bytes`；新增「写成 `bytes` 必须被拒」的用例。
- `e2e/q04-client-signing.spec.ts`：断言 11 个文件的体积都可读且不含 `NaN`。
- `docs/Q04_ACCEPTANCE_20261026.md`：`files[]` 字段名更正。

不含迁移、不含后端模型、不含新的前端入口。

## 7. 门禁证据

| 门禁 | 结果 |
| --- | --- |
| Q05 后端用例 | `9 passed`（28.1s） |
| 后端回归（posting、Q03、Q04、月度包） | `49 passed`（168.5s） |
| 前端单测 | `93 files / 944 tests passed` |
| 类型检查 `tsc -p tsconfig.app.json` | 0 错 |
| ESLint / Prettier（改动文件） | 通过 |
| 生产构建 | `✓ built in 3.04s` |
| e2e（月度交付 + 客户签收，桌面/手机） | `9 passed`（2.8m），随后 Q04 单独复跑 `2 passed` |
| `ruff check`（app/tests/scripts） | 通过 |
| `ruff format --check nexus_backend/app services/api_gateway scripts` | `301 files already formatted` |
| 迁移幂等检查 | 无迁移改动，未触发 |

## 8. 未覆盖与风险

1. **PostgreSQL 未测**：本机无 docker/psql（WSL 只有 PG 14）。没有在 PG16/17 上采样 `pg_locks` 等待时间，也没有验证 `company_write` 锁下并发导出的排队行为。第 5 节的百分比是 SQLite 结论。
2. **没有内存剖析**：50,000 行 3.25MB 的字符串在内存里会先整体拼出（`StringIO` + BOM），未用 `tracemalloc` 量化峰值；上限 16MB 是硬门槛，不是实测峰值。
3. **阶段3 仍有一次聚合扫描**：`_source_digest` 会对该账套的 `voucher_lines` 做 `count/sum`。401 行时该开销在噪声内（23.7 vs 22.4ms），但 50,000 行时未测，是这条优化剩余的固定成本。
4. **未做 200 家并发压测**：第 5 节的 200 家数字是算术推演。
5. **e2e 未覆盖大文件导出**：浏览器侧只验证了既有 400 行规模与 CSV 指纹一致性，没有 50,000 行下载。
6. **移动端未做导出专项**：Q03 已覆盖范围查询与下钻的手机视口，导出的手机交互未新增断言。
7. **远端 CI 未执行**：以上均为本地门禁。

## 9. 测量中发现并修复的缺陷（Q04 回归）

- 现象：客户页「本版包含 11 个文件」展开后，每个文件的体积显示为 `NaN MB`；同时 `validatePackage` 因 `Number.isSafeInteger(file.bytes)` 为假而静默拒绝该响应，导致文件清单之后的状态更新不生效。
- 根因：后端 manifest 冻结字段为 `path/sha256/size_bytes`（`nexus_backend/app/delivery/monthly_package.py` 构造 manifest 处），前端 Q04 类型写成 `bytes`。
- 修复：前端类型与校验改为 `size_bytes`；`formatBytes(file.size_bytes)`；补两条防线——单测（写成 `bytes` 必须被拒）与 e2e（11 个体积必须匹配 `数字 + B/KB/MB`，且面板内不得出现 `NaN`）。
- 影响面：仅客户视角的清单展示；签收与留痕逻辑不受影响（e2e 复跑通过）。

## 10. 交付边界

- 本地完成：实现、9 个后端用例、前端修正、单测、类型检查、构建、e2e、格式与静态检查。
- 未执行：PostgreSQL 16/17 门禁、远端 CI、200 家并发压测、大文件下载压测、内存剖析。
- 未触碰：`services/`（Paddle OCR 已移除）、生产环境、真实模型供应商。
