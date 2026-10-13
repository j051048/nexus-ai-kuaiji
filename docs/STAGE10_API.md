# 第十阶段 API：事实纠错、重复隔离、月结与试点效率

范围仍是中国大陆企业、人民币、2–3 家真实企业试点，会计复核后导出。自动任务可以生成未审草稿，不能代替会计审核、规则发布或月结资料完整性确认。本文依据当前接口和输入模型；真实 PostgreSQL、浏览器及恢复演练结果以阶段验证记录为准。

## 业务事实修订

前缀 `/api/fact-revisions`。提议人需要原账套和目标账套当前编辑权限；独立复核人需要两侧当前复核权限，且不能是业务申请人或修订提议人。目标账套必须属于同一企业，修订日期必须归属于目标开放期间。

| 方法与路径 | 行为 |
|---|---|
| GET `/context/{event_id}` | 读取当前已移交事实、事项/事实/草稿版本及有权限的目标期间 |
| GET 空路径 | 按当前两侧账套权限过滤后分页；支持 `event_id`、`limit`、`offset` |
| POST `/events/{event_id}` | 提出一份不可覆盖的事实修订 |
| GET `/{id}` | 原事实快照、提议事实、独立决议与替代链 |
| POST `/{id}/review` | 独立接受或拒绝当前修订 |

提议输入：`expected_event_version`、`expected_fact_version`、`expected_draft_version`、`target_book_id`、完整 `BusinessPayload`、3–1000 字 `reason`、`idempotency_key`。金额须为字符串，例如 `"80.00"`。复核输入：`expected_version`、`decision=accept|reject`、`reason`、`idempotency_key`。

- 状态：`pending → rejected | replaced | adjustment_proposed`。一个事项只能有一份待复核修订。
- `replaced` 只适用于未审草稿：追加新来源和新空草稿，记录原/新来源、草稿、账套及事实版本，更新当前映射。后台生成草稿与版本失效检查沿用原工作流；原来源、原分录、原审批提交快照和原件保持留存。
- 接受替代前须先处理原有效银行匹配、活跃异常/补件及有效核销分配；不会静默把旧任务迁移到新期间。
- 已审核或已导出的凭证只形成 `adjustment_proposed`。原事实、映射和凭证保持冻结；本期没有以备注直接完成调整的入口，未完成调整建议会阻止月结。
- 事实或草稿变化后不能接受旧提议。具备当前权限的独立复核人仍可拒绝过时提议，避免待复核记录永久阻塞。

## 重复银行来源隔离

前缀 `/api/books/{book_id}/sources/{source_id}`。

| 方法与路径 | 行为 |
|---|---|
| GET `/disposition` | 当前隔离状态、可操作权限和草稿版本 |
| POST `/duplicates/{canonical_id}/same-transaction` | 选择保留的主来源，隔离当前重复副本 |

输入为 `expected_version`、`source_fingerprint`、`canonical_fingerprint`、`reason` 和 `idempotency_key`。须为当前账套复核人，在开放期间核对两侧原始事实。日期、金额、方向、交易对方和已知银行账户须符合候选关系。

副本必须未审、未导出、未关联；有效核销分配和未关闭的原异常/补件也须先人工处置，避免隔离后冻结草稿而无法继续核验补件。不能隔离已有其他副本引用的主来源，也不能建立反向/环形隔离链或覆盖“独立交易”的历史决定。来源和草稿不删除；隔离副本退出待办、建议、匹配、审核和导出。主来源不再因已隔离副本重复阻断。真实不同交易继续使用第九阶段 `/distinguish`。

## 被替代来源的历史证据

前缀 `/api/books/{book_id}/sources/{source_id}/history`。

- GET 空路径：原账套当前读权限下，返回该来源替代前的固定事实快照、原审批提交快照及当时原件清单。
- GET `/attachments/{attachment_id}/content`：只下载清单中的历史原件，每次检查当前账套权限、事件归属、快照 SHA-256、字节数和私有文件安全路径。

旧期间读权限不开放同一事项新期间的当前事实、后续补件或当前映射。通用业务事件及附件接口仍按当前映射授权。HTTP 历史响应不包含存储键；下载禁止缓存。

## 长期账套、独立期间与主数据

前缀 `/api/books/{book_id}/ledger`。长期 ID 连接多个月份，原物理 `book_id`、来源和冻结凭证不迁移。同一长期账套的同一月份仅映射一个物理账套；管理长期账套需要明确授权，父级身份不自动授予旧月份访问权。

| 方法与路径 | 行为 |
|---|---|
| GET 空路径 | 当前可见期间、管理权限、企业主数据及待评测继承规则 |
| POST `/managers` | 明确授权长期账套管理人；`user_id`、`reason` |
| POST `/periods` | 从已授权源期间建立新期；显式选择继承人员和规则 |
| POST `/periods/link` | 明确关联同企业既有期间，核对旧长期 ID；不自动合并同月账套 |
| POST `/candidates/{id}/evaluate` | 用新期真实正例/反例评估继承公式，形成待发布规则 |
| POST `/entities` | 创建往来方、项目或银行账户稳定 ID |
| PUT `/entities/{id}` | 按 `expected_version` 更新名称/启用状态并保留历史 |
| POST `/aliases` | 为主数据提议原始名称别名 |
| POST `/aliases/{id}/review` | 另一位复核人批准、拒绝或停用别名 |

新期输入：`period`、`name`、`source_book_id`、`inherit_user_ids`、`inherit_rule_ids`、`idempotency_key`。只复制被明确选择的当前授权；只继承规则公式，不复制旧期票据或审核样例，不自动发布规则。

主数据类型：`counterparty`、`project`、`bank_account`。别名批准需要独立复核，只有启用实体和已批准别名参与解析。原流水/业务事项中的名称继续保留，不因更名改写原始事实。

## 月结预检

前缀 `/api/books/{book_id}/closing`，要求当前账套复核权限。

| 方法与路径 | 行为 |
|---|---|
| GET 空路径 | 当前确定性检查报告、历史快照、本人复核与关期回执 |
| POST `/preflights` | `idempotency_key` 固定预检快照 |
| GET `/snapshots/{id}` | 查看固定快照及是否过时 |
| POST `/snapshots/{id}/review` | `reason`、`materials_confirmed=true` 明确确认资料完整性 |
| POST `/close` | `snapshot_id`、`reason`，原子复验并锁期 |

预检覆盖已进入系统的事实和凭据：未审/未处理资料、异常与补件、待办任务、流水匹配、事实修订/调整建议，以及导出后未知、部分成功或有差异的回执。回执按金额、日期、摘要、逐行科目、借贷及辅助项重新比较，核对外部凭证号重复和历史冲突，不能只凭 `accepted` 状态通过。检查报告的显示条目有上限，是否通过按完整计数判定。

未移交员工资料缺少发生日期或日期不符合真实日历时，无法可靠归期，只返回本企业账套级完整性阻断，不披露该事项 ID、原文或原件。同期核销登记、分配和逆分配纳入当前账套私有事实指纹；银行收付尚未完整分配仍阻断月结，正常未付款业务的未结余额本身不阻断月结。

快照通过、本人确认和最终锁期是分别执行的操作。资料、版本、回执和核销记录变化使旧快照失效；复核及锁期每次另查当前权限，撤权后不能执行。最终关期重新检查事实指纹。系统无法从“数据库无异常”推断客户没有漏交资料，必须保留人工完整性确认。旧无快照的关期入口返回 409；已关闭期间仅查看历史，仍允许追加回执和说明，不据此解封。

## 人工效率统计

| 方法与路径 | 行为 |
|---|---|
| GET `/api/efficiency/sessions` | 本人工作会话分页 |
| POST `/api/efficiency/sessions` | 显式开始一个账套活动或本人的业务录入计时 |
| POST `/api/efficiency/sessions/{id}/commands` | `heartbeat|pause|resume|complete`、版本和幂等键 |
| GET `/api/books/{book_id}/efficiency` | 当前复核授权下的人工计时和审计指标 |

活动：`review`、`correction`、`supplement`、`month_close`、`business_entry`。账套活动要求复核权限；员工只能为本人事项的 `business_entry` 计时。同一人在一个事务所不能重复并行计入多份活动。每次心跳只确认有限的活跃间隔，不把后台开着页面、断网或离开电脑的整段时间作为人工工时。

这些指标用于测量每家企业人工分钟数、修正和异常负担，不直接证明一人可管理 200–500 家或替代正式总账。

## 财务软件与核销边界

T+ 文件接入路径为 `/api/books/{book_id}/integrations/tplus`。其配置、参考导入、冻结包、实时权限和未知结果禁止重发的完整契约见 [文件接入说明](STAGE10_FINANCIAL_BRIDGE.md)。具体产品版本未知，真实连接保持关闭。

核销模块采用同企业、同开放期间的未结事项与收付分配，金额和尾差由确定性程序处理，操作与 API 见 [同期辅助核销](STAGE10_SETTLEMENTS.md)。当前员工模板首先覆盖未付款应付清偿；跨期收付和未收款业务新模板尚未实现。原业务事实及原凭证不随分配改写，银行清偿分录仍须会计复核，已审/已导出记录不能以普通 CRUD 修改。
