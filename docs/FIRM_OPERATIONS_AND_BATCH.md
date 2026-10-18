# 事务所运营、批处理与客户开通

## 异步批量任务

- `POST /api/firm/batch-operations/monthly-open` 创建持久父任务，接口只做授权、解析企业范围与入队。
- `GET /api/firm/batch-operations` 和 `/{id}` 读取父任务及逐企业子项。
- `POST /api/firm/batch-operations/{id}/retry` 只重置失败子项，不重新执行已成功客户。
- 子项由统一 Worker 的 `batch` 角色领取；每户独立事务，单户失败不影响其他企业。
- SQLite 本地验证可由 API 嵌入执行；PostgreSQL 生产必须配置统一 Worker。

## 事务所驾驶舱

`GET /api/firm/cockpit?period=YYYY-MM` 仅向事务所管理层开放，一次返回：

- 客户数、已建账、已锁期、逾期、异常和风险客户数；
- 客户 × 五步阶段、待办量、主办会计、风险分；
- 按主办会计聚合的客户数、未结任务、异常和逾期；
- 投影尚未收敛的企业数量，提示任务统计可能短暂回退实时口径。

前端入口为 `/workspace/firm`。该页面不替代会计个人待办，而是用于所长分配、催办和定位风险。

## 客户开通

- `GET /api/firm/onboarding/preview` 复用现有企业开通清单做 dry-run；
- `POST /api/firm/onboarding/runs` 保存可续跑开通记录；
- `POST /api/firm/onboarding/runs/{id}/refresh` 重新检查企业、税号、账套和配置条件；
- `POST /api/firm/onboarding/copy-templates` 从同类客户当前账套复制记账模板，重复模板跳过。

前端入口为 `/workspace/onboarding`，可新增客户档案、选择参考客户、复制模板并进入本月账套。

## 客户端极简壳

不具备事务所账套权限的客户成员自动进入独立五页签壳：
`待办 / 提交 / 资料清单 / 消息 / 本月简报`。
会计工作区、后台任务、模型成本与账套设置不出现在该壳中。服务端权限仍按每次请求重新校验。

## 异常收件箱与任务分派

- `GET /api/firm/inbox` 返回今天、逾期、阻塞、待复核、后台失败五类任务。
- `POST /api/firm/tasks/assign` 批量分派负责人和截止日期。
- `POST /api/firm/tasks/claim` 由当前会计领取。
- `POST /api/firm/tasks/remind` 写入客户消息中心，客户端在“消息”页签查看。
- 分派覆盖层保存在 `firm_task_assignments`，不修改事实表，也不改变账务权限。

## 可观测性

- 所有 API 响应返回 `X-Request-Id`，可与结构化日志和任务记录关联。
- `GET /api/firm/observability` 汇总票据、分录、迁移、批处理队列，事务/锁等待，
  Worker 心跳、未结告警、投影收敛状态和当期 AI 成本。

## 集成中心基础版

- `ingestion_connections` 统一记录企业、来源类型、状态、配置摘要、游标和一次性接入凭证。
- `ingestion_runs` 记录每次投递的接收状态、重试、游标、错误码和摘要。
- Webhook 端点：`POST /api/integrations/webhook/{token}`。
- 当前基础版先保证连接与运行可追溯；CSV/XLSX 进入现有导入向导，邮件/SFTP 适配器后续复用同一模型。

## 开通 v2

除记账模板外，开通复制还覆盖：

- 服务单主办、复核、备份与联系人分工；
- 资料模板；
- 银行账户主数据。

期初余额不会被跨企业照抄，dry-run 会明确显示“待导入确认”，避免把其他客户的余额错误带入新企业。

## 性能与回归

```bash
python scripts/benchmark_firm_scale.py --companies 100,500,1000 --repeats 5
```

合成 SQLite 基线（本机）：100 家 p95 15.4ms、500 家 p95 17.5ms、1000 家 p95 23.6ms。
PostgreSQL 环境仍须由 CI/预发布库执行全量迁移与并发回归。
