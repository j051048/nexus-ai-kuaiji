# TAX02 验收记录：双向损益结转与辅助项目保留

- 执行日期：2026-11-10
- 工程状态：**本地工程通过（SQLite / PG16 / PG17 / 桌面 + 手机真实浏览器）**
- 外部验收：不适用（不接真实模型、通知、T+ 或税务申报）
- 候选 HEAD：`781fd93499dcba7bb76056f0febe19e372202af2`
- source：`2062 / 2bb9f3866781d7fd0fdf34d20ad55bd94828bdf011b3623f7e7d830510edcaa0`
- migration：`351 / 298bb66c91b7393c5efb8965ec007c709315a3e48015c87c29d326c5f24ef5c0`

## 1. 缺陷

原 `generate_closing` 只处理：

- 收入贷方余额（`balance < 0` 转为借方）；
- 费用借方余额（`balance > 0` 转为贷方）。

因此：

- 收入出现借方冲减（`balance > 0`）时被遗漏；
- 费用出现贷方冲减（`balance < 0`）时被遗漏；
- `_balances_without_closing` 仅按科目汇总，辅助项目/部门维度被合并，结转分录丢失辅助；
- 源凭证后续变化时，旧草稿可能继续被审批。

## 2. 修复

`nexus_backend/app/periodic/service.py`：

- 新增 `_pl_balances_without_closing`：按 `account_id + category_snapshot + aux_json` 分组，opening 按科目版本映射分类；同科目/辅助多分类 409；零余额跳过。
- `generate_closing`：
  - 收入无论余额方向：借方余额借记、贷方余额贷记；
  - 费用无论余额方向：借方余额贷记、贷方余额借记；
  - 非损益科目不参与；
  - 分录带辅助 `aux`，写入 `periodic_draft_lines.aux_json`，posting 还原 `auxiliary_values`；
  - 计算 `source_fingerprint`；源余额变化时旧未记账 run 标记 `void`、写 `periodic.closing.stale` 审计并生成新版本；
  - 校验当前科目版本与分类，分类变化 409。
- `approve/post` 增加 `_validate_closing_source`：重新计算源余额与指纹，变化返回 409，不静默使用旧草稿。

`src/FinanceOperationsPanel.tsx`：

- “资产与期末草稿”页新增“期末结转”区域：本年利润科目选择、双向/辅助说明、生成按钮。
- 仅生成草稿，不自动复核、记账或导出。

`src/closing-command.ts`：

- `closingRequestBody(bookId, period, account)` 只发送 book/period/利润科目/版本/幂等键/原因，不发送金额；金额完全由服务端计算。
- 单测覆盖稳定幂等键、版本绑定、无金额字段。

## 3. 红测与结果

初始红测：收入 100、费用贷方冲减 10，结转后利润应为 110，原实现只结转 100。

```text
.venv/Scripts/python.exe -m pytest -q nexus_backend/tests/test_tax02_closing.py
```

**9 passed / 25.16s**，覆盖：

- 收入 100 + 费用贷方 10 → 利润 110；
- 收入借方冲减；
- 净亏损；
- 零余额 409；
- 辅助项目 30/20 保留；
- 成本科目不参与、仅成本 409；
- 科目分类改为费用后旧收入余额 409；
- 源凭证后补费用，旧草稿 void、新草稿 110、旧草稿审批 409；
- 审批 + 记账后损益科目归零、利润贷方 11000。

组合回归：

```text
.venv/Scripts/python.exe -m pytest -q \
  nexus_backend/tests/test_tax01_pilot.py \
  nexus_backend/tests/test_tax02_closing.py \
  --junitxml=tmp_test/tax-backend.xml
```

**16 passed, 1 warning in 31.47s**。

## 4. 分层验收

| 层 | 结果 | 证据 |
| --- | --- | --- |
| SQLite | passed | 9 项；`tmp_test/tax-backend.xml` |
| PG17 | passed | `tmp_test/ak05-pg-runs/bf2408e81f5e433095b68627d8bdf6e8`，2 passed / 108.34s |
| PG16 | passed | `tmp_test/ak05-pg-runs/b2a3e2481115452cb9edc92a4e80d3cc`，2 passed / 106.44s |
| 桌面浏览器 | passed | `e2e/tax02-closing.spec.ts`，1365×900，真实 GoTrue/API/页面 |
| 手机浏览器 | passed | 390×844；无横向溢出 |
| 浏览器汇总 | **2 passed / 15.4s** | `tmp_test/tax02-browser-1365.png`、`tmp_test/tax02-browser-390.png` |
| 独立复核 | 保持 | 页面显示“待独立复核”，制单人不能自审 |
| 真实 T+ / 税务申报 | 未执行 | 无外部环境；不计通过 |

PG 测试通过真实受限连接检查：收入 100、费用贷方 10、本年利润 110；辅助项目 A 保留；源后补费用后旧草稿审批 409。未用管理员直写伪造业务成功。

## 5. 构建与候选

- `npm run typecheck` exit 0。
- `TMP=tmp_test/tmp TEMP=tmp_test/tmp npm run build` exit 0。
- `node scripts/frontend_budget.mjs dist` passed：initial 565,455 B / gzip 175,163 B；home 593,019 B / gzip 185,750 B。
- 未提交、未推送、未部署；未改历史迁移 checksum。

## 6. 剩余问题

- 本批只修结转，不扩展税务计算、自动申报、跨年结转或集团合并。
- 真实 T+ 模板/凭证字映射未验证；只保存计划与去标识摘要，不调用外部。
- 未做真人/真机/屏幕阅读器验证。
- 原有失败/临时审计产物保留，不删除。
