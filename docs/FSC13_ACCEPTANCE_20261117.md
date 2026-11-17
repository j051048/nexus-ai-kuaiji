# FSC13 结转权威与防重复——本地验收记录

日期：2026-11-17
状态：本地工程能力与本地验收通过；外部客户/T+ 未验证。
归属：FSC13 结转权威、外部证据核对、内部结转模式门禁由本任务新增；未覆盖其他任务修改。

## 1. 范围与边界

FSC13 要求区分 `internal`、`tplus_voucher`、`hybrid` 三种结转权威模式；外部证据只用于核对，不生成凭证、不贡献利润表余额；同一逻辑来源只能产生一份正式结转贡献；权威模式变化必须使旧草稿复核/记账失效。

本轮没有：

- 生成外部结转凭证或调用 T+；
- 自动记账、自动关账、自动申报；
- 把外部证据金额补入利润表；
- 删除或改写历史结转；
- 修改已应用迁移。

## 2. 实现

### 2.1 外部结转证据

迁移：

- `nexus_backend/migrations/sqlite/194_fsc13_closing_authority.sql`
- `nexus_backend/migrations/postgres/279_fsc13_closing_authority.sql`

表 `closing_external_evidence`：

- 企业/账套/期间、权威模式、profile、来源系统/对象/编号/版本；
- 内容指纹、金额（分）、状态 `matched|conflict|evidence_only|needs_review`、详情；
- 唯一键 `(book_id, source_system, source_object, source_id, source_version)`；
- PG RLS `company_visible` + FORCE RLS + GRANT。

服务 `app/periodic/authority.py::ClosingAuthorityService`：

- `_authority` 使用最新 confirmed `tax_pilot_profiles`；无 profile 时返回 `internal` 且 `confirmed=False`。
- `bind`：review 权限；未确认权威 409；同键同指纹幂等；同键异内容 409；
  - `internal`：内部已 posted → `conflict`，否则 `evidence_only`；
  - `tplus_voucher`：`matched`；
  - `hybrid`：`needs_review`；
  - 写 `closing.external_evidence` 审计。
- `summary`：权威、内部结转 run、证据、状态计数、`double_count_blocked`、下一步。

### 2.2 内部结转门禁

`app/periodic/service.py`：

- 结转 fingerprint 纳入权威 `confirmed/mode/profile_id/profile_fingerprint`；
- `_validate_closing_source` 检测模式变化并 409；
- confirmed 且模式为 `tplus_voucher` / `hybrid` 时拒绝生成内部结转；
- 已 posted 内部结转不被外部证据删除或合并。

### 2.3 API / 前端

- `GET /api/periodic/closing/authority`
- `GET /api/periodic/closing/summary`
- `GET /api/periodic/closing/evidence`
- `POST /api/periodic/closing/evidence`
- `src/ClosingAuthorityPanel.tsx` 挂在税务底稿来源台账之后：显示权威模式/profile、证据表、登记表单；文案明确“外部凭证只作核对，不生成第二张结转凭证”。

## 3. 验收证据

| 项 | 命令 | 结果 |
| --- | --- | --- |
| SQLite 权威/证据 | `PYTHONPATH=..:. pytest -q tests/test_fsc13_closing_authority.py` | 5 passed / 15.25s |
| SQLite 结转回归 | `... tests/test_tax02_closing.py tests/test_fsc13_closing_authority.py` | 13 passed / 36.95s |
| 组合 + 恢复 | `... test_fsc13_closing_authority.py test_tax02_closing.py test_tax04_recovery.py` | 18 passed / 47.79s |
| PG17 | `test_fsc13_closing_authority_postgres.py` | 1 passed / 0.97s，`tmp_test/ak05-pg-runs/84f73f284e884ef8a715874acd6ad47a` |
| PG16 | 同上 | 1 passed / 0.98s，`tmp_test/ak05-pg-runs/32adfcb488134f078f248907f9607fd7` |
| 恢复 | `test_fsc13_closing_authority_and_external_evidence_survive_restore` | 1 passed / 7.26s |
| 浏览器 1280/390 | `npx playwright test e2e/fsc13-closing.spec.ts` | 2 passed / 38.2s |
| 前端类型 | `npm run typecheck` | exit 0 |

浏览器断言：

- 未确认权威时登记被拒，提示“试点配置中的结转权威模式尚未确认”；
- 确认 `internal` 后可登记，证据行显示“仅核对（内部权威）”；
- 桌面面板不超父容器，390 无横向溢出，移动端证据行卡片化。

截图：

- `tmp_test/fsc13-closing-1280.png`
- `tmp_test/fsc13-closing-390.png`

## 4. 覆盖场景

- 未确认 profile：拒绝登记，不把未知权威当 internal；
- 内部模式：证据仅核对，不生成第二张凭证；
- 外部模式：内部结转生成 409；
- 已 posted 内部结转后外部回流：证据 `conflict`，凭证数不变；
- 同键同内容幂等、异内容冲突；
- 权威模式变化：旧草稿复核 409；
- 跨事务所：外部所 404，不泄露证据；
- 恢复：profile、证据、指纹恢复后仍一致。

## 5. 未完成/未验证

- 未生成外部结转凭证，未验证真实 T+ 回执/编号；
- 未做“hybrid 优先级”自动并入，当前只标 `needs_review`；
- 未跑全量后端、全量浏览器、完整 FSC12 联合候选；
- 未验证真实客户、真实外部系统、生产身份；
- 未提交、未推送、未部署。

## 6. 候选

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source：2336 / `b8b7b4501553050508ef21c89cfb3affde1043b14269e06f090a2e45acc60c0c`
- migration：459 / `7543a5d92df4776ea4beccde8afdd4a557c29ed8e675f9da9c797e482bb0db3a`
