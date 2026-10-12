# 第五阶段 · 持久票据队列与批量识别

本阶段把已上传的企业票据送入持久后台队列，复用第四阶段的私有附件准备、结构化识别、交叉核对和异常复核。一个队列任务处理一个附件；成功不等于会计处理已经通过，分录采用、独立审核和导出仍沿用原流程。

首轮仍面向 2–3 家企业。企业间轮转能防止某一家大量上传持续占用领取顺序，不构成对 200–500 家企业的容量承诺；大 PDF 和供应商延迟仍会影响实际等待时间。

试点操作先等待识别完成后再提交业务审批，以保持入队时的事项版本一致；等待或处理期间事项版本、原件摘要、权限、期间或模型配置变化，旧任务会停止，并由当前获授权人员按新资料重试。

## HTTP 契约

所有端点位于 `/api`，使用原有登录、事务所选择与当前数据库权限。服务端决定申请人、事务所、输入摘要、租约及模型请求身份，客户端不能传入这些控制字段。单批必须属于同一企业，不会因为管理事务所而自动获得未移交员工原件。

| 方法与路径 | 输入 | 返回 |
| --- | --- | --- |
| `POST /intelligence/batches` | `company_id`、`items`、`idempotency_key` | `202`，持久批次及当前可见任务 |
| `GET /intelligence/batches` | 可选 `company_id`、`event_id`，`limit` 默认 50、1–100，`offset` 默认 0、0–100000 | `{batches,total}`，范围过滤后的列表与数量 |
| `GET /intelligence/batches/{batch_id}` | 无 | 当前可见批次、任务及进度；不可见时 `404` |
| `POST /intelligence/jobs/{job_id}/retry` | 新 `idempotency_key` | 人工重试后的当前可见批次详情 |
| `POST /intelligence/batches/{batch_id}/retry` | 新 `idempotency_key` | 对当前可重试且获授权的任务提交人工重试 |
| `POST /intelligence/jobs/{job_id}/cancel` | `reason`，1–1000 字，不能全空白 | 取消后的当前可见批次详情，保留处理历史 |

创建示例：

批次重试另存请求人自己的命令回执。同一键重放只读取当前可见进度，不会把后来才失败的任务加入重试；没有可重试任务时返回 `409`，不保存接受回执。单份与批次重试均保留尝试历史。

```json
{
  "company_id": "authorized-company-id",
  "items": [
    {
      "event_id": "authorized-event-id",
      "attachment_id": "attachment-of-that-event",
      "expected_version": 2
    }
  ],
  "idempotency_key": "unique-batch-request-key"
}
```

`items` 为 1–100 个对象，版本为严格正整数，不能用字符串或布尔值代替。幂等键为 8–120 字；同一操作、同一键及相同输入返回原记录，键被用于不同输入时拒绝。服务提交批次前整体验证每个事项、附件和版本；其中一项越权、跨企业或版本已改变时整批不提交，不启动模型处理。`202` 仅表示已保存，HTTP 请求不执行识别。

批次 `status` 为 `queued / running / completed / attention / cancelled`，进度为：

```text
progress: {
  total, completed, succeeded, failed, interrupted, stale, cancelled,
  pending, running, percent
}
```

这些数字只基于调用者当前可见任务重新计算。员工可看本人事项，获授权部门审批人看其部门范围，企业财务看所属企业范围，会计看已移交到获授权账套的事项；隐藏任务不进入总数、百分比或批次状态。授权撤销后列表和详情立即按新范围返回，历史批次不保留旧的读取权。

每个任务返回事项摘要、文件名、状态、尝试次数、安全错误说明、重试时间、`permissions.retry/cancel` 和尝试历史。响应不含私有存储键、供应商密钥、租约令牌或原始供应商异常；查看识别内容仍通过原事项的票据核对接口。

## 状态、重试与重启恢复

| 任务状态 | 含义与处理 |
| --- | --- |
| `queued` | 持久保存，等待 worker 领取 |
| `running` | 已领取，租约仍有效；历史阶段可区分原件准备和已经发送 |
| `retry_wait` | 明确繁忙或限流，等待有界退避到期 |
| `succeeded` | 已持久保存结构化结果和核对异常，不表示已审批 |
| `failed` | 明确处理失败，可按当前授权人工重试 |
| `interrupted` | 请求可能已经发送而结果不确定，需人工判断后重试 |
| `stale` | 输入、权限或可处理状态已改变，结果未继续发布 |
| `cancelled` | 已取消，原历史保留 |

worker 领取记录后先准备图片或 PDF，再重新检查授权及版本，登记发送阶段后请求模型。发送前崩溃的过期租约恢复排队；已登记发送但没有持久结果的过期任务进入 `interrupted`。如果第四阶段识别结果已经提交，仅任务回执未提交，恢复程序读取已保存结果完成回执，不再次请求供应商。恢复不会推定未知结果为成功。

明确繁忙或 HTTP 限流允许自动退避，单轮最多 3 次尝试，等待 30 秒、60 秒；超时、断网及发送后的不确定失败不自动重发。损坏、超限、不合规结构化输出或未配置供应商等错误交人工处理。人工重试开启新的尝试轮次和识别幂等键，并保留原失败记录；每个任务最多 30 次历史尝试。重复提交同一个人工重试键不会增加新的请求。

领取、完成和恢复使用数据库事务、租约令牌和条件更新，多 worker 不能同时成功领取同一任务。过期 worker 的迟到结果不能覆盖当前租约。取消也不会自动回滚供应商已执行的请求或费用；它只阻止队列继续发布和调度该任务。

任务重试仍要求当前处理权、企业处理开关、附件归属和事项版本。已审核或导出的业务凭证被冻结，后台任务不会继续改变相应证据；异常的人工处理权限、自审限制和会计复核后导出保持第四阶段规则。

## 本地与 PostgreSQL 部署

SQLite 本地演示默认启动嵌入式 worker，队列与演示账务保存在同一数据库文件。要暂停自动领取，设置 `ACCOUNTING_QUEUE_EMBEDDED_WORKER=0`；任务仍持久保留。自动化测试显式停用嵌入 worker，并手动运行 worker，使用临时数据库、合成票据和注入供应商响应。

PostgreSQL API 进程必须停用嵌入 worker，单独配置 worker 登录；不能把 API 登录或数据库管理员连接用作调度账号。

API 启动拒绝带 `accounting_worker` 角色的登录。worker 启动要求独立 `NOINHERIT` 登录、仅有 `accounting_app/accounting_worker` 两个父角色，拒绝超级用户、绕过 RLS、创建数据库或管理角色权限；生产还须 PostgreSQL 和显式 TLS。入队及重试的调度时间使用 PostgreSQL 时钟，避免 API 主机与数据库时钟不同步造成等待偏差。

1. 用新项目管理员连接显式运行 `python scripts/migrate_accounting.py migrate`，然后运行 `python scripts/migrate_accounting.py provision-worker-login --username accounting_worker_runtime`。管理员连接仅用于迁移脚本。密码从 `ACCOUNTING_WORKER_PASSWORD` 或隐藏输入读取，不作为命令行参数传递。
2. 在新的、Git 忽略的 `.env.accounting` 中配置受限 API 连接、独立 `ACCOUNTING_WORKER_DATABASE_URL`、真实登录、供应商及持久附件根目录。API 与 worker 必须连接同一个会计数据库，并能读取同一个私有附件目录；生产连接显式启用 TLS。Supabase 使用项目 Connect 面板提供的真实地址及角色用户名格式。
3. 启动 API，另外启动 worker：

```powershell
.\.venv\Scripts\python.exe scripts/run_accounting_worker.py --env-file .env.accounting
```

只有显式指定的配置文件会被加载，不读取旧 `.env`，不覆盖已经注入的进程环境变量。生产 worker 凭数据库专用受限角色调度，不持有用户浏览器 token、service role key 或超级用户凭据；执行每个任务时按原申请人的当前授权处理原件。数据库调度函数只向专用 worker 角色开放，浏览器及 API 普通角色无法领取其他企业的工作。

| 配置 | 默认与范围 |
| --- | --- |
| `ACCOUNTING_QUEUE_EMBEDDED_WORKER` | 本地 SQLite 默认 `1`；PostgreSQL 使用 `0` |
| `ACCOUNTING_WORKER_DATABASE_URL` | PostgreSQL 独立 worker 受限登录，指向同一数据库 |
| `ACCOUNTING_QUEUE_CONCURRENCY` | 默认 `2`，允许 `1` 或 `2` |
| `ACCOUNTING_QUEUE_POLL_SECONDS` | 默认 `1`，允许 `0.2–10` 秒 |
| `ACCOUNTING_QUEUE_LEASE_SECONDS` | 默认 `180`，允许 `150–600` 秒 |

首次真实试点先用 1 个 worker、并行 1–2 件和少量票据验证。备份与恢复必须同时包含数据库和私有附件；worker 重启可恢复租约状态，不能代替业务数据备份。上线前用真实模型检查识别字段、异常处理、任务失败、进程重启和会计复核导出的完整闭环。

本轮测试只验证队列及业务边界，供应商响应来自明确的合成 fixture；没有据此确认真实 OCR 准确率、供应商在线稳定性或正式账务正确率。
