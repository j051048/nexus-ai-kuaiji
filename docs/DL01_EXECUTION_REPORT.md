# DL01 执行报告

## 结论

**DL01 离线模型调用契约验收通过；真实供应商/生产接入未验证。**

## 前置

- H01-R1：54 passed（当前工作区）；
- HEAD `59764a9b`；
- 未修改 C01、生产 provider、数据库、队列。

## 实现

`tools/ai_inference_lab/contracts.py`、`test_ai_inference_contracts.py`、`DL01_PROVIDER_CONTRACT.md`。

## 测试

```text
pytest test_ai_inference_contracts.py -q
26 passed
```

覆盖正常结构化、重复/非法 JSON、schema、拒绝/截断/工具调用、usage/价格未知与显式零、发送前/后取消、发送后超时 unknown 不重发、429/拒绝/连接失败、能力阻断、跨企业、local-only、预算、attempt。

## 复用来源

Dot Loom 固定 commit `b4a9319d51a9f17bd994a35f2950d871fb844847`：只参考 provider 抽象、取消、步骤预算思想；**未复制源码**，因此未新增其许可证文件。

## 未验证

真实 OpenAI-compatible/Ollama、流式/图像/取消、真实 usage/费用、生产企业配额；mock 成功不等于供应商联调成功。

## 门槛

允许进入 DL02；DL-R1A 后契约增强。
