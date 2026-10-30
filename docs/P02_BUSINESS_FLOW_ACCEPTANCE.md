# P02 业务全链路验收（本地真实 API + PostgreSQL 16）

- 日期：2026-10-29
- 结论：**部分完成**（不得进入 P03 集成验收）
- 候选：工作区 HEAD `8a21224d`，未提交；本轮只新增测试 `nexus_backend/tests/test_p02_business_flow_postgres.py` 与本文档，未改产品代码。

## 1. 基线、归属与 P01 前置核验

| 项目 | 事实 |
| --- | --- |
| HEAD | `8a21224d test: add recovery inventory coverage and baseline docs` |
| 工作区 | 进入本轮时 `git status --porcelain` 为空 |
| 本轮新增 | `nexus_backend/tests/test_p02_business_flow_postgres.py`、`docs/P02_BUSINESS_FLOW_ACCEPTANCE.md` |
| 他人修改 | 未覆盖、未 reset、未整体格式化 |

**P01 前置未通过**：工作令要求先读 `docs/P01_RECOVERY_ACCEPTANCE.md`，该文件**不存在**；仅有 `docs/P01_RECOVERY_COVERAGE_20261029.md`，其结论是「恢复清单覆盖缺口已修，PG16/17 联合恢复未执行（环境缺口），结论＝部分完成」。

因此本轮 P02 **不能**把 P01 旧报告当作当前候选的通过证据，只能做与恢复无关的独立部分，并在结论上标注阻断。差异已如实列出：P01 交付名不符、PG 联合恢复缺证据。

## 2. 环境与替换边界

- PostgreSQL：WSL2 内 `postgresql-16`（16.15，端口 25531），一次性库 `p02_chain`，迁移 001–178 全量应用，运行账号 `p02_app_pg16` / `p02_wrk_pg16`（受限角色，`status --strict` 通过）。
- 应用：真实 `create_app()`（FastAPI），`database.is_postgres=True`。
- 替换边界（工作令允许）：身份供应商 → 本地 stub 验签（`AuthSettings(mode="supabase")` + `SimpleNamespace(authenticate=...)`），模型 → 固定夹具 `FakeProvider`。
- 未替换：数据库、权限、版本、金额、幂等、审计、复核规则全部为真实实现。
- 未执行：PG17、真实浏览器、真实 PaddleOCR 图片识别、真实通知渠道。

## 3. 角色与实际路径（已跑通部分）

执行方式：`pytest nexus_backend/tests/test_p02_business_flow_postgres.py -q` → **1 passed，6.23s**。

| 步骤 | 角色 | 入口 | 结果 |
| --- | --- | --- | --- |
| 员工上传原件并提交事项 | employee | `POST /api/business/events`、`/attachments`、`/submit` | 通过 |
| 部门审批 | department | `POST /api/business/events/{id}/department-approve` | 通过 |
| 企业财务核验 | finance | `POST /api/business/events/{id}/finance-approve` | 通过 |
| OCR 识别与异常核对 | finance | `POST /api/intake/.../recognize`、`/api/intelligence/...` | 通过（模型为夹具） |
| 交接并生成草稿 | accountant | `POST /api/business/events/{id}/handoff` | 通过 |
| 分录建议 | accountant | `POST /api/books/{book}/drafts/{id}/ai-proposals` | 通过 |
| 独立复核 | manager | `POST /api/drafts/{id}/approve` | 通过（制单人≠复核人由服务端强制） |
| 正式过账 | accountant | `POST /api/posting/manual`-同源 `post` 路径 | 通过 |
| 账簿与试算 | accountant | `GET /api/books/{book}/trial-balance` | 通过（借贷合计相等、期初+发生=期末） |
| 导出批次 | accountant | `POST /api/books/{book}/exports` | 通过（导出后草稿 `status=exported`） |
| 外部导入回执 | accountant | `POST /api/books/{book}/delivery/exports/{id}/receipts` | 通过 |
| 税务底稿 | accountant → manager | `POST /api/tax/workpapers`、`/review` | 通过（底稿 409 校验差异说明；`filing` 恒 `not_filed`） |
| 报表生成/校验/复核 | accountant → manager | `POST /api/reports`、`/validate`、`/review` | 通过 |
| 关期预检（资料齐全性） | accountant | `POST /api/books/{book}/closing/preflights` | 通过（`report.passed=true`） |
| 月结预检（B04） | manager | `POST /api/b04-close/preflight` | **阻断**：`subledger_control`、`bank_reconciliation` |
| 报表发布 | manager | `POST /api/reports/{id}/publish` | 未达（需内部锁期） |
| 交付包/客户打开/签收 | accountant → employee | `/delivery/monthly-package`、`/open`、`/acknowledge` | 未达（依赖已发布报表） |

## 4. 缺陷、先红后绿证据与最小修复

本轮**未改产品代码**。测试期出现的失败全部是测试自身契约理解错误，已按真实契约修正（不是放宽断言）：

1. **回执条目形状**：`delivery_receipts.body_json.items[].lines[].ordinal` 必须与清单一致（清单为 **0 基**），`amount` 必须等于借方合计、`description/date` 必须与清单逐字段相等，且 `auxiliary_values` 必须是对象。测试初版用 1 基序号与固定金额 → 关期预检报 `delivery_pending`。修正测试后通过。
2. **报表接口返回嵌套**：`create/validate/review/publish` 均返回 `{"report": {...}, "content": {...}}`，版本在 `report.version`。测试初版直接取顶层 → `KeyError`。已按契约修正。
3. **关期与月结是两个门**：`/closing/*` 只检查资料齐全性；`/b04-close/*` 才做内部锁期，且要求报表已复核、银行对账与子账控制。测试初版试图用 `/closing/close` 满足报表发布前置 → 409「期间未内部锁期」。已改为真实顺序。

**发现的产品缺陷（本轮未修，建议 P02 修复项）**：

- **F1（中）阻断项 detail 为空**：`/api/b04-close/preflight` 返回 `status=preflight_failed` 时，`checks[]` 中 `subledger_control` 的 `detail` 为 `[]`，`bank_reconciliation` 只给计数。会计在界面上看不到「哪个科目、哪个账户、缺什么」，违反验收标准「每处阻断都有谁处理、改什么、下一步」。
- **F2（低）状态码语义**：预检未通过仍返回 **201**（资源已创建），前端只能读 `close.status` 判断；与 `/closing/preflights` 的 201+`report.passed` 风格不一致，容易误判成功。
- **F3（低）草稿导出顺序未提示**：过账不改变草稿状态，关期要求 `drafts.status='exported'`，而「过账成功」提示不会告诉用户还需导出+回执。这是真实业务链上最容易漏的一步（本轮即因此多花数轮）。

## 5. 实际边界

| 维度 | 实际执行 |
| --- | --- |
| PostgreSQL | 16.15 单版本；17 未跑 → **不满足**工作令「PG16/17 均跑」 |
| 浏览器 | 未执行真实浏览器全链路 → **不满足**工作令第 3/4 层 |
| 存储 | 未接对象存储，附件为本地文件系统 |
| 身份 | 本地 stub 验签，**不等于** Supabase 生产身份恢复验证 |
| 模型 | 固定夹具，非真实 OCR/LLM；不得据此宣称识别质量 |
| 通知 | 未接真实通道，站内 sink 之外未验证 |

## 6. 复现命令

```bash
# WSL 内启动两版集群（16.15 / 17.11）
wsl -u root -- bash -lc 'pg_lsclusters'

# 迁移 + 建受限角色（凭据在 tmp_test/p02-pg.local.json，已 gitignore）
./.venv/Scripts/python.exe tmp_test/p02_pg_provision.py --major 16

# 目标测试
ACCOUNTING_TEST_POSTGRES_URL='postgresql://postgres:***@127.0.0.1:25531/p02_chain' \
ACCOUNTING_TEST_DATABASE_URL='postgresql://p02_app_pg16:***@127.0.0.1:25531/p02_chain' \
ACCOUNTING_TEST_WORKER_DATABASE_URL='postgresql://p02_wrk_pg16:***@127.0.0.1:25531/p02_chain' \
./.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_p02_business_flow_postgres.py -q -p no:cacheprovider
```

## 7. 指纹

- 候选源码：`git rev-parse HEAD` = `8a21224d`（本轮未提交，工作区含新增测试与本文档）
- 迁移：001–178（PG16 `status --strict` 通过）
- 关键夹具：员工/财务/部门/会计/复核/客户联系人 6 个独立身份；账套期间 `2026-10`；单据金额 268.10（借方合计）

## 8. 剩余阻断与能否进入 P03

**不能进入 P03。** 缺口按优先级：

1. **P0**：B04 月结门槛（子账控制 + 银行对账）未打通 → 报表发布、交付包、客户签收全链路未验证。
2. **P0**：PG17 未跑；浏览器全链路未跑。
3. **P1**：F1/F2/F3 三个可用性缺陷未修。
4. **P1**：P01 恢复验收报告缺失（`docs/P01_RECOVERY_ACCEPTANCE.md`），恢复后重跑本链路的 B06 场景未做。

下一轮最小路径：给测试账套的往来科目配置子账控制并生成期初未结项，补一笔银行流水覆盖期初余额 → B04 预检通过 → 锁期 → 报表发布 → 交付包 → 客户签收；随后在 PG17 与真实浏览器复跑同一用例。
