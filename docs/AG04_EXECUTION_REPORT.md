# AG04 执行报告：有限调查与人工接手

状态：**离线状态机通过**；生产队列/worker 接线未完成；跨进程恢复未验证。  
真实模型效果：未验证。

实现：`tools/ai_inference_lab/investigation.py`

- 步骤/调用预算，重复证据停止；
- 冲突生成差额说明，不补造分录；
- 新证据可结束；无证据增量转人工；
- 工具结果企业/账套/版本不匹配即拒绝；
- 证据中“忽略审批/立即导出”作为数据忽略；
- 发送后 unknown/撤权停止，不换 attempt 重试；
- 人工接手包只含冲突、建议角色、停止原因；
- JSON 保存/加载；未知版本拒绝。

测试：`nexus_backend/tests/test_ag04_investigation.py`，**9 passed**。

覆盖 D01–D06、D08–D09；D07 双进程恢复未实现，仅单进程 unknown 停止。
