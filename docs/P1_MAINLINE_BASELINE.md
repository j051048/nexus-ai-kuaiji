# P1 主线基线（合成小规模客户）

## 已有能力

仓库已有完整“收票 → OCR/建议 → 草稿 → 独立复核 → 导出”的 HTTP 与服务层测试，不需要新建页面。

本轮复跑：

```text
test_positive_ocr_proposal_apply_independent_review_and_export_is_exact_and_idempotent
test_http_end_to_end_synthetic_ocr_apply_review_export_and_private_original
2 passed
```

覆盖：

- 员工提交、部门审批、财务核验、会计移交；
- OCR 合成票据；
- 生成建议并应用草稿；
- 未审核导出 409；
- 会计独立审核；
- 审核后导出 CSV，金额/科目正确；
- 重复导出幂等；
- 私有原件读取。

## 本轮结论

这条链路可作为 P1 的**第一段基线**：

```text
收票 → 识别 → 草稿 → 复核 → 导出
```

## 已有但尚未串联

月结能力已存在，单独复跑：

```text
test_stage10_closing.py::test_empty_book_close_has_immutable_snapshot_and_explicit_coverage
1 passed
```

覆盖不可变快照、报告门槛、关闭和重放。

新增同客户串联测试：

```text
test_same_customer_invoice_export_delivery_and_close
1 passed
```

它把同一合成客户的：

```text
票据 → OCR/建议 → 导出 → delivery 回执
→ 月结 preflight → 快照复核 → close → 重放
```

真正串起来。

修复前：

```text
counts = {"model_uncertain": 2}
report.passed = false
```

原因是合成 provider 没有报告 usage，且 meter 未配置价格。  
本轮测试显式提供：

- `ProviderUsage(3,4,7)`；
- OCR/proposal 合成价格。

修复后：

```text
report.passed = true
status = closed
replayed = true
```

这是同一合成客户的完整链路，不是两个独立夹具拼接。

## 说明

- 这是合成 usage 和合成价格，不是真实供应商账单；
- 没有调用真实模型；
- 月结后凭证仍保持审核后导出；
- 未验证真实客户/真实税期。

HTTP 路径也补了可观察门禁：

```text
test_http_same_customer_export_receipt_reaches_close_gate
1 passed
```

但 HTTP fixture 使用 seeded demo 公司，预置 `background_pending`、`unmatched_bank`、`unreviewed` 会阻塞关闭。  
因此：

- HTTP 导出/回执/预检路径通过；
- HTTP 真正关闭未在该 seeded fixture 完成；
- 干净同客户关闭由服务层测试覆盖。

## 缺口

仍缺：

- 同一客户上的银行流水对账；
- 发票导出 → 月结 → 报表的串联；
- 报表交付确认；
- 真实客户样本；
- 真实小规模纳税人税务口径。

因此不能写成“完整月结一条龙通过”。

## 下一步

1. 在同一合成客户上补银行对账 → 月结 → 报表；
2. 记录每一步页面/API、人工介入点；
3. 再复制一般纳税人分支；
4. 最后才做老板看板和导航减法。
