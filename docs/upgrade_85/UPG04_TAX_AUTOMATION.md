# Batch 04：四税种全闭环底稿、进项风控与电子税务局连接器沙箱

- **先读**：`docs/upgrade_85/UPG00_MASTER.md`、`docs/pilot/DEEPSEEK_FIVE_COMPANY_INTEGRATED_WORK_ORDER_20261118.md`（第 05 节）、`nexus_backend/app/tax/policy_items.py`、`workpaper.py`、`filings.py`、`readiness.py`、`nexus_backend/app/invoices/parser.py`、`src/TaxWorkpaperPanel.tsx`、`src/TaxFilingRecords.tsx`
- **前置**：Batch 02 完成
- **提分目标**：补齐增值税、附加税、企业所得税预缴、印花税全闭环计税底稿、计提凭证、进项风控与可插拔电子税务局沙箱连接器（将维度 3 从 4.8 拉升至 8.0+，拿到真实税局授权即可无缝突破 8.5+）

---

## 1. 实施任务

### 1.1 统一税费政策口径与四税种确定性底稿引擎
- 先写失败测试区分：历史已出旧底稿保持只读复现，**新建当期正式底稿缺税目政策条目时必须 409 阻断，禁止静默回退单一 `vat_rate_bp`**；
- 新增双库前向迁移（SQLite + PG + `FORCE RLS`），支撑四税种底稿、纳税调整台账、申报单与状态流转记录，全部金额采用整数分 `BIGINT` 与 `scale_by_basis_points`（`ROUND_HALF_UP`）：
  1. **增值税（`vat`）**：区分一般纳税人与小规模纳税人；不再把合法红字发票粗暴 reject，改为显式支持**红字发票冲减、作废发票排除留痕、期初期末留抵税额结转、进项税额转出、免税销售额单独归集**；
  2. **附加税费（`surtax`）**：基于当期增值税实缴额计算城建税、教育费附加、地方教育附加，支持“六税两费”减半优惠条目（仅在政策明确批准生效期间适用，严禁自动猜测）；
  3. **企业所得税预缴（`cit_prepayment`）**：读取期间累计利润表基数 + 纳税调增/调减台账 + 弥补以前年度亏损 + 累计已预缴额，计算本期应补（退）所得税额（明确标注：年度汇算清缴另列未启用状态）；
  4. **印花税（`stamp_duty`）**：按应税合同/营业账簿台账、税目税率与幂等防重计征键计算，拦截同一合同重复计税。

### 1.2 税金计提草稿凭证与进项发票税务风控引擎
- 独立复核通过后的四税种底稿，支持一键生成平衡的“计提税金”凭证草稿（通过科目角色路由，进入既有独立复核流程，不自动过账）；
- 新增 `nexus_backend/app/tax/risk_checks.py`：在生成底稿前自动扫描并分级告警/阻断以下税务风险：① 红冲/作废进项未做转出；② 进项未标记勾选认证状态；③ 进项发票临期/跨期风险；④ 销项与进项金额/税率异常偏离；⑤ 同一税种同期间重复申报风险。高危风险未登记原因与依据前禁止进入申报准备。

### 1.3 可插拔电子税务局连接器与本地沙箱闭环
- 新增 `nexus_backend/app/tax/etax_connector.py`：定义标准协议 `ETaxConnector`（`validate_payload` / `submit_declaration` / `poll_receipt` / `fetch_input_invoices`），提供：
  1. **受控申报数据导出包**（标准 JSON/CSV，带内容 SHA-256 及醒目“未经电子税务局核定，仅作复核底稿”标识）；
  2. **本地电子税务局沙箱替身（`SandboxETaxConnector`）**：严格隔离无外网请求，覆盖申报状态机 `prepared → approved → submitted → accepted / rejected / unknown → paid / corrected`；
  3. **防重复扣税硬约束**：申报超时处于 `unknown` 状态时禁止自动重报，必须先调用 `poll_receipt` 查回原回执终态；覆盖乱序回执、重复提交幂等、更正申报与撤权拦截。

### 1.4 前端多税种策略配置与申报矩阵（按需懒加载）
- 在 `TaxWorkpaperPanel.tsx` / `TaxFilingRecords.tsx` 增补（采用按需拆分避免突破首屏体积预算）：
  1. 多税种政策条目可视化编辑器（含独立批准人展示与缺项阻断提示）；
  2. 四税种底稿标签页、进项风险预警条与“生成计提凭证草稿”入口；
  3. “法人 × 四税种 × 期间 × 办理状态”申报总览矩阵，清晰区分系统测算、人工登记、沙箱/税局回执与缴款凭证。

---

## 2. 验收命令与通过标准

```text
.venv/Scripts/python.exe -m pytest nexus_backend/tests -q -k "tax or etax or workpaper"
.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_pg_sql_hygiene.py -q
npm run typecheck && npm run lint && npm test -- --run && npm run build && npm run test:frontend-budget
```

- 本批新增 ≥25 个后端测试覆盖四税种计算边界（红字/留抵/减半/防重计征）、进项风控、计提草稿、沙箱申报状态机（含 `unknown` 防重报）及至少 1 条 Playwright E2E（1366/390）；
- 产出 `docs/pilot/ETAX_LIVE_HANDOFF.md`，列出接入真实电子税务局/税企直连服务商所需的接口规范、认证证书与客户税种核定清单；
- 更新 `docs/rework/UPGRADE_85_STATUS.md`。
