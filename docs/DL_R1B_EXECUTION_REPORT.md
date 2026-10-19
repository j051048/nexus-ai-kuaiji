# DL-R1B 执行报告：复查、比较与离线恢复

## 1. 结论

**DL-R1B 离线复查、比较与恢复工程验收通过，可以进入 DL-R1C。**

限定：合成数据、mock/冻结响应、本地文件恢复；真实模型、真实供应商、生产接入未验证。

## 2. 前置

- HEAD `59764a9b`；
- H01 replay：40 passed；
- DL01 契约：26 passed；
- DL-R1A 归档来源/可信身份/attempt/budget 回归通过；
- 未修改 C01、生产 provider、队列、权限、数据库、记账/复核/导出。

## 3. 候选哈希

```text
selective.py
A7562F52803C4A2CDD0738009BD9AADE9987C0EEA8680CE2C23A86E81C4555C9

test_ai_selective_review.py
64FCDA5B32B444196FF593BF948BA76D856968748FDE9CCFD3728685FB2C0DD6

contracts.py
E7EB9643E23B4CA5A21085AD17AFB7762D8BE80BB52F381D31BCA1892646D0BA

replay.py
9314F1411C82E38BC7856C210F976468165EFCBA3EC4D550DA3941BCDFC43774
```

## 4. 实现

- 基线/候选共用同一提取与确定性校验；
- 路由只读提取字段、证据、异常、可信 policy、预算；不读金标；
- 异常分为正常、可澄清、硬阻断；
- 复查输出先过 DL-R1A 契约/证据/严格字段标签校验，再合并；
- 合并后重新执行字段类型、日期、精度、必填、证据、金额关系；
- 保留 before/patch/after、resolved/new anomalies、review call；
- reviewed/requested/attempted/completed 分开；
- 预算耗尽明确 `BUDGET_EXHAUSTED`，仍 review_required；
- 状态：`completed/review_required/blocked/failed/unknown`；
- 本地 state 文件保存逐样本结果摘要、指纹、attempt 状态；恢复复用完成结果，unknown 不重发；
- 比较统一验证 manifest/scope/input/schema/run mode/result set，空/缺/多/重复拒绝。

## 5. B01–B12

| 编号 | 测试 |
|---|---|
| B01 | `test_b01_budget_zero_keeps_review_required_and_explicit_reason` |
| B02 | `test_b02_requested_attempted_completed_are_not_collapsed` |
| B03 | `test_b03_review_amount_mismatch_is_rechecked`（确定性校验直接验证） |
| B04 | `test_b04_wrong_label_evidence_does_not_support_gross` |
| B05 | `test_b05_identity_change_rejects_comparison` |
| B06 | `test_b06_empty_missing_duplicate_results_cannot_compare` |
| B07 | `test_b07_unknown_and_cancel_preserve_attempt_outcome` |
| B08 | `test_b08_policy_block_does_not_call` |
| B09 | `test_b09_post_review_type_date_precision_and_required_are_revalidated` |
| B10 | `test_b10_b11_resume_reuses_completed_and_rejects_changed_budget`、`test_b10_unknown_review_is_reused_without_resend` |
| B11 | 同上预算/输入/策略指纹拒绝 |
| B12 | `test_b12_duplicate_cross_company_sample_id_is_rejected`、两企业隔离测试 |

## 6. 测试

```powershell
pytest test_ai_inference_contracts.py test_ai_selective_review.py `
       test_ai_execution_trace.py test_ocr_replay_engine.py `
       test_ocr_field_evaluation.py -q
# 107 passed
```

分项：

- DL01：26
- DL02/DL-R1B：19
- DL03：5
- H01 replay：40
- OCR 字段评分：17

无 skip/xfail。

## 7. 候选结论

mock 候选：A1/A2 各追加一次复查并修复 gross，但仍 `review_required`；A3 不追加；B1 阻断。
坏复查：`regression`，票号关键退步。
无改善/失败复查：`no_improvement`/`incomplete`，不伪装 promising。

mock 耗时/调用不代表真实模型质量；mock 费用未知不填零。

## 8. 恢复边界

- B10 验证完成结果复用、unknown 不重发；
- B11 验证输入/策略/预算指纹变化拒绝；
- 这是本地文件状态，不是供应商 exactly-once，也不抗进程崩溃后的所有窗口；
- 未实现跨进程锁/数据库持久 attempt。

## 9. 未完成/未验证

- B03/B09 的“完整复查后坏字段”通过确定性校验函数验证，未构造所有 OCR 标签组合；
- 缺页/注入文本/无改善的独立夹具仅部分覆盖；
- 真实模型、真实供应商、生产权限/队列/数据库未验证；
- mock 费用/耗时来源标记为未知/模拟。

## 10. 门槛

允许进入 DL-R1C。不得据此宣称真实模型提效、生产权限验收或 C01 通过。
