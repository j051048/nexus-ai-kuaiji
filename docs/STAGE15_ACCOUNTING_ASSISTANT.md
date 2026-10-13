# 第十五阶段：全局会计助手

版本 `0.15.0`。工作空间顶部“问会计助手”及左侧入口打开对话面板；手机显示全屏面板。当前凭证原有的依据助手继续用于确定性核对。

## 日常使用

1. 选择企业，勾选明确的账套、期间或业务事项，创建本人会话。每会话只属于一家企业，最多 12 个账套、20 项事项；范围固定，更换资料请新建会话。未选择资料时仅提供一般咨询，后台任务不扫描全企业事项。
2. 向助手提问。它读取当前保存的业务事实、账套数量与金额摘要、所选凭证分录，以及所选事项的当前 OCR 结构字段和阻断提示。附件原件、OCR 全文、未授权事项不发送到对话模型。识别字段仍须核验，程序计算的金额合计不代表利润或纳税结论。
3. 助手可建议识别票据、生成业务分录或流水草稿。先看服务器生成的固定候选清单，再确认执行；预览 30 分钟有效，事实、科目、规则、授权或期间变化后必须重新预览。确认和真实任务回执同事务保存，同一请求重试不重复执行。
4. 关闭面板不会删除会话或中断已登记任务。重新选择企业，在“我的会话”恢复问题、回答和任务回执。任务卡显示真实状态；已有分录任务需在后台运行页核验、重试。历史回复标明当时依据，资料变化时提示旧回复不适用于当前版本。

会话是个人工作记录。事务所管理员也不能读取其他人的聊天；每次列表、详情、消息、任务确认及模型外发前重新校验当前资料权限，失去任何一项授权后整个会话隐藏。业务审批、会计审核和导出继续走原有流程，助手不能代为批准、过账、修改事实或导出。

## 模型、费用及部署

沿用会计后端的可配置 OpenAI 兼容接口。在 API 和独立助手 worker 中设置相同的：

```dotenv
ACCOUNTING_AI_BASE_URL=https://your-provider.example/v1
ACCOUNTING_AI_API_KEY=
ACCOUNTING_AI_CHAT_MODEL=
ACCOUNTING_AI_RESPONSE_FORMAT=json_schema
ACCOUNTING_AI_CHAT_INPUT_CNY_PER_MILLION=
ACCOUNTING_AI_CHAT_OUTPUT_CNY_PER_MILLION=
```

不自动回退到视觉或分录模型。供应商须支持结构化输出及本接口使用的 `max_tokens`，回答限制 4096 token。上下文最多 64 KiB，最近 12 条历史最多 48 KiB；超出范围请缩小资料选择。缺少模型或企业尚未启用“票据与模型处理”时，不发送问题，仍可使用现有人工流程及受控任务预览。

对话费用进入原有 `model_costs`，类型为 `chat`，与 OCR、分录共用企业月度预算。单价由管理员明确配置；费用控制开启时，“单次会计对话预留”须大于零。旧客户端未提交此字段时保留已有设置。未知费用保持预留并进入站内提醒，供应商用量估算与人工账单核定分别保存。

先执行显式数据库迁移，再部署 API 和独立助手 worker：

```powershell
python scripts/migrate_accounting.py migrate
python scripts/run_accounting_assistant.py --env-file .env.accounting
# 单轮运维检查：追加 --once
```

`ACCOUNTING_ADMIN_DATABASE_URL` 只用于迁移。助手须配置 `ACCOUNTING_WORKER_DATABASE_URL` 与同一私有附件根目录 `ACCOUNTING_ATTACHMENT_ROOT`；PostgreSQL 使用已有独立 worker 登录，API 登录不能取得 worker 权限。生产连接必须显式 TLS，密钥及连接不返回浏览器、不载入旧平台环境文件。本地 SQLite 验证可嵌入助手 worker；真实登录环境必须独立进程。

PG066–071 / SQLite051–056 是新增迁移，保留旧迁移及冻结证据。助手和 OCR / 分录后台领取共用最多两个运行槽，同一家企业同时只领取一种处理任务；对话供应商超时或已发送后进程死亡，任务进入中断、结果未知，不自动重发。未发送的任务可依租约恢复，最多三次领取；旧租约不能发布回复。

助手每 15 秒保存专用心跳，任务变化进入企业增量监控。费用与运行监控显示会计对话等待/运行和助手后台状态；排队、连续失败及后台不可用沿用站内告警。OCR 与助手分别判断可用性，互不掩盖故障。聚合监控不展示聊天内容。

## 接口与验证范围

接口前缀 `/api/assistant`：

| 路径 | 用途 |
| --- | --- |
| `GET /scope?company_id=…` | 当前授权账套、事项分页及模型可用性 |
| `POST /conversations` / `GET /conversations?company_id=…` | 创建、分页查询本人会话 |
| `GET /conversations/{id}` / `/messages` | 当前范围及分页消息 |
| `POST /conversations/{id}/turns` | 幂等登记一轮问题 |
| `GET /conversations/{id}/turns/{turn_id}` / `POST …/cancel` | 真实处理状态及取消 |
| `POST /conversations/{id}/actions` / `GET …/actions` | 准备、分页恢复任务预览 |
| `GET …/actions/{plan_id}` / `POST …/confirm` / `GET …/receipt` | 预览详情、确认、真实回执 |

创建、提问和确认均使用客户端幂等键；任务确认还必须带服务器指纹和严格的 `confirmed: true`。浏览器不能指定模型、供应商、任意 SQL、工具参数或其他企业记录 ID。

当前是 2–3 家企业试点的辅助记账版本。对话模型实际供应商尚未配置或实测；兼容 HTTP 完整链路已用明确的本地合成端点验证，结果不是实际模型质量或真实账务验收。测试与截图见 [验证记录](VALIDATION_STAGE15_20261013.md)。
