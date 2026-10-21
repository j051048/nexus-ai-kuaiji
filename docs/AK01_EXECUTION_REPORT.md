# AK01 执行报告

## 结论：部分完成；只能声明本地 L 的部分证据。

## 本轮修改

### 1. journal service 限制未贯通

原代码：

```python
journal_limits = None
...
result = self.provider.suggest_journal(context, limits=journal_limits)
```

真实 service 入口完全不传 context/output/timeout 限制。

修复：

- 在同一事务内调用 `_policy_check(..., "journal", ...)`；
- 用 `_limits(evidence)` 生成有效交集；
- 传给 `suggest_journal`。

文件：

```text
nexus_backend/app/intelligence/service.py
```

说明：该修复未先写独立红测，属于“审计已复现缺陷的补充修复”；现有 provider 测试覆盖 limits，但 service 级 journal 发送计数红测未补齐，不能标记矩阵 A01 完整通过。

### 2. 旧费用上限测试与新 contract 冲突

`test_model_cost_workflow.py` 用 20,000,000 total tokens 构造 `ProviderUsage`，但 AK01 已统一上限 10,000,000，导致 Pydantic 拒绝。

已改为最高合法值：

```text
prompt 5,000,000 + completion 5,000,000 = 10,000,000
```

并同步费用期望。这是测试与显式上限契约对齐，不是放宽上限。

## 测试

```text
test_ag01_r1_limits.py
test_intelligence_provider.py
test_model_cost_workflow.py::test_highest_legal_token_counts...
```

结果：

```text
95 passed
```

基线：

```text
test_ag01_r1_limits.py
test_intelligence_provider.py
test_model_cost_response_usage.py
106 passed
```

## 未完成

- OCR/journal/chat 三入口 service 级发送次数矩阵；
- A04 标记发送后崩溃；
- A05 真双进程竞争与重启；
- A08 发送前后取消；
- A09 接纳时撤权；
- A11 双进程；
- A12 429 费用/重试资格完整矩阵；
- A13 空/坏结构；
- A14 bool/负数/小数边界；
- `test_stage7_proposal_queue` SQLite 路径阻断未复验。

## 分层

- L：部分通过；
- D：未跑真实 PG 的 AK01 并发；
- P：未验证真实供应商/生产。

## 门槛

AK01 本地门槛**未完整通过**，不能宣布 AK02 依赖集成完成。
