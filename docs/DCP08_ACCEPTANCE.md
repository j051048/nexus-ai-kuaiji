# DCP08 验收：税务协同（申报前准备、外部回执与缴款证据）

## 结论

| 部分 | 状态 | 证据 |
| --- | --- | --- |
| A 申报前清单 | 完成 | `test_dcp08_readiness.py` 6 例、`test_tax_api.py` HTTP 契约、`tax-readiness.render.test.ts` 3 例 |
| B 外部办理记录与缴款证据 | 后端+页面完成，PG/浏览器未验 | `test_dcp08_filings.py` 4 例、`tax-filings.render.test.ts` 3 例 |
| C 月度服务进度时间线 | **未做** | 依赖 DCP05 handoff 与 DCP07 通知的合并视图 |

未推送：本包含新迁移 **124（SQLite）/ 195（PG）**，必须先迁移再推送。

## A：申报前清单

`nexus_backend/app/tax/readiness.py`

- `filing_readiness(service, actor, book_id, period)`：读权限 → 无生效纳税人档案时 `supported=false` 并给出复核岗位可读的理由；否则复用 `_snapshot()`，把它自己抛出的「不支持范围」错误原样作为阻断理由，**不估算**。
- 底稿状态：无底稿 `missing`、来源指纹不一致 `stale`、草稿 `unreviewed`、已复核 `ready`；每项带底稿版本、复核标记、一致性、差异分、下一步与负责岗位。
- 契约 `GET /api/tax/readiness?book_id=&period=`；未知状态在前端被整体丢弃（`src/tax-readiness.ts`），不猜「已申报」。
- 页面：`src/TaxReadinessList.tsx` 挂在税务底稿页顶部，桌面两列、移动单列（`.tax-readiness`）。

## B：外部办理记录

- 迁移：`nexus_backend/migrations/sqlite/124_tax_filings.sql`、`.../postgres/195_tax_filings.sql`；表 `tax_filings` 只追加，SQLite 用触发器、PG 用 `tax_filings_append_only()` 拒绝 UPDATE/DELETE。
- 服务 `nexus_backend/app/tax/filings.py`：`record()` 只接受事务所复核岗位（`access.book(..., 'review')`），校验税种/状态/回执号 3–100 字/日期/非负金额，同账套同期间同税种同状态同回执重复提交返回 409；`list_for()` 客户财务可读。
- 契约：`POST /api/tax/filings`（201）、`GET /api/tax/filings`。
- 清单联动：`filing_readiness` 增加 `external_result`（none/registered）、`filing_state`（not_filed/filed/paid）、`filings` 列表，并在每个税种上挂最新一条 `filing`——登记申报不影响另一个税种的「未申报」。
- 页面 `src/TaxFilingRecords.tsx`：列表 + 登记表单（仅 owner/admin/reviewer 可见），登记成功后刷新清单；文案明确「系统不会代客户申报或缴款」。

## 生产事故复盘（2026-11-02 08:21–08:27 UTC+8）

推送 `21d8f26b..2c189d37` 后五个服务中四个 `CRASHED` 并被平台挂起，`/api/health` 持续 502。

根因：**迁移 192 从未应用到生产**。此前只把 193/194 写进生产台账（并假定 192 已在），新镜像包含 192，启动自检 `records.get(version) != migration_digest(path)` 报「PostgreSQL 迁移缺失或校验不符」后退出。

恢复：应用 192 + 补台账 → 全量比对 182 个 PG 迁移与台账（`missing=[] mismatched=[] extra=[]`）→ `redeployService` 四个服务 → 五服务 RUNNING、health 200、新路由 401（存在）。

新增前置检查（每次推送前必做）：

1. 列出代码侧全部 PG 迁移，与生产 `accounting.schema_migrations` 逐条比对 **版本+checksum+compatibility**，三者全等才推送；
2. 迁移文件头必须是 `-- compatibility: incremental`（纯新增表/列/索引/函数），否则不允许库先行；
3. 先迁移、后推送、再验 `/api/health` 与新路由 401。

## 未验证 / 未完成

1. PG 双会话未跑（WSL 集群在线但 Windows 侧连不上 25531/25532，`netsh portproxy` 为空），195 只在 SQLite 生效过；
2. 浏览器真实组件证据未做（需要复核人夹具 + 已复核底稿）；
3. 月度服务进度时间线（DCP08-C）未实现；
4. 缴款证据只存说明文字，未关联附件 sha256。
