# 助手第四批：可续读的执行进度

复用前三批交付诊断、ContextPack、事实校验及持久队列；本批只增加受控读取和前端展示，不增加模型调用、任务重试、工具权限或数据库迁移。

## 实现边界

`GET /api/assistant/conversations/{conversation_id}/turns/{turn_id}/events?after=...&limit=3`

这是一条**已有任务事实的确定性投影**，最多三项：任务保存、发送标记、结束。不是新增的 append-only 事件表，也不是模型思维链、工具逐步轨迹或 token 流。未发送即取消的任务序号为 1、3，不能补造第二步。

- 每次读取先通过现有私有会话及全部引用的实时授权，再校验游标。管理员不能因此读取其他人的会话；旧游标不是授权凭证。
- 游标绑定事务所、企业、会话、任务、发起者以及已读前缀摘要；跨范围、超前或已变化的前缀返回 409。摘要用于检测不匹配，不是签名或身份认证。
- 数据只读取任务 ID、范围、状态、三项时间和受控错误码。不会读取或输出原件、历史问题、回答、快照、租约、供应商原文或费用明细。成功响应 `Cache-Control: no-store`。
- “发送标记”明确表示供应商结果尚未确认。发送后取消与超时/中断保持保守的未知说明，提示核对执行和费用，不自动重发。
- 任务成功仅代表回答已生成，不代表会计已审核、过账或导出。所有节点保持 `accounting_approved=false`。
- 前缀文案/投影版本变化时，旧游标可能失效。客户端重新读取记录，不重新执行任务。

## 前端接线

沿用助手原有可见页面轮询与 30 秒完整范围刷新。支持新接口的任务通过 `events_available` 声明能力，活跃任务每秒只读取增量进度；终态到达才立即读取完整工作区与已校验回答。其他后台动作仍沿用原进度接口。

`useAssistantTurnEvents` 负责请求去重、取消和恢复；`assistant-events.ts` 原子校验企业/会话/任务/版本、节点顺序、重复内容及终态。清空旧数据后才接受新范围；状态与游标仅存当前抽屉内存，不进入浏览器持久存储。

- 网络/5xx：保留最后已知记录和游标，明确暂时无法读取，下次只恢复读取。
- 409 或错误形状/范围/顺序：清空进度，重新读取。
- 401/403/资源 404：清空敏感工作区，停止该范围读取。
- 仅精确的路由缺失 `404 / Not Found` 才回退旧进度接口；不把资源失权当兼容降级。
- 切企业/会话、关闭或重新打开助手时取消旧读取；迟到结果不能覆盖新范围。

默认只显示最新节点，展开“查看处理过程”才展示三项时间。原有“查看执行记录”仍承担详情和人工核对，不新增首页模块、导航或财务操作。

## 离线复跑

```powershell
.\.venv\Scripts\python.exe -m tools.assistant_replay --out tmp_test/assistant-events-new-run
.\.venv\Scripts\python.exe -m pytest nexus_backend/tests/test_assistant_events.py -q
npm test -- src/assistant-events.test.ts
$env:PATH = (Join-Path (Get-Location) '.venv/Scripts') + ';' + $env:PATH
$env:E2E_FRONTEND_MODE = 'preview'
npx playwright test e2e/assistant-events.spec.ts e2e/stage25-assistant-progress.spec.ts
```

PG16/17 复用 `scripts/assistant_delivery_postgres_gate.py` 和本机隔离配置；必验清单 v3 为 38 项，在前轮 35 项上增加成功/未知记录重放、范围与撤权用例。门槛核对真实数据库版本、受限角色、持久化设置、迁移 checksum 和精确 JUnit 清单，禁止静默跳过。

本批不接真实模型、SSE、通知、线上身份或生产数据库。没有新的模型质量、精确 token 成本、规模容量或发布批准结论。验收结果见同目录的第四批验收报告。
