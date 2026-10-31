# 助手第三批：完整请求预算与历史降级

面向已有会计助手与交付诊断，复用 ContextPack、事实发布器、持久任务及费用实现。没有增加第二次模型调用、自动摘要模型、工具循环或新权限。

## 处理顺序

1. 沿用 Worker 的当前企业/账套授权、事实版本、外发政策与供应商限制检查。
2. 由现有 ContextPack 编译器构建当前事实、证据、阻断项、必需规则、当前问题，以及未过期权限范围内的历史问题。历史回答仍不具有当前事实资格。
3. 新增的 `app/assistant/request_budget.py` 按真实网关的 UTF-8 估算方法，计算系统提示、schema、JSON 转义和整个用户消息的开销。支持已有 `json_schema` 与 `json_object` 两种输出模式，与实际 MockTransport 请求对拍。
4. 若超出可信输入上限或 65,536 字节的用户 ContextPack 消息上限，复用编译器裁剪较早历史问题；最近问题优先。预算搜索有界，最多 20 次纯本地探测，没有网络请求。输入上限与输出预留分开处理，不人为扩大管理员限额。
5. 当前事实、证据、金额、阻断项及必需规则全部保留。它们仍放不下时，返回 `context_too_large` 和缩小范围的下一步；**先拒绝，再预留费用或设置发送标记**。
6. 按现有流程仅调用一次模型；旧版本、撤权、结果未知、费用结算及租约围栏继续生效。
7. 服务端根据 ContextPack 的覆盖计数解释保留/省略了多少条历史问题。模型不能用自由文字声称资料完整、审核完成或已导出。

## 保持的契约

- 预算估算不是精确 tokenizer，也不是实际 usage，更不用于对客户收费。费用仍依据已有用量/预留/未知结果契约处理。
- 仅处理服务端已授权快照；不增加跨企业或跨账期检索，不推断用户未选择的范围。
- 原始快照与完整已保存历史的指纹仍绑定到 ContextPack；即便某历史问题被省略，改动它后也不能复用旧 pack。
- 不把部分资料说明成完整月结或交付结论；`can_deliver` 的原约束、独立复核、导出机制均保留。
- 省略历史提示只说明本轮编译器的裁剪量；数据库原有的历史页数/字节范围限制继续生效，不声称覆盖整段会话。
- 必需事实不能放下时不自动换模型、不提高额度、不总结掉风险。应由用户减少当前选择或重新说明问题。
- 原始无 ContextPack 的网关兼容调用仍保留原有大小限制；运行中的持久助手始终传入并重新校验 ContextPack。

## 版本与部署边界

系统提示词和 `PROMPT_VERSION` 没有改变。预算策略使用 `assistant-request-budget-v1`，纳入 `AssistantProvider.configuration_fingerprint()`，避免旧任务静默采用不同的裁剪策略。未来部署时，旧配置指纹的待处理任务可能进入现有失效处理流程，需要按新范围重新提问；不自动重发已发送请求。

离线候选编号更新为 `assistant-context-budget-v3`，原六类任务输入、金标与评分标准不变。历史验收报告和产物不覆盖。本轮无数据库迁移，无前端组件或导航变更；提示通过已有助手回复展示。

## 复跑

```powershell
.\.venv\Scripts\python.exe -m tools.assistant_replay --out tmp_test/assistant-budget-new-run --timeout 600

.\.venv\Scripts\python.exe -m pytest nexus_backend/tests/test_assistant_context_budget.py -q
```

PG 门禁复用上一批入口，必验清单已版本化到 v2（35 项：原 33 项 + 两项真实持久历史/费用边界用例）。需要两套按既有 AK05 契约准备好的本机隔离库；不能使用线上连接。

```powershell
.\.venv\Scripts\python.exe scripts/assistant_delivery_postgres_gate.py `
  --pg16 .codex/assistant-delivery-pg-20261030/pg16.json `
  --pg17 .codex/assistant-delivery-pg-20261030/pg17.json `
  --out tmp_test/assistant-budget-pg-new-run
```

每次使用新的输出目录，不覆盖旧候选证据。结论见 [2026-10-31 验收记录](ASSISTANT_CONTEXT_BUDGET_ACCEPTANCE_20261031.md)。本轮只做本地 Mock/FakeProvider 验收，没有真实模型质量、浏览器、远端 CI 或生产运行结论。
