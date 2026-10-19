# AG01 执行报告：调用、费用与 attempt

## 状态

本地离线工程验收：**部分通过**。  
本地适配验证：通过（production provider + MockTransport）。  
生产接线：未完成。  
真实模型效果：未验证。

## 红—绿

问题：`choices` 缺失/空/多项时，先结构失败，丢失合法 usage。

失败测试：

```powershell
.\.venv\Scripts\python.exe -m pytest nexus_backend/tests/test_intelligence_provider.py -q
```

失败断言：`caught.value.usage is not None`；结果 4 failed。

修复：
- 先解析外层合法 usage，再判断 choices；
- `choices` 结构失败通过统一 `failure()` 携带 usage；
- `ProviderUsage` 增加 100,000,000 上界，超界视为未知、需人工核定。

通过：

```powershell
pytest test_intelligence_provider.py test_model_cost_response_usage.py -q
# 90 passed
```

覆盖：
- choices 缺失/空/多项保留 usage；
- 显式零 usage 为已知；
- 负数/布尔/小数/部分字段拒绝；
- 超大 usage 拒绝且不记为零；
- 既有拒绝输出保留 usage、成本 workflow 测试不回归。

## 未完成

- A04 输入/输出/时限只校验了现有配置，未把端点 context/output/timeout 贯通到请求构造；
- A05–A09 的生产 attempt/撤权/他企/限流分层仍主要依赖既有测试，未新增全部专项用例；
- PG 并发/租约未验证。

未联网、未调用真实模型、未改迁移。
