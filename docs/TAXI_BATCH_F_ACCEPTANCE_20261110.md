# TAXI 批次 F 验收（TAX13–TAX15）

日期：2026-11-12  
状态：**本地部分通过（P0 工程与本地/PG/浏览器主链通过，真实客户模板与外部办理未验证）**

## 1. 候选

- HEAD：`781fd93499dcba7bb76056f0febe19e372202af2`
- source：2135 / `a7ce3d85d649c07d51f4fb14f544d0ae41a9cc59ff6b9516b1132ea5dc9b12c7`
- migration：373 / `38a3cc409ca8dfb9df59996b72ca4e7838e157c38e1a1d1495724cf820b13725`
- 本批次改动未提交、未推送、未部署。

## 2. 结论表

| 工作包 | 本地实现 | SQLite | PG16 | PG17 | 浏览器 | 外部验收 |
| --- | --- | --- | --- | --- | --- | --- |
| TAX13 租赁 | 合同、逐月计划、业务/收款/发票确认、收入确认、退款、改版、终止 | 2 passed | 与 F PG 测试同批 | 与 F PG 测试同批 | 1280/390 真实办理成功 | 客户合同/发票模板未提供，未验证 |
| TAX14 税费 | 版本化政策、基数、正负调整、人工依据、来源引用、独立复核 | 2 passed | 与 F PG 测试同批 | 与 F PG 测试同批 | 生成/独立复核成功 | 真实申报、缴款、回执、税局回执未接 |
| TAX15 利润 | 科目映射、已过账凭证行贡献、利润项目、凭证/来源下钻、未知不补零 | 2 passed | 与 F PG 测试同批 | 与 F PG 测试同批 | 展示、下钻成功 | T+ 结转与真实客户报表未验证 |

## 3. 实际修改

### 迁移

- `nexus_backend/migrations/sqlite/151_tax_f_leases_basis_profit.sql`
- `nexus_backend/migrations/postgres/234_tax_f_leases_basis_profit.sql`

新增 `lease_contracts`、`lease_plan_rows`、`lease_confirmations`、`lease_events`、`tax_basis_reviews`、`report_line_mappings`；PG 含 RLS。

### 后端

- `nexus_backend/app/tax/leases.py`
  - `LeaseService.create/list/get/confirm/recognize/refund/amend/terminate`。
  - 业务确认、收款、发票分别登记；预收按可用余额释放，收款超过当期计划的部分保留为预收。
  - 收入确认生成合成来源与草稿，不直接过账；合同版本、退款同期间唯一。
- `nexus_backend/app/tax/basis.py`
  - 政策版本、税率、附加、基数、正负调整、人工证据、来源引用；税额确定性整数分计算。
  - 复核校验版本、状态、制单/复核分离。
- `nexus_backend/app/reports/profit.py`
  - 持久化/版本化利润项目映射；已过账凭证行按最长科目前缀归集；收入 `credit-debit`、其余 `debit-credit`。
  - 贡献行保留凭证、行号、科目、净额、来源证据；所得税/净利润未知返回 `null`，年累计标记不完整。
- `nexus_backend/app/tax/router.py`、`nexus_backend/app/reports/router.py`、`nexus_backend/app/main.py` 完成路由与服务注册。

### 前端

- `src/TaxFPanel.tsx`、`src/tax-f.css`，挂到 `src/TaxWorkpaperPanel.tsx`。
- 租赁：登记、合同列表、计划表、业务/收款/收入确认。
- 税费：基数、税率、附加、人工依据、生成与独立复核。
- 利润：项目金额、凭证行下钻、来源证据、未知所得税/净利润、映射版本。
- 移动端 390 单列显示，无横向溢出。

## 4. 验证证据

### SQLite / HTTP

```text
cd nexus_backend
python -m pytest tests/test_tax13_leases.py tests/test_tax14_basis.py \
  tests/test_tax15_profit.py tests/test_tax_f_recovery.py \
  -q --disable-warnings --junitxml=../tmp_test/tax-f/tax-f-combined.xml
```

结果：**7 passed / 16.25s**（租赁 2、税费 2、利润 2、恢复 1）。

恢复用例：备份 → verify → restore 后租赁合同/计划/确认、税费核定、利润映射与草稿关系保留；SQLite `tax_cost_sources` 为空避免与其他批次夹具混用。

### PG16 / PG17

```text
python scripts/test_accounting_postgres_isolated.py --config tmp_test/tax-e-pg17.json \
  nexus_backend/tests/test_tax_f_postgres.py -q --disable-warnings
python scripts/test_accounting_postgres_isolated.py --config tmp_test/tax-e-pg16.json \
  nexus_backend/tests/test_tax_f_postgres.py -q --disable-warnings
```

- PG17：3 passed / 1.49s（`tmp_test/ak05-pg-runs/661897ca08cb4a7fb27ab2379efd0eee`）
- PG16：3 passed / 1.69s（`tmp_test/ak05-pg-runs/7370b47722da414d894fb87ccaf9de7f`）
- 覆盖：租赁确认/收入草稿、税费 206000、利润映射与跨企业 404。
- 修复的方言差异：`report_line_mappings.active` 是 boolean；`accounts.active` 是 integer；映射保存必须在事务提交后返回新版本。

### 真实浏览器

```text
npx playwright test --config playwright.tax-f.config.ts
```

结果：**2 passed / 21.8s**（1280、390；真实 GoTrue/API，无 route.fulfill 业务成功）。

截图：

- `tmp_test/tax-f-1280.png`
- `tmp_test/tax-f-390.png`

浏览器链路：登记租赁合同 → 业务确认 → 收款确认 → 收入草稿 → 生成税费核定 → 独立复核 → 利润项目读取 → 凭证行下钻。

### 构建

```text
npm run typecheck
TEMP=tmp_test/esbuild-tmp TMP=tmp_test/esbuild-tmp npm run build
```

- typecheck：0
- index JS：565.54 kB / gzip 175.18 kB
- CSS：99.72 kB / gzip 20.27 kB
- `TaxWorkpaperPanel`：70.55 kB / gzip 21.38 kB；CSS 10.93 kB / gzip 2.55 kB

## 5. 未完成与不得宣称

1. **租赁**：收入确认只生成草稿，未在浏览器完成独立复核/过账/导出；发票确认已存事实但无发票管理界面；合同变更后的旧审批失效未落到审批实体（`approved_version` 当前仍为 0）；未与规费批次做跨业务重复确认校验。
2. **税费**：未接真实发票、申报、缴款、官方回执；政策未与现有 `tax_profile` 强制一致；人工登记不等于税局核验；无申报表生成。
3. **利润**：T+ 结转与内部结转的重复排除尚未实现；四层“确认方式→计算逻辑→基础资料→凭证/原件/T+”只实现到凭证行/来源证据层；年累计和所得税未知即 `null`；未做 406,349.10/411,349.10 的客户原账核回；混合凭证贡献标记仍为近似。
4. **恢复**：SQLite 全库恢复通过；PG 目标表行级恢复未覆盖（当前 P01 只证明模式/基础数据）。
5. **外部**：真实客户合同/发票/审批、真实税局/ T+、真实模型/通知、远端 CI、真机均未验证。
6. 批次 F 不构成“已全面上线验收通过”；M 未启用。

## 6. 环境

- PG16/17 隔离集群 25496–25499 在测试期间使用；P01 bridge 未启动。
- 未执行提交、推送、部署、线上迁移、真实模型/通知/OA/T+ 调用。
