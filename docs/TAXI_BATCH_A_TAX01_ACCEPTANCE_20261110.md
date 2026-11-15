# TAX01 验收记录：版本化试点边界与客户资料依赖

- 执行日期：2026-11-10
- 工程状态：**本地工程通过（SQLite / HTTP / PG16 / PG17 / 桌面 + 手机浏览器）**
- 外部验收：**未授权 / 缺少客户资料**（真实客户规则、真实 T+ 适配、税务申报均未验证）
- 候选 HEAD：`781fd93499dcba7bb76056f0febe19e372202af2`
- source：`2062 / 2bb9f3866781d7fd0fdf34d20ad55bd94828bdf011b3623f7e7d830510edcaa0`
- migration：`351 / 298bb66c91b7393c5efb8965ec007c709315a3e48015c87c29d326c5f24ef5c0`
- 本批修改归属：仅本任务 TAX01/TAX02 文件、迁移、测试、浏览器配置与文档；未覆盖其他智能体修改。

## 1. 已有能力与复用

- 既有税务底稿、税务档案、月度交付/签收、人工外部办理记录。
- 既有政策/主档/服务计划与 `audit_events`。
- 既有企业隔离、服务端实时授权、来源版本、独立复核后导出。
- 新增 `tax_pilot_profiles / tax_pilot_dependencies / tax_pilot_previews`，不复制业务状态机。

## 2. 实现

### 迁移

- `nexus_backend/migrations/sqlite/140_tax_pilot_boundaries.sql`
- `nexus_backend/migrations/postgres/223_tax_pilot_boundaries.sql`

三张表：

- `tax_pilot_profiles`：企业、版本、`draft|confirmed|superseded`、`authority_mode`、`config_json`、64 位 fingerprint、确认人/时间。
- `tax_pilot_dependencies`：profile 下 10 个客户资料/规则项、必填、`unresolved|confirmed|not_applicable`、负责人角色、证据引用、影响、说明。
- `tax_pilot_previews`：绑定 profile 与 fingerprint 的预览，失效时间。

PG 上启用并强制 RLS：读按 `company_visible`，写同时要求事务所负责人/管理员与公司可见；应用角色只授予 SELECT/INSERT/UPDATE，所有直接授权先 REVOKE。

### 服务与入口

- `nexus_backend/app/tax/pilot.py`
  - `AUTHORITY_MODES = internal | tplus_voucher | hybrid`。
  - 10 个依赖 code：`income_336_335`、`prior_period_residue`、`imbalance_832_19`、`rent_3550`、`insurance_formula`、`tax_base_5990`、`tax_rounding`、`tplus_template`、`source_files`、`payroll_scope`。
  - T+ 模式必须填 `tplus_version`、64 位模板摘要、凭证字；缺一即 422。
  - 依赖必须覆盖 10 项；`confirmed/not_applicable` 必须有证据引用；未知 code/状态/额外字段 422。
  - draft 更新只允许负责人/管理员，版本必须匹配；新 draft 立即失效该企业所有尚未失效的预览。
  - confirm 要求所有必填依赖已确认、权限范围已确认；旧确认版本 superseded。
  - preview 必须基于当前 confirmed 配置；validate 同时校验 profile id 与 fingerprint，失效或变化返回 409。
- `nexus_backend/app/tax/router.py`
  - `GET /api/tax/pilot/companies/{company_id}/readiness`
  - `POST .../draft`、`POST .../confirm`、`POST .../preview`、`POST .../previews/{preview_id}/validate`
- `src/TaxPilotBoundaryPanel.tsx` 接入税务页，展示 10 项依赖、状态、证据、业务权威范围与预览指纹；无确认数据时明确“未确认”，不把缺资料当作已确认。
- `main.py` 注册 `application.state.tax_pilot`。

## 3. 红测与修复证据

红测先行命令（失败阶段）：

```text
.venv/Scripts/python.exe -m pytest -q nexus_backend/tests/test_tax01_pilot.py
```

失败断言覆盖：

- 未确认配置生成预览被拒绝；
- 未决依赖确认返回 409；
- 非负责人修改/确认被拒绝；
- 跨企业读取/修改被拒绝；
- T+ 模式缺模板被 422；
- draft 更新后旧预览仍有效（修复前失败）。

修复后：

```text
.venv/Scripts/python.exe -m pytest -q \
  nexus_backend/tests/test_tax01_pilot.py \
  nexus_backend/tests/test_tax02_closing.py \
  --junitxml=tmp_test/tax-backend.xml
```

结果：**16 passed, 1 warning in 31.47s**（TAX01 7 + TAX02 9）。

## 4. 分层验收

| 层 | 结果 | 证据 |
| --- | --- | --- |
| SQLite/HTTP | passed | `tmp_test/tax-backend.xml`；TAX01 7 项 |
| PG17 | passed | `tmp_test/ak05-pg-runs/0371191696bc4dbcbc19b696aa6c4334/results.xml`，1 passed / 2.57s |
| PG16 | passed | `tmp_test/ak05-pg-runs/a087570bc5474b94950cb22fba67f6ff/results.xml`，1 passed / 1.59s |
| 桌面浏览器 | passed | `e2e/tax01-pilot.spec.ts`，1280×900，真实 GoTrue/API/页面 |
| 手机浏览器 | passed | 同上，390×844；无横向溢出 |
| 浏览器汇总 | **2 passed / 20.0s** | `tmp_test/tax01-pilot-1280.png`、`tmp_test/tax01-pilot-390.png` |
| 独立复核 | 保持 | 面板只生成本地边界与预览，不产生申报、缴款、正式凭证或送达 |
| 真实 T+ | 未执行 | 无供应商/OA/出管环境与授权 |
| 真实客户规则 | 未执行 | 10 项依赖均为合成“客户确认”，未冒充实客户签认 |

浏览器测试中的“客户确认”文本是合成夹具证据，仅证明控制可用；不代表任何真实客户已确认 336/335、832.19、3,550、保险、5,990、税额分位差或 T+ 模板。

## 5. 候选与构建

- `npm run typecheck` exit 0。
- `TMP=tmp_test/tmp TEMP=tmp_test/tmp npm run build` exit 0。
- `node scripts/frontend_budget.mjs dist` **status=passed**：initial 565,455 B / gzip 175,163 B；home 593,019 B / gzip 185,750 B。
- 未提交、未推送、未部署；未改历史 SQL 或 checksum。

## 6. 剩余问题

- 真实客户 10 项资料/规则全部未提供；默认 readiness 为“待确认”，确认门槛保持。
- 未接真实 T+ / OA / 出管 / 税务申报或缴款；`authority_mode` 只记录计划，不产生外部调用。
- 未做真实客户试用、真人可用性、真机/微信/屏幕阅读器验证。
- 本批未扩展税种计算、自动申报或税额准确性；缺失累计/所得税仍是未知，不补零。
