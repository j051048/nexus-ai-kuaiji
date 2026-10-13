# 第十阶段：增量监控、运行观测与人工效率

## 持久增量监控

PG043 / SQLite029 新增 `ops_company_dirty` 和 `ops_company_refresh`。任务、费用、补件、回执、凭证和期间变化在同事务内登记企业；队列只保存事务所/企业 ID、首次待刷新时间和版本，不保存原文或私有材料。重复变化保留首次时间，避免热企业持续抢占其他企业。

worker 默认每轮最多消费 20 家变化企业、5 家已到期企业，按最早时间轮转；已观测企业 30 秒后可再次巡检，用于无新事件的失败窗口过期、费用未知、心跳异常和等待超时。实际刷新延迟还取决于 worker 调用频率、企业数和数据库耗时，水位通过企业概览 `monitor.last_refreshed_at/next_due_at` 返回，不保证精确每 30 秒刷新全部企业。

`OperationsService.heartbeat(...)` 与 `refresh_pending(...)` 可以放在两个独立短事务中。旧 `maintenance(...)` 保持接口兼容。后台新增能力仅返回处理企业数；普通 API 账号不能消费 dirty 队列，worker 也不能直接读取它或打开员工原件。告警复查保持 acknowledged 状态和既有幂等/乐观版本逻辑，风险发生变化才重新打开。

企业概览增加 `workflow`：未关闭异常/补件数、逾期补件数、无回执导出数和最近回执的 unknown/rejected 条数。回执状态来自用户导入的事实，不表示供应商独立确认；金额、分录和辅助项差异仍使用交付工作台逐笔核验。未关闭案件进入现有异常积压告警，不展示内部标题或员工内容。

## 性能观测

`GET /api/operations/companies/{company_id}/telemetry` 仅当前企业管理员/事务所管理员可用。返回当前 API 进程最近 900 秒、至多 4096 个**已明确标记该企业**的事务样本，含连接、写锁等待、事务耗时、回滚计数。未标记企业事务不分摊给企业；其它进程和历史信息不在此样本中。

全库写协调锁保持不变，模型并发仍全库 2、每企业 1。下一步依据真实锁等待和事务耗时分布决定拆锁及增加并发，不能把当前进程观测当成 200–500 家容量验收。

## 明确人工计时

- `GET /api/efficiency/sessions?limit=20&offset=0`：仅本人、且当前仍授权的范围，数据库中授权过滤后分页。
- `POST /api/efficiency/sessions`：`book_id` 或本人 `event_id` 二选一，`activity`、`idempotency_key`。账套需要当前 review 权限，员工仅本人业务录入。
- `POST /api/efficiency/sessions/{id}/commands`：`heartbeat/pause/resume/complete`、`expected_version`、`idempotency_key`。重放返回原收据，不重复加时；每人同事务所仅一个活跃计时。
- `GET /api/books/{book_id}/efficiency`：仅当前账套 review 权限，统计该账套的明确计时和审核审计。

每次确认最多计入 60 秒、单次最多 8 小时；暂停期间不加时。前端只在页面可见、有焦点且最近 30 秒出现操作时确认活跃。未确认或关闭页面的时间不自动补齐，缺少计时记录不能解释成零人工投入。API 无任意传入工时字段，PG 原生能力使用服务器时间计算，运行账号不能直接 UPDATE 累计秒数。

审核指标口径：首次审核前无 `draft.update` 记录称“未记录修正”，不等同于会计正确率；首次审核前有 `draft.company_rule.apply` 称“规则应用覆盖”，不等同于最终全部采用规则。补件等待为首次请求到首次提交响应的日历时间，不纳入人工工时。当前未收集真实企业效率基线，不计算节省率或承诺一人管理数量。
