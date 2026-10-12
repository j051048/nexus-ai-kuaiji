# 第五阶段持久票据队列与数据库权限

第五阶段的批次、单件任务、尝试历史及调度顺序都保存在数据库。HTTP 提交只建立任务；独立后台进程领取任务，并在原有员工事项权限范围内调用第四阶段识别服务。模型结果仍是证据，异常仍须人工处理，凭证仍须会计复核后才能导出。

## 迁移与独立后台账号

PostgreSQL 新增 `013_ocr_queue.sql`、`014_ocr_queue_fencing.sql` 与 `015_ocr_batch_retry_receipts.sql`；SQLite 新增 `006_ocr_queue.sql` 和 `007_ocr_batch_retry_receipts.sql`。既有迁移原文及校验和保持不变。PostgreSQL 由管理员显式执行 `python scripts/migrate_accounting.py migrate`，应用服务不持有迁移凭据。

`accounting_worker` 是独立的 `NOLOGIN / NOSUPERUSER / NOBYPASSRLS / NOCREATEDB / NOCREATEROLE` 能力角色。它没有队列表或业务原件表的直接读取权，也没有 schema 创建权；全库调度只能通过撤销 `PUBLIC` 权限、固定 `search_path` 的有限安全函数执行。迁移拒绝已有危险权限或父角色的同名角色，不自动覆盖或降级管理员配置。

新增配置命令 `python scripts/migrate_accounting.py provision-worker-login --username accounting_worker_runtime`。管理员连接来自 `ACCOUNTING_ADMIN_DATABASE_URL`，后台账号密码来自 `ACCOUNTING_WORKER_PASSWORD` 或隐藏交互输入，不接受密码命令行参数，不打印连接或密码。该命令拒绝覆盖已有账号；密码至少 20 个字符。

新后台登录是 `LOGIN / NOINHERIT / NOSUPERUSER / NOBYPASSRLS / NOCREATEDB / NOCREATEROLE`，只加入 `accounting_app` 与 `accounting_worker`。后台处理一件任务时以该任务记录的人员身份进入 `accounting_app` 事务，重新核对权限；领取、租约及恢复操作暂时切换到 `accounting_worker`。既有 API 登录继续使用 `accounting_app` 和只负责身份同步的 `accounting_system`，不会获得 `accounting_worker`。浏览器不获取任何数据库连接或后台角色。

真实企业部署要求 API 和后台连接到同一个会计 PostgreSQL 数据库，并访问同一持久私有附件目录。Supabase 使用同样的受限数据库登录与显式迁移；`accounting` schema 仍不向前端 PostgREST 开放。模型配置与原件目录说明见 `AI_PROVIDER_SETUP.md` 和第五阶段 API 部署说明。

## 表与约束

| 表 | 持久内容及约束 |
| --- | --- |
| `ocr_batches` | 单企业批次、原始请求人、请求指纹及幂等键；`(firm_id, created_by, idempotency_key)` 唯一；批次身份不可修改 |
| `ocr_jobs` | 附件所属事项、版本、原件哈希、模型配置指纹、执行人员、状态、累计尝试次数、租约、当前识别运行和安全错误说明 |
| `ocr_attempts` | 每次领取时的人员与输入快照、独立租约、`claimed / sending / finished` 阶段、识别运行及终态；终态历史不可重写 |
| `ocr_retries` | 有权限人员显式重试的请求键、人员及时间；每个任务和重试请求键唯一；记录只追加 |
| `ocr_batch_retries` | 批次重试命令的接受回执；按批次、请求人、请求键唯一，仅保存命令存在，不保存原可见事项集合或数量 |
| `ocr_company_schedule` | 每个事务所与企业的最近调度序号；由后台函数维护，前端及 API 不读取全库调度数据 |

批次与任务绑定相同 `(firm_id, company_id)`；事项通过 `(event_id, firm_id, company_id)` 复合外键隔离；附件还须属于相同事项。当前识别运行及每次尝试的识别运行通过 `(run_id, event_id, firm_id, company_id, attachment_id)` 复合外键绑定同一原件。后台绑定函数另外要求运行种类为 OCR、输入版本一致、请求人员等于执行人员且运行尚未完成。

同一附件在 `queued / running / retry_wait` 中最多存在一个活跃任务，数据库唯一索引是并发重复提交的最终防线。批量请求与模型调用都不会产生新的附件或修改员工填写的业务事实。

任务状态为 `queued / running / retry_wait / succeeded / failed / interrupted / stale / cancelled`。运行任务必须同时具有租约令牌、到期时间、后台实例 ID，且没有完成时间；排队任务没有租约与完成时间；终态没有租约并且具有完成时间。所有持久时间使用 UTC ISO 字符串，PG 调度与租约判断使用数据库时钟。

`attempt_count` 记录历史累计次数；初始 `max_attempts=3`。自动重试不得突破当前预算。人员显式重试保留旧次数及历史，最多增加 3 次预算，累计最多 30 次。API SQL 权限只能把有处理权的 `failed / interrupted / stale / cancelled` 任务恢复为 `queued`，执行人员改为当前请求人；不得伪造运行、成功、租约或尝试次数。原始请求人与批次、企业、事项、附件身份保持不变。

批次重试还保存一次命令接受回执。例如第一次提交重试时有一件失败、一件运行中，后者随后失败；重复同一请求键不会再为后者新增尝试或模型费用。回执与实际重试在同一事务提交，失败或没有可重试项不留下已接受回执；后续新的重试意图须使用新键。回执只向请求人且当前批次仍可见时开放，不保存隐藏任务的 ID 集合或数量，权限变化不会打开旧隐藏事项。

## 公平调度与崩溃恢复

领取函数与原有会计写入使用同一事务级 advisory lock，保证并发认领、取消、过账相关校验及恢复不会交错破坏状态。锁仅覆盖数据库操作，不覆盖 PDF 准备或网络模型调用。

每个事务所与企业最多运行 1 件任务，全库最多运行 2 件。调度从已到可运行时间的企业中，优先选择最近调度序号最小的企业；再按任务创建时间及 ID 选择该企业的任务。序号保存在数据库，后台进程重启不重置企业顺序。单企业大量上传不会连续占用全部处理槽位。

后台领取生成新的不可预测租约令牌并追加输入快照。发送模型请求之前，先将尝试阶段持久化为 `sending`；只有租约仍有效且仍绑定当前未完成识别运行时才允许发送。续期及完成操作同时检查任务、令牌、状态与到期时间。旧进程或旧响应不能凭已失效的令牌更新任务或发布新的识别证据。

恢复函数处理过期的 `running` 任务：

- 对应识别运行已经提交终态时，直接采用持久化的 `succeeded / failed / stale` 结果，不重复请求供应商。
- 尚未进入 `sending` 时，在预算内转为 `retry_wait`，后续领取生成新的令牌与尝试；超出预算则失败。
- 已进入 `sending` 而结果尚未发布时，转为 `interrupted`，必须由获授权人员决定是否再次调用供应商。
- 仍在运行的旧识别记录转为 `stale`，防止迟到响应发布；正常提前失败、停止与取消也执行同样的防护。

发送后无法确认的外部请求不承诺供应商计费恰好一次。后台采取保守恢复策略，保留尝试记录并请求人工决定；人工重试可能再次消耗供应商额度。

取消要求当前事项处理权限且账期仍开放。即使企业关闭 AI 开关，仍可取消排队、运行、等待重试或中断的任务；取消不会读取原件，也不授予新增外部处理权。公司开关关闭后不能新建或重试识别任务。

## 函数契约

| 函数 | 返回值 |
| --- | --- |
| `claim_ocr_job(worker_id, lease_seconds=180, global_limit=2)` | 0 或 1 行完整 `ocr_jobs`，仅后台可调用 |
| `recover_ocr_jobs()` | 恢复任务数，仅后台可调用 |
| `worker_check_ocr_lease(job_id, token)` | 布尔值，仅后台可调用 |
| `worker_bind_ocr_run(job_id, token, run_id)` | 布尔值，仅后台可调用 |
| `worker_mark_ocr_sending(job_id, token)` | 布尔值，仅后台可调用 |
| `worker_renew_ocr_lease(job_id, token, lease_seconds=180)` | 布尔值，仅后台可调用 |
| `worker_finish_ocr_job(job_id, token, status, error_code='', error_detail='', run_id=NULL, retry_delay_seconds=0)` | 布尔值，仅后台可调用 |
| `cancel_ocr_job(job_id, reason)` | 布尔值，API 当前事项处理身份可调用 |

领取与续期的租约限定 30–600 秒；全库并发参数限定 1 或 2；重试延迟限定 0–3600 秒。SQL 层拒绝突破这些范围。对外 API 不返回租约令牌、配置指纹、内部运行关联或跨企业调度记录。

## 权限与验证

六张新表全部启用并强制 RLS。任务、尝试、单件重试记录沿用本人、获授权部门审批人、企业财务，以及移交后的账套访问范围；批次重试回执进一步限定为请求人本人。批次只有存在当前人员可见的任务时才可见；批次进度必须由应用按当前可见任务计算，不能透露失权或无权事项的数量。企业与事务所管理配置权限本身不打开员工原件。

模型发送和结果发布仍重新检查当前事项权限、账期、版本、原件哈希与模型配置。取消或撤权后的迟到响应不能绕过这些检查。后台队列不自动处理异常，不自动修改凭证，不审核或导出凭证；第四阶段的确定性异常与独立会计复核限制保持有效。

2026-10-12 在专用本机 PostgreSQL 14 验证 37 项 SQL 测试，通过范围包含权限隔离、角色权限、真实并发认领、全库及企业并发上限、持久轮转、过期恢复、发送后中断、晚结果防护、取消、重试预算、尝试历史不可变、受限后台账号创建、危险已有后台角色拒迁移，以及批次重试接受回执的幂等、本人可见、撤权不可见和不可变约束。SQLite 新库全迁移也通过。

测试文件为 `nexus_backend/tests/test_queue_postgres.py`，需显式提供 `ACCOUNTING_TEST_POSTGRES_URL`、`ACCOUNTING_TEST_DATABASE_URL`、`ACCOUNTING_TEST_WORKER_DATABASE_URL`。测试用随机企业与账号，保留不可变历史；每个用例完成时取消本用例剩余活跃任务，不删除业务资料。领取函数管理整个数据库，应使用独立测试数据库串行运行队列用例，避免其他活跃测试占用全库处理槽位。测试的立即可运行时间略早于当前时间，避免 Windows 与 WSL 时钟的毫秒偏差造成无关等待。

这些测试使用合成记录验证数据库与调度边界，不证明真实供应商 OCR 准确率、供应商限流策略或真实企业月度处理速度。试点仍限 2–3 家企业，分别授权模型处理并保留人工异常处理及会计复核后导出。
