# 第十三阶段：旧账迁移接口与部署

## 接管范围

历史资料放在独立的 `migration_*` 表中。原件、科目与辅助映射、解析记录、差异报告、独立验收和接管快照均保留版本。客户原软件仍承担正式账本；初始化历史余额不会产生当前 `sources`、`drafts` 或 `exports`。当前仅支持人民币、完整连续月份、月首接管。

当前实现中性 `nexus.legacy/v1` JSON 包，以及会计明确确认列映射的 CSV / `.xlsx`。选择“畅捷通 T+ / 用友 / 金蝶”仅记录来源；`vendor_native_verified=false`。不支持直接上传厂商数据库备份，也没有启用真实 ERP 连接。合成示例显式标为 `SYNTHETIC`，不能用作厂商格式验收。

## API

以下路径前缀为 `/api/books/{book_id}/legacy-migrations`。所有读取与操作重新检查当前用户、事务所、有效客户关系、当前账套与项目的明确历史授权；资源 ID 不授予权限。原件和报告响应禁止缓存。

| 方法与路径 | 用途 |
| --- | --- |
| GET / POST `/` | 授权过滤后分页 / 创建空的开放目标月份迁移项目 |
| GET `/template` | 下载指定范围的合成中性模板 |
| GET `/{project_id}` | 当前版本、文件、进度、校验、验收与可执行权限 |
| POST `/{project_id}/files` | multipart 原件、类别、列映射、版本、依据与幂等键 |
| GET `/{project_id}/files/{file_id}` | 当前历史权限内的私有原件，读取前复算 SHA-256 |
| GET `/{project_id}/files/{file_id}/inspection` | 表头、前五行预览和列映射建议 |
| POST `/{project_id}/files/{file_id}/remap` | 保留旧件，新增列映射版本与替代关系 |
| GET `/{project_id}/records` | 按类别与月份过滤的历史记录分页 |
| POST `/{project_id}/mapping` | 合并本页科目、辅助档案和应收应付范围，形成完整新版本 |
| POST `/{project_id}/parse` / `/validate` | 独立持久后台解析 / 完整复算与校验 |
| GET `/{project_id}/issues` / `/report` | 分页差异与完整复核报告 |
| GET `/{project_id}/jobs/{job_id}` | 授权过滤的任务进度，不返回领取凭据 |
| POST `/{project_id}/jobs/{job_id}/retry` / `/cancel` | 保留原执行记录后排队重试 / 取消 |
| POST `/{project_id}/accept` | 另一名会计独立验收当前报告与原件 |
| POST `/{project_id}/activate` | 账套负责人确认接管，原子冻结快照并初始化明确映射 |
| GET / POST `/{project_id}/access` | 当前可授权成员 / 授予历史项目职责 |
| DELETE `/{project_id}/access/{user_id}` | 撤销历史资料授权，保留审计依据 |

通用命令包含 `expected_version`、`idempotency_key` 和 `reason`。验收与接管还需要报告的 `validation_id`、`basis_token` 及明确的 `confirmed=true`。文件、映射、期间、科目、主数据或授权变化后旧报告失效；重放幂等请求不得更改请求内容。资料准备人及最新映射作者不能验收自己的成果。

表格必须确认列映射、日期格式、借贷余额口径和原凭证状态。多行凭证按原标识与分录序号组成完整凭证；红字、冲销、作废、未记账分别保留。缺失金额、重复身份异内容、借贷不平、余额连续性、凭证与月发生额不一致、来源控制总数及未结明细差异均进入复核。已有科目映射须类别、余额方向与辅助维度一致；非记账上级科目只归档，不变成可记账目标科目。原软件控制总数由用户提供，系统复算一致不代表已独立验证原软件数据完整。

MVP 有界处理：单件最多 10 MB，每项目最多 64 个原件、合计 64 MB，校验最多 50,000 条规范化记录，列表默认分页。超限需缩小范围或先扩容评测，不能静默截断后接管。

## 历史规则与未结清偿

`/{project_id}/sample-candidates` 提供候选创建与查询，子路径 `/{candidate_id}/review`、`/shadow`、`/publish`、`/retire` 分别保留独立审核、正反例回放、发布与停用记录。只开放事实明确、企业往来方已映射的简单银行收支规则。原软件“已记账”状态不等于本系统的人工学习审核；历史样例不写入普通已审核样例表。复杂、红字或冲销处理继续留给会计。

`/{project_id}/settlements/items` 查询历史未结余额，`/settlements/banks?bank_book_id=...` 选择同长期账套接管后月份的当前银行流水，`/settlements/previews` 生成确定性清偿分录，`/settlements/previews/{id}/confirm` 原子确认分配并替代未审核草稿。整笔银行金额须完整分配给同一往来方的同类应收或应付，不能超过未结余额。只允许另一名会计在未审核且版本未变化时通过 `/settlements/commands/{id}/reverse` 撤销并原子还原草稿。

银行清偿不再次计入费用或收入，也不能成为普通分类规则的学习依据。新草稿仍须会计复核后导出。隐藏其他月份的收付明细不会恢复已经用掉的历史余额。

## 独立后台与恢复

先备份数据库和私有原件，再显式执行数据库迁移。PostgreSQL API 使用受限运行账号；迁移任务使用既有专用 `accounting_worker` 登录，不能让 API 账号领取任务。部署指引见 [数据库接入](POSTGRES_SETUP.md) 和 [备份恢复](BACKUP_RECOVERY.md)。

```powershell
$env:PYTHONPATH = 'nexus_backend'
python -m app.legacy_migration.worker --env-file .env.accounting
```

配置 `ACCOUNTING_WORKER_DATABASE_URL` 与 `ACCOUNTING_ATTACHMENT_ROOT` 为共享持久私有存储；`ACCOUNTING_MIGRATION_CONCURRENCY` 默认为 1，可取 1–2。生产连接必须 PostgreSQL、独立受限账号及显式 TLS。迁移与 OCR 使用不同队列表和领取逻辑，迁移不会占用模型任务槽或调用供应商。

最多两件迁移工作同时运行，每企业最多一件，按企业轮转领取。解析每 250 条提交检查点，租约过期从已提交位置恢复，旧租约无法覆盖新结果。校验报告已提交、任务回执未完成时重启，会复验原件和依据后复用同一不可变报告；输入变化则旧任务失效，不覆盖报告。原件上传的数据库提交回执不确定时保留原件，使用相同请求编号恢复，不能删除可能已入库的证据。权限、输入版本或原件替代后旧任务失效，不自动替换执行者身份。失败重试保留历史，验证未完成不能验收。

本地 SQLite 演示随应用启动迁移 worker；`ACCOUNTING_QUEUE_EMBEDDED_WORKER=0` 可停用嵌入后台。PostgreSQL 试点使用上述独立进程，API 不启动嵌入后台。统一待办中的“旧账迁移”按当前角色显示准备资料、处理差异、独立验收、确认接管或后台进度，按项目精确定位。

## 真实试点验收

先由客户和会计确认实际 T+ 产品、版本、导出范围和样本，再形成版本明确的格式适配与对照报告。首次接管应逐月核对原软件凭证数、状态、借贷合计、科目及辅助余额、应收应付明细，并做原件与数据库一起恢复的演练。合成测试通过不能替代实际企业数据验证，也不能据此承诺一个会计管理 200–500 家企业。
