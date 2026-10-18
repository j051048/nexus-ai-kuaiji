# B03 执行报告：正式凭证、原子记账、冲销与账簿

日期：2026-10-18
基线：`a9daee5b`（B02 外部提交后的 HEAD）
性质：本机隔离原型 + SQLite/HTTP 验证；**未提交、未推送、未部署、未切换真实账本**

---

## 0. 结论

B03 的“受控记账 + 账簿基础”已在 SQLite/HTTP 层实现：冻结期初绑定、已审核草稿/手工凭证记账、连续编号、幂等回执、全额冲销、科目余额/明细/日记账/辅助查询。

**不能签收为完整 B03：**

- B01.3 与 B02 PostgreSQL 边界仍未验收。
- 未做真实 PostgreSQL 并发、RLS、故障注入。
- 红字、拆分/合并金额分配、部分更正未实现。
- 最小前端未实现。
- 期初控制/辅助勾稽仍有 B02 遗留缺口。

---

## 1. B02 前置验收表

| 前置 | 状态 | 证据/缺口 | 对 B03 的影响 |
|---|---|---|---|
| B01.1 金额/有效来源 | **通过（合成）** | 8 个 `test_monthly_briefing` | 可用于合成样本 |
| B01.2 原件备份/恢复 | **部分通过** | 8 类原件、合成恢复；真实对象存储未验证 | 新凭证表已随 SQLite 全库快照恢复，但真实恢复未验证 |
| B01.3 审核/DB 边界 | **未验收** | 4 条 PostgreSQL 负向测试 skip | 记账 DB 边界不能签收 |
| B01.4 外部接入 | **应用层通过** | 代码关闭、旧 URL 无副作用、HTTP 202；网关未验证 | 不影响内部记账 |
| B02 核算契约 | **部分完成** | 政策/科目/期初/绑定已有；控制/辅助勾稽未实现 | 余额表只按已录期初+分录计算 |
| B02 稳定科目 | **通过（SQLite）** | 版本、父级、末级、映射、冻结触发器 | B03 使用稳定 ID/版本 |
| B02 期初包 | **通过（SQLite）** | draft→validated→submitted→approved→frozen | B03 只绑定 frozen |
| B02 PostgreSQL 104 | **未验证** | 未运行迁移/RLS | B03 PostgreSQL 105 同样未验证 |
| B02 权限 | **合成通过** | 管理层/ledger_manager；财务拒绝 | 撤权/企业停用并发未验证 |
| 会计样本确认 | **待确认** | 开发 AI 自拟 | 不冒充会计负责人 |

**前置结论**：可做不依赖 PG/会计确认的合成原型；不得启用真实企业记账或宣布 B03 完整通过。

---

## 2. 实际完成范围

### 2.1 正式凭证

- 新增 `vouchers`、`voucher_lines`，独立于 `drafts`。
- 保存企业/账套/期间、类型/编号、日期、来源、审核政策、期初绑定、内容指纹、执行主体、冲销关系。
- 分录保存稳定科目 ID/版本、代码/名称/类别/方向快照、借贷分、辅助 JSON。
- 正式凭证/分录 append-only；SQLite 触发器拒绝 UPDATE/DELETE。

### 2.2 三条状态轴

- 草稿审核：`drafts.status` 原样保留。
- 内部记账：`vouchers.status=posted`。
- 外部交付：`exports`/回执不变；`exported` 不被改写为 `posted`。
- 导出不触发记账；记账不触发导出。

### 2.3 期初绑定

- `opening_bindings` 绑定 B02 frozen 期初版本、账套/期间、指纹。
- 一账套/期间一个 active 绑定；同版本幂等，不同版本拒绝。
- 记账读取绑定版本；指纹变化拒绝。

### 2.4 统一命令

- `post_draft`：已审核/已导出草稿，映射到稳定科目，校验来源金额。
- `post_manual`：手工转账/计提等非银行凭证。
- `reverse_voucher`：全额反向分录冲销，新增凭证，原凭证不变。
- 事务内重新校验权限、期间、绑定、科目版本、辅助、平衡、来源/草稿版本、幂等、编号。

### 2.5 编号/幂等

- `voucher_sequences(book_id,voucher_type,next_no)`，事务内分配。
- `posting_commands(firm_id,book_id,command_key)` 持久化回执。
- 同键同内容返回原凭证；同键不同内容 409；草稿重复记账 409。
- 回滚不消耗号码；失败后首张成功凭证仍为 1。

### 2.6 账簿

- 科目余额表：期初 + 正式分录，父子汇总，叶子合计。
- 科目明细账：期初、逐笔、余额。
- 现金/银行日记账：1001/1002 前缀。
- 辅助明细：辅助 JSON 过滤。
- 凭证详情/关系链。
- 无余额缓存；按需计算，清缓存不改变结果。

### 2.7 接口

- `POST /api/posting/opening-bindings`
- `POST /api/books/{book_id}/drafts/{draft_id}/post`
- `POST /api/posting/manual`
- `POST /api/posting/vouchers/{id}/reverse`
- `GET /api/books/{book_id}/vouchers`
- `GET /api/posting/vouchers/{id}`
- `GET /api/books/{book_id}/posting-commands/{key}`
- `GET /api/books/{book_id}/trial-balance`
- `GET /api/books/{book_id}/ledger/{account_id}`
- `GET /api/books/{book_id}/daily-journal`
- `GET /api/books/{book_id}/aux-ledger`

---

## 3. 明确未实施

| 项 | 状态 |
|---|---|
| 红字负数金额 | **未实现**（当前只允许单侧正数金额） |
| 拆分/合并、多来源金额分配 | **未实现** |
| 部分更正/可更正上限 | **未实现** |
| 跨期更正开放期间策略 | **未实现**（仅当前 open 期间） |
| 本年累计分栏 | **未实现** |
| 数据库侧分页/权限过滤 | **未实现**（服务层权限 + Python 过滤） |
| 真实 PostgreSQL 105/104 迁移、RLS、写门 | **未验证** |
| 并发发号/撤权/锁期竞争 | **未验证**（SQLite 全局写锁 + 合成测试） |
| 故障注入/响应丢失/进程崩溃 | **仅幂等重放测试，未做注入** |
| 恢复后编号/关系/余额一致性 | **仅验证新表随快照恢复，未做业务数据恢复** |
| 最小前端 | **未实现**（只有 API） |
| 完整 UI/助手接入 | **本批不实施** |
| 通知/外发任务 | **本批不实施** |

---

## 4. 事务、编号与并发策略

- 记账使用全局写事务；SQLite 串行化。
- 编号、凭证头、分录、来源、命令回执、审计同事务提交。
- 任何校验失败在分配编号前或事务回滚中退出；不留下半张凭证。
- `voucher_sequences` 无普通 sequence，不预占、不删号、不改历史。
- PostgreSQL 未使用 `SELECT ... FOR UPDATE` 的行级发号验证；当前依赖全局锁/事务，**不能宣称并发安全**。
- 没有在事务内调用模型、下载附件或访问外部网络。

---

## 5. 测试命令与结果

```powershell
$env:PYTEST_PLUGINS='tmp_test.pytest_plugin'
.\.venv\Scripts\python.exe -m pytest nexus_backend/tests/test_posting_service.py `
  nexus_backend/tests/test_posting_api.py `
  nexus_backend/tests/test_opening_service.py `
  nexus_backend/tests/test_opening_api.py `
  nexus_backend/tests/test_stage10_ledgers.py -q
```

结果：**22 passed**（B03 6 + B02 6 + 旧长期账套 10）。

B01 回归：

```powershell
python -m pytest nexus_backend/tests/test_monthly_briefing.py `
  nexus_backend/tests/test_ingestion_service.py `
  nexus_backend/tests/test_stage10_backup_recovery.py `
  nexus_backend/tests/test_restore_hold.py `
  nexus_backend/tests/test_accounting.py `
  nexus_backend/tests/test_review_boundary_postgres.py -q
```

结果：**72 passed, 4 skipped**（skip 为 PostgreSQL 边界）。

**合计：94 passed, 4 skipped**（B01 72 + B02 6 + B03 6 + 旧长期账套 10）。

覆盖：

- 冻结期初绑定；指纹校验。
- 草稿正式记账；不改变 `drafts.status`；不产生 `exports`。
- 同键同内容重放；同键不同内容 409；同草稿换键重复 409。
- 手工凭证；连续编号；失败不占号。
- 全额冲销；原凭证保留；重复冲销 409；凭证/分录不可改。
- 余额表逐分正确；明细账/日记账余额。
- 未审核、关闭期间、无 review 权限拒绝。
- 备份恢复后新表存在（仅结构，不是业务数据恢复）。

未运行：

- PostgreSQL 迁移 105/104、RLS、真实受限角色。
- 并发发号、并发冲销、锁期/撤权竞争。
- 故障注入、响应丢失、进程崩溃。
- 前端构建/UI。
- 真实对象存储恢复。

---

## 6. 数据库迁移

- `nexus_backend/migrations/postgres/105_b03_posting_core.sql`
- `nexus_backend/migrations/sqlite/080_b03_posting_core.sql`

包含：

- `opening_bindings`
- `vouchers` / `voucher_lines`
- `voucher_sequences`
- `posting_commands`
- `voucher_sources`
- `voucher_relations`
- 不可变触发器 / RLS / 写门

**PostgreSQL 105 未执行**；SQLite 080 在测试库执行通过。

兼容/回退：

- 新表增量；旧 `drafts/sources/exports` 不改。
- 回退应用可忽略新表；已生成正式凭证不参与旧流程。
- 回退不删除历史；真实回退未验证。

---

## 7. 权限与审计

- 记账/绑定/查询使用现有 book 读/复核权限。
- 非授权 finance 记账被拒绝。
- 跨企业/跨账套：服务按 book 作用域校验；未做专门 HTTP 跨租户负向测试。
- 审计事件：期初绑定、凭证记账、冲销、失败由服务层记录；未单独实现失败事件表。
- 合规声明：哈希/审计不能宣称防止所有管理员篡改；触发器只保护正式凭证/分录。

---

## 8. 会计样本确认状态

| 样本 | 状态 |
|---|---|
| 期初启用（银行/应付） | 合成通过；待会计确认 |
| 已审核费用草稿记账 | 合成通过；待确认 |
| 手工计提/转账 | 合成通过；待确认 |
| 全额反向冲销 | 合成通过；待确认 |
| 红字冲销 | **未实现** |
| 拆分/合并/部分分配 | **未实现** |
| 跨期更正 | **未实现** |
| 折旧/摊销/工资/存货 | **本批不做完整引擎** |

开发 AI 自拟样本，不冒充会计负责人确认。

---

## 9. B04 可依赖接口

- `opening_bindings`：冻结期初绑定与指纹。
- `vouchers` / `voucher_lines`：不可变正式凭证与分录。
- `voucher_sequences`：按 book+type 的连续编号。
- `posting_commands`：幂等回执/响应恢复。
- `voucher_relations`：冲销/调整关系。
- `trial_balance` / `account_ledger` / `daily_journal` / `aux_ledger`：统一期初+分录口径。
- `book` 权限与审计事件。

B04 仍需自行实现：完整月结、损益结转、报表、跨年、缓存/汇总和真实 PostgreSQL 验证。

---

## 10. 过程与 Git 状态

- B02 代码由外部进程提交至 `a9daee5b`；本代理未主动提交，也未回退。
- B03 代码/文档当前为未提交工作区改动；未推送、未部署。
- 未访问生产数据库/存储/凭据，未调用模型/邮件/外部财务系统/支付。
- 真实企业账本归属未改变；`exported` 未回填 `posted`。

---

## 11. 完成状态分类

| 项 | 分类 |
|---|---|
| 正式凭证/分录/冲销/编号/幂等 | **已实现，SQLite/HTTP 验证** |
| 期初冻结绑定 | **已实现，SQLite/HTTP 验证** |
| 余额表/明细/日记账/辅助 | **已实现，SQLite 验证** |
| 不可变触发器 | **已实现，SQLite 验证** |
| 旧流程兼容 | **B01/B02 回归通过** |
| PostgreSQL 105/104 | **未验证** |
| 并发/故障注入 | **未验证** |
| 红字/拆分合并/部分更正 | **未实现** |
| 最小 UI | **未实现** |
| 会计确认 | **待确认** |
| 真实账本切换 | **本批不做** |

**结论**：B03 是受控记账与账簿的技术原型，不是完整 B03 验收，更不是客户权威账本或生产切换。
