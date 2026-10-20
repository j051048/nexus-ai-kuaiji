# AG01-R1 执行报告

## 1. 结论

**AG01-R1 部分完成；不具备进入 AG02 离线集成的完整门槛。**

已完成：
- 可信 `ExecutionLimits` 与 provider 请求构造；
- OCR/journal/chat 输出限制与有效超时；
- 429 合法 usage 解析；
- retryable 不再释放费用；
- provider/meter 用量上限统一为 10,000,000。

未完成：
- 端点限制的 service 端完整接线（journal 路径保留原模板分支，未强制传 limits）；
- L01/L02/L03 的生产 service 级发送次数/请求断言，目前主要是 production provider + MockTransport；
- T01–T12 三路径完整 attempt 矩阵与跨进程恢复；
- 既有 `test_stage7_proposal_queue` 两个模板测试出现 `sqlite3.OperationalError: unable to open database file`，未作为本轮通过证据。

真实模型：未验证。生产接线：未上线。

## 2. 基线

- HEAD：`5c48399a`
- 修改文件哈希：

```text
intelligence/provider.py 71F2ACA6E4FDE7AD37F58FD8BB7065E318DA893FB498F05E4E879C925E291663
intelligence/models.py 6D2991D8541CC0D52E609E9C81EE867D1FDDDDFDBB70D65F39D003EF91FD0680
intelligence/endpoints.py 33A3CE0C9A6297B76AF79DDC7E103F48C7E615A1AFBA1B8F202A7C5C9F895640
intelligence/service.py 200C9397645BFF9AB3442EB314389CFBF61211EA6DE9E5A9EECBA2C13746B5E0
operations/meter.py 119B17CABA87109962DE0BB1C629F2E085B4717208E15F230EF5E990E2396CB7
assistant/provider.py BBB5E98775E9A435FB5474FB5C0282DF9C63EF8772227F9AE5C48013BD6099CE
assistant/worker.py 8A1891DF61B6E2762FDB2A12235BAD58E168612BD4A6DAE261859CF76EE406F4
```

## 3. 红—绿

### L01–L04/L06

测试：`nexus_backend/tests/test_ag01_r1_limits.py`

- context_limit=1 时 OCR 发送次数 0；
- OCR/journal/chat 均带 `max_tokens=1234`；
- transport timeout ≤ 1 秒；
- 请求不能提高 output 上限；
- 截止时间耗尽不发送。

通过：**8 passed**。

### F01–F04

测试：`test_ag01_r1_limits.py`、`test_model_cost_response_usage.py`

修复前：429 + retryable 被 `MeterContext.finish` 释放，状态 `released`。

失败断言：

```text
assert 'released' == 'uncertain'
assert 'released' == 'estimated'
```

修复：`safe_rejection` 只接受显式本地未发送证据，删除 `retryable` 推导。

通过：
- 429 有 usage → `estimated`；
- 429 全零 usage → `estimated`；
- 429 无 usage → `uncertain`，不归零；
- retryable 有 usage 不释放。

### U02/U03

`ProviderUsage` 上限统一为 10,000,000：
- 10,000,000 接受；
- 10,000,001 拒绝，usage=None；
- 100,000,000/100,000,001 拒绝。

## 4. 组合回归

```text
test_intelligence_provider.py
test_ag01_r1_limits.py
test_model_cost_response_usage.py
106 passed, 1 warning
```

```text
test_stage15_assistant_provider.py
test_stage15_assistant_meter.py
test_stage15_conversations.py
43 passed, 1 warning
```

## 5. attempt 覆盖

已有并复跑的保护：
- 发送后 unknown、重建 worker 不自动重发（assistant meter）；
- 发送后崩溃转人工（proposal queue 其他测试）；
- 取消后不采纳结果；
- 撤权保留费用。

未完成：
- OCR/journal/chat 三条路径各自的 T01–T12 完整矩阵；
- 真正的双进程恢复；
- 两个竞争领取者的当前候选专项；
- 端点限制的 service 级发送前测试。

## 6. 阻断

1. 代码未完成：journal service 端点 limits 未完整贯通；assistant 仅传 check_send limits，未覆盖全部恢复分支。
2. 测试未完成：L01/L02/L03 的 service 级发送次数和 transport 观察；T 矩阵。
3. 环境/既有测试：`test_stage7_proposal_queue` 两个模板测试在 `finish_rule` 处出现 SQLite `unable to open database file`，与本轮 provider/meter 断言无关，但阻止“全量回归全绿”声明。
4. 数据库/生产接线：PG 并发、租约、真实供应商未验证。

## 7. 门槛判断

**不满足**“AG01-R1 本地离线门槛通过，可进入 AG02 离线集成”。

最小解除条件：
- 完成 service 级 L01–L03 断言；
- 三路径 T 矩阵及双进程恢复；
- 解决或隔离上述模板测试 SQLite 失败；
- 复跑企业隔离、撤权、审核后导出。

## 8. 边界声明

未联网、未调用真实模型、未启动生产 API/worker、未执行迁移、未修改 C01/UX、未提交/推送/部署。
