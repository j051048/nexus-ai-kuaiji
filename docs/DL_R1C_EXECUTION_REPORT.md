# DL-R1C 执行报告：事件状态机、授权投影与离线界面闭环

## 1. 结论

**DL-R1C 离线事件、授权投影与界面闭环验收通过。**

范围：合成数据、DL-R1B mock 产物、本地静态页面；未接生产 API/模型/数据库。

## 2. 前置

- HEAD `59764a9b`；
- DL-R1A：26 passed；
- DL-R1B：19 passed；
- H01 replay：40 passed；
- 未修改 C01、生产前端/API/权限/数据库/worker/复核/导出。

## 3. 候选哈希

```text
trace.py
D36305D7C0AD554A3FB4627773C8F0171C3A807768611A804FB3A686C302DF92

viewer/index.html
688D05CE425154272F9F42B68A1E426C870794755F79F954BB30834A753A5C66

selective.py
9E17989FD83CC5414F3080E53693EF31104D6669D86BB0202D73B97E2D90B8F5

test_ai_execution_trace.py
C5B6C430594406463A8AECB46A3A8C01BB316604E864F1F5B27E3E1BDBD44262
```

## 4. 事件契约

- `Event`：schema、event_id、run/step/attempt、firm/company/ledger/book/sample/employee、input/policy 版本、seq、类型、outcome、reason、message、证据、字段差异、usage/cost、时间。
- 状态机：`run_created → extraction_started → extraction_completed → validation_completed → [review_requested → review_started → review_completed → validation_completed] → [blocked/failed/unknown] → run_finished`。
- `run_finished` 必须有明确 outcome；不得覆盖 failed/unknown。
- 同一 event_id 相同重传幂等；不同内容冲突；不同 finish 拒绝。
- seq 只在 run 命名空间内校验。

## 5. 身份与投影

- `RunContext` 冻结 run 的 firm/company/ledger/book/sample/employee/input/policy；任何事件漂移拒绝。
- 证据引用必须匹配 run 的 firm/company/sample/input。
- Principal 角色白名单：employee/company_owner/accountant/admin；未知角色失败关闭。
- 员工仅本人；企业负责人仅授权企业；管理员仍需明确 firm/company 集合；诊断单独 `can_diagnose`。
- 其他企业损坏事件不向当前用户泄漏 run_id/证据/错误详情。

## 6. C01–C12

| 编号 | 结果 |
|---|---|
| C01 无 run_created | 拒绝 |
| C02 跳步骤 | 拒绝 |
| C03 failed/unknown 后 finish 覆盖 | 拒绝 |
| C04 重复 event_id | 相同重传幂等；不同 finish 拒绝 |
| C05 身份漂移 | 拒绝 |
| C06 跨企业证据 | 拒绝，不投影 |
| C07 未知角色 | 失败关闭 |
| C08 员工/负责人 | 员工仅本人；负责人企业内 |
| C09 无诊断权限 | 投影数据无诊断字段 |
| C10 不同 run 局部 seq | 命名空间隔离 |
| C11 其他企业损坏事件 | 不泄漏 |
| C12 DL-R1B 产物 | 正常/复查/预算/unknown/退步/阻断转换并展示 |

## 7. DL-R1B 产物转换

`events_from_dl02(report, contexts)` 从实际 `selective.run()` 报告生成事件；字段前后差异、异常、预算耗尽、review 状态、unknown/blocked 保留。

浏览器场景页：

```text
viewer/demo/normal.html
viewer/demo/budget.html
viewer/demo/unknown.html
viewer/demo/bad.html
viewer/demo/blocked.html
```

## 8. 浏览器验证

Playwright chromium，file://，1280×900 与 375×800：

| 场景 | 状态 | 证据 | 外部请求 |
|---|---|---|---|
| normal | pass | pass | 0 |
| budget | pass (`budget_exhausted`) | pass | 0 |
| unknown | pass | pass | 0 |
| bad/regression | pass (invoice diff) | pass | 0 |
| blocked | pass (`POLICY_BLOCKED`) | pass | 0 |

无诊断权限、员工/企业隔离由投影测试验证；页面不发送全量跨企业数据，不提供审核/记账/导出/重试按钮。

## 9. 测试

```powershell
pytest test_ai_inference_contracts.py test_ai_selective_review.py `
       test_ai_execution_trace.py test_ocr_replay_engine.py `
       test_ocr_field_evaluation.py -q
# 115 passed
```

分项：DL01 26、DL02 19、DL03 13、H01 40、OCR 17。

## 10. 未验证/风险

- 真实登录、RLS、生产 API、生产前端导航未验证；
- 生产浏览器矩阵未验证；
- 事件证据 quote 来自 DL-R1B 保存的实际 OCR 证据引用；未做生产 OCR 复核。
- 页面为静态合成原型，不提供生产操作；
- 未验证真实模型/真实事件流；
- 普通 hash 只做完整性，不声称防篡改审计。

## 11. 门槛

DL-R1C 通过仅表示离线工具达到约定门槛；不授权真实模型调用、生产接入或自动发布，不代表 C01 完成。
