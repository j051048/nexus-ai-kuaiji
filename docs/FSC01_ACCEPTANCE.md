# FSC01 验收（2026-11-16）

## 结论

- 本地代码与流程能力：**部分通过**。
- PG16/17：**通过**（档案迁移 162/246 在独立 PG 库验证，PG17/PG16 各 1 passed）。
- 浏览器：**通过**（档案矩阵 1365/390）。
- 实际五人运营能力：**未验证**。
- 外部客户确认：**未取得**，五行业档案全部保持未确认。

## 已实现

### 1. 归属与能力基线

- `docs/FSC_OWNERSHIP.md`：HEAD、10 项未提交归属、共享文件最小增量、并行提交保护。
- `docs/FSC_CAPABILITY_MAP.md`：业务能力→服务/路由→页面→本地测试→PG→恢复。
- `docs/FSC_SERVICE_CATALOG.md`：保留 15 人原始服务目录，不删未自动化分母。

### 2. 五公司版本化档案

- 迁移 `nexus_backend/migrations/sqlite/162_fsc_company_profiles.sql`、`postgres/246_fsc_company_profiles.sql`。
- 12 栏：主体、账务权威、会计制度、期间、税务边界、来源系统、单据量、合同原件、岗位、敏感数据、部署、留存。
- 服务 `nexus_backend/app/fsc/profiles.py`：
  - 草稿版本、确认版本、fingerprint；
  - confirmed 内容不可改、不可删除；
  - 每栏确认必须有 status（confirmed/not_applicable）、owner、evidence_ref、confirmed_at；
  - 缺栏 422；未知保持未知，不能当“不适用”。
- API：`/api/fsc/profiles/matrix`、`active`、`list`、`create`、`confirm`。
- 前端 `src/FscProfilesPanel.tsx`：企业/行业/状态/版本/未确认栏目/确认时间；未登记显示“12 栏待填”。
- 离线合成清单 `docs/fsc/five_company_profiles_20261116.json`：五行业 12 栏全为 null，等待客户输入，不伪造确认。

### 3. 人效分母修复

- `approved_drafts` 以 `approved_at` 计，不因导出后 status 变化而丢样本。
- `returned_events` 按 distinct 事件；`return_actions` 另计动作次数。
- mapped/approved 使用 DISTINCT。
- `cost_unknown_count`；unknown 存在时不计算 AI 单笔已通过成本。
- 回归：3 提交、1 映射、1 导出后审批、1 事件退 3 次 → first_pass 0.6667、exception 0.3333。

### 4. 关账雷达权威预检

- 增设银行账户缺失、银行覆盖缺失、子账控制待处理阻断。
- `precheck` 返回 `state`、`check_version`、`checked_at`、`fingerprint`、`authoritative`。
- `closable` 只在 `precheck_passed` 时为 true；计数为 0 不再等于可关账。

## 测试

```bash
cd nexus_backend
python -m pytest tests/test_fsc01_profiles.py tests/test_value_baseline.py tests/test_closing_radar.py -q
# 4 passed / 11.47s

PYTHONPATH=.. python ../scripts/test_accounting_postgres_isolated.py --config ../tmp_test/tax-e-pg17.json tests/test_tax_f_postgres.py -q -k fsc_company_profile
# 1 passed / 1.00s

PYTHONPATH=.. python ../scripts/test_accounting_postgres_isolated.py --config ../tmp_test/tax-e-pg16.json tests/test_tax_f_postgres.py -q -k fsc_company_profile
# 1 passed / 1.29s
```

- `test_fsc01_profiles.py` 1 passed/6.93s：前 4 家确认、第 5 家缺税务 422、matrix/active、employee 拒绝。
- `test_value_baseline.py` 2 passed。
- `test_closing_radar.py` 1 passed。
- 浏览器：`npx playwright test e2e/tasks0712.spec.ts --grep "运行记录" --workers=1` → **2 passed/21.3s**（1365/390）。
- 截图：`tmp_test/tasks0712-20261107/screenshots/monitor-1365.png`。
- `npm run typecheck` 0；build 成功。

## 候选

- HEAD `767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source `2222 / bdfdd02a8010c92183567b9eb554fca8a59cd3747afe123c7de10da4e1399c9f`
- migration `394 / b5ea389468e7f86768607f8201507dd8606b75316f0870118ce6d870f93dd5cc`
- 未提交、未推送、未部署；P01 未按本候选重跑。

## 未完成

1. `FSC_EXECUTION_STATUS.md` / `FSC_CHECKPOINT.md` 首版（已建，持续维护）。
2. 15 人真实工时与质量基线（当前只保留目录）。
3. 客户对五行业档案的确认（全部待填，不伪造）。
4. 共享中心 5 人运营能力验证（需真实业务量/工时，不在代码验收内）。

## 明确声明

- 未提交、未推送、未部署；未接真实模型、通知、OA、HIS、T+、生产库。
- 未把“页面存在”或“合成档案”写成客户已确认。

## 2026-11-17 迁移修复

- SQLite 162 的 `FOREIGN KEY(company_id,firm_id) REFERENCES firm_clients(firm_id,company_id)` 列序在 SQLite 中形成 `company_id→firm_id` 的错误配对，导致档案无法插入。
- 未改已应用 162；新增 `173_fsc_profile_firm_client_fk.sql` 重建表为 `FOREIGN KEY(firm_id,company_id)`，保留数据、索引和确认/删除触发器。
- FSC01 测试夹具补 `conn.commit()` 以提交合成企业开户；后端 11 passed、浏览器 6 passed。
