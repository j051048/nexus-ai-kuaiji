# AG05 执行报告：企业样例检索与评测

状态：**离线只读检索/评测通过**；生产 learning/规则发布未接线；真实模型效果未验证。

实现：`tools/ai_inference_lab/examples.py`

- 同事务所、同企业、同账套；
- 稳定 vendor_id、业务类型、收付方向、会计政策筛选；
- 排除未审核、撤销、隔离、证据不完整、holdout、同源泄漏；
- 历史金额不覆盖当前事项；
- 检索结果稳定排序；
- 分母固定，unknown/missing 保留；
- 关键金额、跨企业、风险漏转人工增加即 regression，即使平均分上升；
- manifest 版本不一致拒绝比较。

测试：`nexus_backend/tests/test_ag05_examples.py`，**10 passed**。

覆盖 E01–E10。未训练模型、未发布规则、未改生产样例库。
