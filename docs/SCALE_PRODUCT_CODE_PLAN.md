# 千级客户产品的代码改造蓝图

目标：把当前“专家可用的记账工作台”演进为可承载几百至上千家客户的事务所数据中心。
原则是保留整数分记账、证据链、复核导出和租户隔离，优先减少运营复杂度与重复代码。

## 已落地（2026-10-17）

- 投影刷新已从 API 进程拆出，使用独立 `maintenance_worker` 与系统能力数据库账号；
- 新增持久 `batch_operations` 父任务/子项、`batch` Worker 角色和异步月初批量开期；
- 新增 `/workspace/firm` 事务所驾驶舱与 `/api/firm/cockpit` 只读聚合；
- 新增 `/workspace/onboarding` 客户开通、可续跑记录和同类客户记账模板复制；
- 客户成员自动进入独立极简壳，不再共享会计工作区导航。

第二批继续落地：

- 驾驶舱增加任务分派、领取、批量催办和截止日期覆盖层；
- 新增异常收件箱五泳道与客户端消息中心；
- 开通 v2 复制服务单分工、资料模板和银行账户，期初余额继续要求独立导入确认；
- API 统一返回 `X-Request-Id`，新增事务所队列、锁等待、Worker、投影、告警和 AI 成本视图；
- 新增可观测数据接入中心、Webhook 接入凭证与运行记录；
- 增加 100/500/1000 家合成客户驾驶舱性能脚本和扩展 smoke 集。

## 审计复核增补（2026-10-17）

Gemini 对前几轮提交的复核中，核心生产风险基本成立；以下项目优先于新功能开发。

| 复核项 | 结论 | 归入 |
| --- | --- | --- |
| 网关模式把普通传输上限 3 MiB 误用于两段式直传，前端会提前拦截 3–10 MB 文件 | 成立，P0 | 第 8、9 点 |
| 同源 `PUT /api/uploads/{id}/content` 未携带 `X-Firm-Id`，多事务所切换后上传会 404 | 成立，P0 | 第 8、9 点 |
| `ProjectionRefresher._sweep` 漏掉从未建过投影的活跃企业，且先 drain 后 sweep 会立即重入 | 成立，P0/P1 | 第 1 点 |
| 发票挂账草稿没有绑定唯一交易对方辅助核算 | 成立，P1 | 第 4、7 点 |
| `BankImportService.distinguish` 和旧账清偿 confirm/reverse 仍是全局写锁 | 成立，P1 | 第 1、8 点 |
| recurring/settlement/briefing/integration/catalog/learning/ledger/migration 仍会退出常驻账套壳 | 成立，P1 | 第 9 点 |
| `BusinessWorkspace` 仍有旧版内联表单，`IntakeWizard` 仍有三个底部动作 | 成立，P2 | 第 5、9 点 |

轻微事实修正：简报字段实际名为 `briefings`；`App.tsx` 当前约 2,587 行而非 2,617 行；
工具栏回调实际名为 `onWorkspace`。这些差异不改变上述架构判断。

P0 收尾必须拆成独立小提交：

1. `TransportLimits` 增加独立的 `direct_upload_max_bytes`。普通 multipart/JSON 的
   `max_file_bytes` 继续由网关 3 MiB 限制；超过 2 MiB 的两段式直传按业务 domainLimit
   （当前 10 MB）和 `direct_upload_max_bytes` 的较小值校验，不能直接把 50 MiB 当成可上传业务文件。
2. `upload-client.ts` 的同源 PUT 分支同时发送 `Authorization` 与 `X-Firm-Id`，补多事务所切换测试。
3. `ProjectionRefresher._sweep` 对齐 `pending_companies` 的 `LEFT JOIN` 语义并纳入从未构建企业；
   `_loop` 改为先 sweep 再 drain。最终仍应迁到独立 Worker lease job。
4. 挂账草稿通过 `LedgerService.resolve_in_transaction` 解析唯一往来方，再经
   `CatalogService.save_auxiliaries_in_transaction` 写入；名称歧义或未匹配时不猜测，保留阻断说明。
5. 三个锁迁移先做单账套/单企业写门与 PG 回归；旧账迁移不能因模块名而整块保持全局锁。

## 1. 统一任务与批处理底座

复用现有 `jobs/service.py`、`jobs/worker.py`、`unified_worker.py` 和租约模型，不另造
一套并行任务系统。

- 新增企业级批处理父任务：建议表 `batch_operations` 与 `batch_operation_items`，字段至少包含
  `firm_id/kind/status/idempotency_key/request_json/total/succeeded/failed/created_by`，
  子项包含 `company_id/book_id/status/attempt/error_code`。
- 每个子项进入现有任务队列；父任务只汇总进度，不在一个事务内逐家写库。
- 把批量开期、批量分配、批量催办、批量交付、旧账迁移入口统一成“预览 → 创建父任务 → 单户执行 →
  汇总失败项 → 单户重试”。
- `ProjectionRefresher` 从 API 进程移到独立 Worker 或带租约的 maintenance job；Web 容器不跑
  长期后台循环。
- 在迁移 Worker 之前先补齐 P0 预热修复：扫描从未构建投影的活跃企业，并先 sweep 后 drain；
  增加“全新部署无 state 行”和“脏企业反复失败”的回归测试。
- 所有任务统一要求：幂等键、企业范围、租约、退避重试、死信、取消和审计事件。

验收：100/500/1000 家合成客户批量任务可续跑；单户失败不影响其他客户；API 重启不丢任务。

## 2. 事务所运营驾驶舱

后端新增只读聚合服务 `firm_cockpit`，复用 `workspace` 投影、`monthly_workbench`、`closing`、
`delivery` 和 `operations`，避免前端逐客户串行请求。

接口建议：

- `GET /api/firm/cockpit?period=`：客户 × 五步阶段、异常账龄、逾期、最近活动、负责人。
- `GET /api/firm/workload?period=`：按会计、团队统计客户数、未结任务、复核积压。
- `POST /api/firm/assignments/preview` 与 `/apply`：转派预览和幂等确认。
- `GET /api/firm/risks`：长期不动、反复退回、交付异常、模型失败和存储异常的客户。

前端在 `WorkspaceHome` 之外新增事务所管理壳 `/firm`，使用热力矩阵、风险列表和负载图；
点击行进入现有账套工作区。不要把这些统计重新堆回会计首页。

验收：1000 家客户首页固定查询次数、分页稳定；所长可在两次点击内定位并转派风险客户。

## 3. 客户标准化开通

把企业、税务、科目、角色、银行、期初和服务单串成可恢复的开通流程。

- 新增 `onboarding_runs` 与 `onboarding_steps`（或在企业服务单下建等价模型），保存模板版本、
  每一步结果、阻断项和确认人。
- 复用 `catalog` 的科目模板、`service_plans` 的角色与资料清单、`monthly` 的期初余额和
  `ledger` 的稳定主数据，不复制业务规则。
- 增加“从模板复制”：行业模板、客户规模模板、历史客户复制；每次复制生成新的版本化记录。
- 最后一步执行 dry-run：检查税号、银行、科目、权限、资料清单和月结前置条件，不生成正式凭证。

前端采用五步向导，每步只保留一个主动作；完成页直接给出“进入本月账套”和“发给客户员工”的两个入口。

验收：同一模板开通下一家客户的中位时间显著下降；任一步中断后可续跑；dry-run 能发现缺失配置。

## 4. 异常收件箱与工作量调度

以现有 `workspace_task_projections` 为读模型，增加可操作的任务分配字段，避免新建第二套待办系统。

- 任务投影增加 `assignee_id/team_id/due_at/sla_state/last_activity_at`，来源仍以事实表为准。
- 新增 `GET /api/firm/inbox`：今天、逾时、阻塞、待复核、后台失败五类视图。
- 新增 `POST /api/firm/tasks/{kind}/{id}/assign` 和批量版本；写操作必须重新核对企业、账套和当前版本。
- 前端会计首页改为“异常优先”的四个泳道；批量选择后显示可执行动作，不显示整套后台 URL。

验收：负责人变化后投影可重建；同一任务不被重复领取；逾期和阻塞口径可由独立 SQL 复算。

## 5. 客户端极简门户

建立独立的前端信息架构，复用现有 API，不复制业务逻辑。

- 一级页面限制为“待办、提交、资料清单、消息、本月简报”。
- 普通员工隐藏科目、置信度、规则名、XML 路径和内部状态码。
- 手机首屏只保留拍照/上传与一个待补事项；提交成功后给出人话状态和预计处理人。
- 客户老板只保留审批、简报、需要关注的风险和下载结果。
- 前端新增 `ClientPortalShell`，与会计工作壳共享组件和 design tokens，不共享完整导航。

验收：员工从打开到提交票据不超过三到五个主要动作；无横向溢出；退回原因可直接定位到补件动作。

## 6. 集成中心

把现有文件桥接升级为可观测的数据接入平台，保留“先证据、后入账”的边界。

- 统一 `ingestion_connections` 与 `ingestion_runs`：来源、连接状态、最近游标、失败码、重试次数、
  原件摘要和同步日志。
- 先补 CSV/XLSX、邮件附件、SFTP、Webhook 四类通用入口，再接银行、电子发票和 T+ 适配器。
- 所有外部记录先进入暂存区，经 schema 版本校验、金额整数化、企业/月套核验后，再由人工确认转为业务来源。
- 对外 API Key 采用 scoped token、IP/来源限制、Webhook 签名和可撤销审计。

前端集成页只展示“连接是否正常、上次同步、失败记录、重试”，把字段映射留在低频设置抽屉。

验收：同一来源重复投递幂等；断点可续；错误可按批次追溯；客户 IT 无需读日志文件即可定位失败。

同时把上传传输限额拆成“普通内联请求上限”和“两段式直传上限”。当前网关流式能力为 50 MiB，
但业务附件仍为 10 MB；前端必须分别读取两个口径，不能用一个 `max_file_bytes` 同时表达两种通道。

## 7. AI 工作流副驾

把 AI 限定为“证据整理、异常解释、任务预填”的工作流副驾，不提供自动记账权限。

- 复用 `assistant/service.py`、`assistant/actions.py` 和统一右侧抽屉，新增固定的工作流能力：
  异常解释、缺件清单、匹配候选、关期风险摘要、客户简报草稿。
- 每个能力返回结构化结果：结论、证据引用、可执行动作、不可执行原因、版本指纹。
- 生成的动作只产生预览或草稿；确认后走现有任务/复核 API。
- 增加按企业、能力、模型的成本、延迟、人工采纳率和撤销率统计。
- 发票转挂账属于确定性工作流，不应交给模型猜测往来方：由主数据唯一解析后自动写辅助核算，
  歧义或多候选时返回明确阻断并要求会计选择。

代码上拆分 `GlobalAccountingAssistant.tsx` 的 transcript、composer、plans、history 子模块；
不再新增第三个助手入口。

验收：回答必须能定位到当前授权资料；资料变化使旧回答标记历史；任何模型输出都不能直接审核、锁期或导出。

## 8. 数据面、可观测性与安全

把“能不能扛住”变成可测量的工程指标。

- 为请求、任务、批处理和导出统一 trace ID；结构化日志只记录 ID、耗时、状态和错误码。
- 扩展 `processing_job_metrics` 和 transaction metrics：按 firm/company/domain 输出队列延迟、
  锁等待、p95、失败率、重试率、投影回退和单客户成本。
- 大表建立 `firm_id/company_id/period/status/created_at` 组合索引，并为审计、任务、回执、
  原件元数据制定归档和分区策略。
- API、Worker、数据库和对象存储放在同地域；明确 RPO/RTO 和季度恢复演练。
- 企业版增加 SSO/SCIM、会话设备管理、审计导出、数据保留和数据驻留配置。

验收：1000 家合成租户性能基线、慢查询清单、队列积压告警、一次完整恢复演练和零跨租户事件。

## 9. 前端架构、设计系统与测试效率

先把“大文件、平铺目录、重复请求样板”解决，再做视觉打磨。

- `App.tsx` 拆成 `WorkspaceRouter`、`useWorkspaceRouting`、`useBookWorkbench`、
  `useAssistantDrawer`；目标控制在 400 行以内。
- `LegacyMigrationWorkspace.tsx` 按项目、文件、映射、预览、接管拆成子模块；
  `GlobalAccountingAssistant.tsx` 按消息、计划、历史、输入区拆分。
- `src` 迁移到 `app / domains / shared / admin`；先迁新模块，最后机械搬迁旧文件。
- 提炼 `useScopedQuery/useScopedMutation/useUpload`，统一取消、竞态、dirty 和撤权清空。
- 建立 tokens、表格、表单、错误页、抽屉和任务进度等基础组件，减少页面级 CSS 分叉。
- 把 `recurring/settlement/briefing/integration/catalog/learning/ledger/migration` 八个账套
  子视图按同一 `embedded` 契约迁入 `BookWorkspaceShell`；每次迁一页并保留五步导航回归。
- 删除 `BusinessWorkspace.tsx` 旧版内联 `BusinessForm`；`IntakeWizard` 底栏收敛为
  “暂存草稿”和“提交财务处理（或提交审批）”两个动作。自动保存继续保留，但不再作为第三个按钮。
- 后端测试拆为 unit/sqlite/postgres/e2e 四层，使用并行或分片；本地 smoke 控制在 10 分钟以内；
  CI 保留全量，并增加 100/500/1000 租户性能基准。
- Vite 使用 vendor 分块和路由级懒加载；对部署静态资源继续保留 `_deploy_reload` 自愈机制。

验收：大文件不再增长；首屏不因新增业务模块变慢；本地回归稳定；重部署不会出现旧 chunk/CSS 错误页。

## 推荐顺序

1. 任务/批处理底座、Worker 解耦、可观测性。
2. 事务所驾驶舱、异常收件箱、客户开通模板。
3. 客户端门户与集成中心。
4. AI 工作流能力。
5. 代码拆分、设计系统与规模化性能验收并行推进。
