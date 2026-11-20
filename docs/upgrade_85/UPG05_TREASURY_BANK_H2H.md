# Batch 05：独立出纳角色、资金支付审批流与成都银行/工商银行双行适配器

- **先读**：`docs/upgrade_85/UPG00_MASTER.md`、`docs/pilot/PLT03_ROLE_HANDOFF.md`、`docs/pilot/DEEPSEEK_FIVE_COMPANY_INTEGRATED_WORK_ORDER_20261118.md`（第 06 节）、`nexus_backend/app/accounting/bank_imports.py`、`nexus_backend/app/reconciliation/`、`nexus_backend/app/tax/variances.py`
- **前置**：Batch 02 完成
- **提分目标**：填平全仓最大业务断层——补齐独立出纳职责分离、付款审批限额控制、电子回单自动匹配与成都银行/工商银行双行可插拔适配器及沙箱演练（将维度 5 从 3.2 拉升至 8.0+，拿到两家银行真实前置机授权即可突破 8.5+）

---

## 1. 实施任务

### 1.1 独立出纳角色与资金支付内控审批引擎
- 新增双库前向迁移（SQLite + PG + `FORCE RLS` + 不可删改审计触发器）：
  - 在权限体系中正式新增 **`cashier`（出纳）** 与 **`treasury_approver`（资金审批人）** 能力，**数据库与服务层强制互斥**：付款制单人 ≠ 资金审批人 ≠ 凭证复核人，不得用普通企业财务替代出纳；
  - 建立收款账户版本表（收款人账号变更须独立审批并锁入版本号，防止付款审批后被篡改账号）；
  - 建立 `payment_batches`、`payment_orders`、`payment_approvals`、`payment_events` 表：支持企业级单笔限额与日累计限额策略（超限强制升级至集团资金主管二次审批）、幂等键防重付、完整状态机：`draft → pending_approval → approved → queued_for_bank → submitted → bank_unknown / succeeded / failed / bounced`。

### 1.2 成都银行 & 工商银行双行可插拔适配器与本地 H2H 沙箱
- 新增 `nexus_backend/app/treasury/connectors/`，定义统一抽象协议 `BankConnector`（`fetch_balance`、`fetch_transactions`、`fetch_receipts`、`submit_payment_batch`、`query_payment_status`、`export_batch_file`、`parse_statement`）；
- 分别实现 **`ICBCAdapter`（工商银行）** 与 **`BOCDAdapter`（成都银行）** 适配器（版本化字段映射、账号与法人强绑定、严格校验无外网硬编码），并配套本地双行 H2H 沙箱替身（`SandboxICBCBank` / `SandboxBOCDBank`）：
  - 模拟工行与成都银行各自报文差异、网络超时（返回 `bank_unknown`）、退票（`bounced`）、跨行延迟、重复/乱序回执；
  - **资金安全铁律**：处于 `bank_unknown` 的付款指令严禁自动重发或切渠道，必须先调用 `query_payment_status` 查回原指令终态；无真实银行授权配置时，导出功能仅允许生成带醒目水印的“受控付款核对清单（待出纳网银 U 盾复核）”，严禁宣称直连成功。

### 1.3 银行电子回单自动解析与银行流水明细行强关联（消除 `TAXI_CHAIN` B3）
- 新增多页/单页银行电子回单批量解析与自动匹配引擎 `nexus_backend/app/treasury/receipt_matcher.py`：
  - 按“本方账号 + 唯一银行流水号 + 交易日期 + 整数分金额 + 对方户名”实施确定性匹配，唯一命中时自动挂接 `bank_statement_lines` 与对应凭证；
  - 遇到同日同额多候选、缺关键字段或跨法人账号一律打入人工歧义待办队列，严禁按金额猜测；
- 新增前向迁移为 `fee_receivable_allocations` 及银行核销关联补充可选强外键 `bank_statement_line_id`，打通从凭证/应收核销直达具体银行流水行与回单原件；
- 新增按需懒加载的前端资金付款与出纳工作台组件（含付款制单、双人审批、出纳执行、回单批量上传匹配），并将 `payment_approval`、`payment_execute`、`bank_receipt_unmatched` 接入统一待办。

---

## 2. 验收命令与通过标准

```text
.venv/Scripts/python.exe -m pytest nexus_backend/tests -q -k "treasury or payment or bank or receipt"
.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_pg_sql_hygiene.py -q
npm run typecheck && npm run lint && npm test -- --run && npm run build && npm run test:frontend-budget
```

- 本批新增 ≥25 个后端测试覆盖出纳互斥 SoD、收款人变更防篡改、超限二级审批、工行与成都银行双行沙箱正常付款/超时查回不重付/退票处理、多页回单唯一匹配与同额歧义拦截，以及 1 条 Playwright E2E（1366/390）；
- 产出 `docs/pilot/BANK_H2H_HANDOFF.md`（分列成都银行与工商银行真实前置机联调所需接口文档、国密证书、白名单 IP、测试账户与双人演练脚本）；
- 更新 `docs/rework/UPGRADE_85_STATUS.md`。
