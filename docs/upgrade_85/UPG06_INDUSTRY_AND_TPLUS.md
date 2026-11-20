# Batch 06：出租车全链断点清零、五行业专用处理器与 PLT05 完整收口

- **先读**：`docs/upgrade_85/UPG00_MASTER.md`、`docs/pilot/TAXI_CHAIN.md`、`docs/rework/RWK06_PLT_REMAINING.md`（RWK06-B）、`docs/pilot/DEEPSEEK_FIVE_COMPANY_INTEGRATED_WORK_ORDER_20261118.md`（第 03/04/07 节）、`nexus_backend/app/fsc/`、`nexus_backend/app/integrations/`
- **前置**：Batch 02 + Batch 05 完成
- **提分目标**：彻底消除出租车业务链 B1~B8 断点，为 5 家企业（含医院/物业/贸易）补齐行业专用草稿处理器并收口外部正式账 T+ 往返（推动维度 4 突破 8.8，拉升维度 1 至 8.1+）

---

## 1. 实施任务

### 1.1 清零 `TAXI_CHAIN.md` 遗留断点（B1 / B2 / B5 / B7 / B8）
- **B1**：在 `TaxFeeBatchPanel` 为已通过独立复核的规费批次新增受控“正式记账”入口，直接走 `PostingService.post_draft`（不削弱独立复核与锁期门槛）；
- **B2**：消除 `voucher_lines.line_no` 与 `draft_line_sources.ordinal` 在拆行时的错配隐患（新增双库前向迁移绑定确定性来源行键），实现报表下钻至凭证行后一键追溯到 `tax_source_files` 原始行；
- **B5**：取消 `ProfitDrilldownService` 1000 条静默截断，改为标准分页（`cursor / limit / total_count / has_more`）并附带全量汇总校核额；
- **B7 & B8**：为来源行提供可授权分享的 URL 深链，新增只读统一链路视图 API 与抽屉展示，一线串联 `批次 → 应收 → 银行明细核销 → 正式凭证 → 报表快照`。

### 1.2 补齐其余 3 家行业专用台账处理器（基于 `fsc_import_envelopes`）
每家实现“最核心主链正例 + 1 个高频阻断异常 + 幂等确定性可复核草稿”：
- **物业与食材集采（`property_grocery`）**：支持预收物业费按期摊销与食材收货/退货/采购价差草稿生成；
- **贸易公司（`trading_inventory`）**：支持购销合同、出入库单与发票三单核对，按企业已批准的存货计价方法（移动加权/先进先出；未批准则 409 阻断）生成主营业务成本结转草稿；
- **内部医院（`internal_hospital`）**：仅接收已批准的科室/期间汇总聚合台账；**建立强制隐私红线拦截器**：一旦检测到患者姓名、身份证号、病历号、就诊卡号或个人医保明细，立即返回 422 整包拒收，且绝不落盘、不写入日志、不进入快照；
- **实施与上手体验降负**：优化新公司导入初始化向导，将 `processor_code`、规则指纹等工程契约字段收进折叠“技术详情”，主界面使用财务业务术语，并展示映射预览与精准报错行号。

### 1.3 完成 PLT05 全量收口（RWK06-B）
- 修正 `PLT05-B` 返回差异处置中“忽略”选项的合规问题：任何处置（resolved / ignored）均强制要求选择结构化原因代码 + 附证据引用，且所有已处置差异永远保留在可审计历史清单中可查；
- 按 `docs/pilot/PLT05_EXTERNAL_ROUNDTRIP.md` 原文收口 `PLT05-C / D / E`：仅允许对确认失败且当前仍有权限的凭证重走预览补导，状态未知禁止重发，跨期/错法人严格拦截，集团汇总与月结预检接入外部正式账三态提示。

---

## 2. 验收命令与通过标准

```text
.venv/Scripts/python.exe -m pytest nexus_backend/tests -q -k "plt04 or plt05 or fsc or taxi or drilldown"
.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_pg_sql_hygiene.py -q
npm run typecheck && npm run lint && npm test -- --run && npm run build && npm run test:frontend-budget
```

- 5 家合成企业（出租车/广告/物业/贸易/医院）均具备“导入→校验→草稿→独立复核→过账→来源回查”与阻断异常用例（含医院含患者身份信息 422 拒收且 0 残留断言）；
- 更新 `docs/pilot/PLT_STATUS.md`、`docs/rework/RWK_STATUS.md` 与 `docs/rework/UPGRADE_85_STATUS.md`。
