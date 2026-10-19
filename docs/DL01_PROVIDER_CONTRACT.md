# DL01 模型调用契约（离线）

状态：工程契约已实现；真实供应商未联调。  
范围：`tools/ai_inference_lab/contracts.py`，不接入生产 provider。

## 契约

- `CallRequest`：run/attempt、企业/账套范围、样本、输入 SHA、场景/schema/策略版本、provider/model/endpoint、允许数据、token/调用/时限预算、取消令牌、所需能力、输出 schema。
- `CallResult`：状态、finish reason、结构化结果、校验状态、provider/model、是否发送/接收、usage、cost、attempt、模拟标记。
- `Failure`：发送前失败、拒绝、限流、能力不支持、响应格式错误、截断、发送前/后取消、发送后超时/断连、未知。
- `Capabilities`：结构化输出、图像输入、流式、usage 回执、取消；不得由“OpenAI 兼容”推导全部支持。

## 控制

- `MockTransport` 只读冻结响应，不打开 socket；
- 一次 `Executor.invoke` 只对应一个 attempt；
- 无隐藏重试、模型切换或云回退；
- 发送后未知保持 unknown，`re_send_safe=False`；
- usage/价格缺失为 unknown，显式 0 为 known；
- Decimal 费用；
- 企业 policy 先校验，再选择端点；
- 预算耗尽在发送前阻断。

## 未验证

真实 OpenAI 兼容/本地端点、真实流式/图像/取消、生产费用和企业配额。

## 测试

`test_ai_inference_contracts.py`：正常结构化、重复/非法 JSON、schema、拒绝/截断/工具调用、usage/cost 未知与零、发送前/后取消、超时未知、429/拒绝/连接失败、能力阻断、跨企业、local-only、预算、attempt 绑定。15 passed。
