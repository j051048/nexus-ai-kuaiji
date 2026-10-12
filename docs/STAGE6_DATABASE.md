# 第六阶段模型费用预留与企业运行监控

费用记录属于模型服务运营数据，独立于客户的会计账本。预留、估算及人工核定费用不会生成会计凭证，不会授予凭证审批或导出权限，也不会打开员工票据原文。

## 数据库迁移

PostgreSQL 新增 `016_model_costs_operations.sql`、`017_model_cost_carry_snapshots.sql`、`018_model_cost_late_settlement.sql`、`019_model_pending_reservations.sql` 和 `020_operations_alert_stable_versions.sql`。SQLite 对应新增 `008_model_costs_operations.sql` 与 `009_model_cost_late_usage.sql`。已应用迁移的原文及校验和保持不变，通过追加迁移完善边界；PostgreSQL 仍由管理员显式执行 `python scripts/migrate_accounting.py migrate`。

六张新增表全部启用并强制 RLS。API 使用既有受限登录；后台使用第五阶段独立后台登录，均不得具有超级用户或 `BYPASSRLS` 权限。后台没有费用、告警、原件或心跳表的直接全表读取权。所有有限安全函数固定 `search_path`，撤销 `PUBLIC` 执行权，只授予对应应用或后台角色。

## 金额与供应商报价

金额以人民币微元整数记录：1 元等于 1,000,000 微元。企业政策输入保留两位人民币小数，计费及人工核定保留六位。报价由供应商配置显式提供，系统没有内置模型价格或默认零价。

单次价格快照保存精确字段 `configured / input_per_million_micro / output_per_million_micro / provider / model`。费率表示每百万输入或输出 token 的人民币微元，必须是非负整数且不超过 1,000,000,000,000；输入、输出 token 分别不超过 10,000,000。供应商及模型必须匹配当前识别运行；报价配置不得增加未知字段，不能以浮点数、字符串或空值冒充已配置价格。

估算公式是 `ceil((input_tokens × input_rate + output_tokens × output_rate) / 1,000,000)`，结果以整数微元保存。PostgreSQL 在乘法之前转换为 `NUMERIC`，避免高合法费率与 token 数量的中间乘积超过 `BIGINT`。SQLite 使用 Python 整数计算。已知 usage 和明确零价可以形成已知零费用；缺失 usage 或价格产生 `uncertain`，不能由缺值推断免费。

模型用量估算与供应商账单费用分别保存。费用估算不代替供应商账单，也不证明供应商所有计费项目已经覆盖；人工核定金额及依据另行保存，原报价、原 token 与原估算继续保留。

## 企业政策与费用证据

| 表 | 作用与边界 |
| --- | --- |
| `model_company_policies` | 月额度、OCR/记账建议单次预留、失败/异常积压/等待/心跳阈值、政策版本；只允许企业或事务所管理员写 |
| `model_costs` | 每个 AI 运行唯一费用证据；绑定同一事务所、企业、事项、种类及原请求人；冻结价格、费用月份、预留与不透明结算令牌 |
| `ops_exception_snapshots` | 授权事项处理时记录的异常计数与事实指纹；不保存金额、交易对方、单据原文或模型输出 |
| `ops_alerts` | 企业运行告警，每个企业与告警代码唯一；保留风险指纹、状态、版本及计数说明 |
| `ops_alert_actions` | 人员确认告警的依据；只追加，不能修改或删除 |
| `worker_heartbeats` | 后台存活时间、状态和并发数；不包含客户企业、事项、原件或模型内容 |

`model_costs` 的 `id=run_id`，复合外键进一步校验运行种类和原请求人。成本状态为 `reserved / sent / estimated / uncertain / released / reconciled`。输入身份、费用月份、模型、报价、原始预留及结算令牌不可修改或删除；既有已知 usage、估算或人工核定不能被自动晚响应覆盖。

应用没有 `model_costs` 的直接写权限。预留、发送、结算和核定通过有限安全函数执行。普通员工和账套复核人员不能因此读取全企业费用或监控数据；函数只返回操作状态或布尔值，不返回隐藏原件或其他客户数据。

## 月度预留与跨月风险

费用月份按中国企业的 `Asia/Shanghai` 时间划分，创建、发送、完成及心跳时间仍使用 UTC ISO 时间。10 月 31 日 16:00 UTC 已属于中国 11 月，不能继续使用 UTC 的 10 月额度。

预留函数与会计、队列写入共用事务级 advisory lock。启用额度时，函数在同一事务重新读取企业当前政策，从政策选择 OCR 或建议的单次预留，不能通过调用参数低报金额；报价、月限额与对应预留必须明确且有效。

额度占用为：本月未释放记录的 `confirmed → estimated → reserved` 优先值之和，加上历史月份仍为 `reserved / sent / uncertain` 的原预留。历史已估算或已核定费用不会在新月份重复扣减；历史未核定风险也不会在月初被视为免费。任一月份仍有超预留标志时，新的有额度约束调用继续拒绝，直至管理员核定。

原请求运行与不透明令牌组合唯一；重复同令牌预留不增加占用。不同令牌或修改报价快照的重放不会沿用旧记录。启用额度后，如果占用与本次预留超过月限额，拒绝发送；金额、令牌与当前企业合计由数据库事务再次核对。

`reserved_micro` 聚合表示本月仍持有的待确认预留，只计算 `reserved / sent / uncertain`；不是所有历史调用的原始预留之和。`carried_reserved_micro` 单独表示跨月待确认预留。`exposure_micro` 包含这两类风险及本月已有估算、核定费用。token、估算及核定汇总属于所选月份；没有已知值时返回 `NULL`，前端不得把缺值当成零费用。`reported_count` 只统计已经取得完整输入、输出 token 的调用。

## 中断与晚结算

费用预留提交后，发送阶段独立标记。确认没有发送，或者服务明确判断为可安全释放的拒绝时，费用记录变为 `released`，不再占用额度。外部请求已经发送而用量或价格不明时，记录变为 `uncertain` 并保留原预留。

主动监控扫描把超过 180 秒仍停留在 `reserved / sent` 的记录转为 `uncertain`，保留金额、报价、原请求人及令牌。这样服务器中断不会留下永远无法核定的运行记录。纯读取监控指标不修改费用状态。

原请求人凭原事务所与不透明令牌，可在业务权限撤销之后记录该次调用的费用事实。这只更新成本证据，不读取或发布原件。监控已经标记为未知、且尚无已知估算或 token 的记录，可接收合法晚 usage，形成估算或安全释放；已经核定、估算或保存已知 usage 的记录不会由另一次自动结算重写。

人工核定只允许企业或事务所管理员，对 `uncertain / estimated / reconciled` 状态使用当前版本、明确金额和处理依据执行。`reserved / sent` 不允许直接提前释放；`released` 也不走人工改价。核定保留报价、token 与估算，单独写确认金额，清除超预留标志并追加审计。晚响应不能覆盖人工核定。

## 监控范围与告警

企业全量费用、异常摘要及监控只向当前事务所管理员、企业管理员/所有者、企业财务开放；当前企业关系必须仍有效。管理或监控权限不等于事项原件访问权限。普通员工、部门审批人、只有账套权限的复核或查看人员没有全企业监控读取权。

异常摘要通过 `record_ops_exception_snapshot` 写入：只允许当前事项处理人写同事务所的摘要，返回布尔值，不返回旧计数。普通员工写摘要后仍不能读取全企业摘要。相同事实指纹与计数不刷新摘要更新时间；持续未解决的事项保留首次积压时间。已经审核/导出的映射凭证与关闭 AI 政策的企业，不纳入当前积压指标。处于正常排队中的票据缺识别提示与不能人工解决的流程阻断，由应用在生成摘要时排除。

监控聚合函数只返回该企业的费用、队列、异常计数及基础设施存活布尔值/时间，不返回其他客户的企业 ID、任务 ID、原件或模型输出。心跳只能由后台有限函数记录，API 不能模拟后台存活；后台全库扫描只刷新企业汇总，不读取票据正文。

告警代码包括 `budget_exhausted / quota_overrun / usage_uncertain / task_failures / exception_backlog / queue_wait / worker_unavailable`。同企业、同代码重复扫描复用现有告警，风险解除后标为解决；后台不可用只有存在待处理任务时才产生告警。人工确认需要依据，但不自动清除预算、费用或会计异常。

风险指纹包含当前成本、跨月待确认成本、历史超预留记录、任务或异常事实的内部散列。即使异常数量不变，新事实仍可重新打开已确认告警；没有新风险时保持确认状态、仅刷新观察时间，不增加版本，避免无意义地使人员确认请求的乐观锁失效。散列及内部成本令牌不暴露给前端。

## PostgreSQL 函数契约

| 函数 | 返回及权限 |
| --- | --- |
| `reserve_model_cost(run_id, token, provider, model, pricing_json, reserve_micro)` | 状态或安全错误代码；应用当前处理身份 |
| `mark_model_cost_sent(run_id, token)` | 布尔值；原请求人且当前仍可处理 |
| `settle_model_cost(run_id, token, input_tokens, output_tokens, safe_rejection=false)` | 布尔值；只允许原请求人、原事务所及原令牌；不要求仍能读取事项 |
| `manual_reconcile_model_cost(run_id, expected_version, confirmed_micro, reason)` | 布尔值；企业或事务所管理员 |
| `record_ops_exception_snapshot(event_id, blocking_count, warning_count, oldest_opened_at, context_fingerprint)` | 布尔值；当前事项处理人；应用通常传 `oldest_opened_at=NULL` |
| `ops_company_metrics(firm_id, company_id, period)` | `policy / costs / queue / exceptions / worker` 聚合 JSON；企业监控授权身份 |
| `ops_refresh_company_alerts(firm_id, company_id)` | 刷新计数 JSON；企业监控授权身份或后台 |
| `ops_scan_all_alerts()` | 处理企业数；仅后台 |
| `record_worker_heartbeat(worker_id, state, inflight_count)` | 布尔值；仅后台 |

预留安全错误代码包含 `authorization_changed / run_invalid / pricing_invalid / pricing_missing / policy_invalid / quota_exhausted / quota_overrun / token_mismatch`。操作函数不返回结算令牌；对外成本列表进一步剥离令牌、价格内部信息、原请求人员及事项原件关联。

## 验证与试点边界

`nexus_backend/tests/test_model_operations_postgres.py` 使用显式专用 PostgreSQL 管理员、API 与后台测试连接，创建随机测试企业，不访问生产服务；缺少任一专用测试连接时默认跳过。每个 fixture 结束时，只将本随机事务所残留活跃任务与尝试标为取消，保留不可变业务、识别及费用历史。覆盖真实并发预算预留、微元向上取整与高合法费率乘积、缺报价/缺 usage、月界与跨月未知风险、超预留、版本化人工核定、撤权后只记录费用事实、运营与原件权限分离、后台角色不具有表直读权、心跳与告警去重、新事实重开、同风险稳定版本，以及摘要范围和已审核积压排除。全库队列测试仍须在专用数据库串行运行，避免其他测试进程同时占用全库处理槽位。

本阶段数据库与服务验证使用合成用量和报价，不能代替真实供应商账单校准。首次真实企业试点，应分别配置供应商报价、企业额度及预留；用少量票据核对供应商实际用量、计费口径及人工核定记录，再扩展批量。试点仍限 2–3 家企业，异常人工处理与会计复核后导出保持独立。
