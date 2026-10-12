# 辅助记账 API 契约 · v0.2

范围：中国大陆企业、人民币、会计人工审核后导出通用凭证 CSV；没有正式总账、税务计算或已接入 LLM。默认示例数据均为虚构。

- 启动：`cd nexus_backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`。
- 所有金额均为十进制**字符串**，最多两位小数，不接受浮点 JSON、指数、负数；输出统一两位小数。
- 默认本地模式：`GET /api/session` 返回 `{token,mode:"local",firm_name,user_name}`。只允许回环访问，令牌只在当前进程内有效，2 小时过期。真实模式该接口 404，必须使用 Supabase access token。除了 `/api/auth/config`、`/api/health`，业务 `/api` 接口必须携带 `Authorization: Bearer <token>`。
- 错误统一 `{detail:"中文信息"}`。参数错误 422，权限/关闭期间 403，版本/重复内容冲突 409，缺失 404。
- 开发前端通过 Vite `/api` 代理访问。跨域仅接受 `ACCOUNTING_ALLOWED_ORIGINS` 明确白名单，不接受通配符。

## 接口

`GET /api/workbench`
```json
{"books":[{"id":"...","name":"...","period":"2026-10","period_status":"open","pending_count":2,"exception_count":1,"approved_count":0}],"stats":{"book_count":1,"pending_count":2,"exception_count":1,"approved_count":0}}
```
exception_count 是待审核草稿中有 issues 的数量；pending_count 包含异常草稿。approved_count 包含 approved 和 exported。

`POST /api/books` `{name,period:"2026-10"}` → book；自动生成当前本地用户授权、期间和演示科目。

`GET /api/books/{book_id}` → `{book,accounts:[{code,name}],sources:[source],drafts:[draft]}`。

source：`{id,external_id,date,description,counterparty,amount:"100.00",direction:"expense"|"income",evidence_ref,status:"unprocessed"|"draft"|"approved"|"exported"}`。

draft：`{id,source_id,description,status:"draft"|"approved"|"exported",version,explanation,issues:string[],lines:[{account_code,debit:"100.00",credit:"0.00"}],source_date,source_amount,source_description,evidence_ref}`。

`POST /api/books/{book_id}/imports` `{source_name,rows:[{external_id,date:"2026-10-01",description,counterparty,amount:"100.00",direction,evidence_ref}]}` → `{imported,duplicates}`。相同账套 external_id + 相同规范化内容视为重复；相同 ID 不同内容返回 409，整批回滚。最大 1000 行。

`external_id` 须在账套内唯一；同一账套多个银行账户请使用 `银行账户标识:流水编号` 前缀。

`POST /api/books/{book_id}/sources/{source_id}/evidence` `{evidence_ref:"..."}` → source。追加证据引用与审计记录，保留原始导入内容和指纹；同时使对应草稿版本 +1，前端应重新读取账套。已审核、已导出或已关期间不可补充。引用仅为会计提供的资料定位，系统没有核验附件真实性。

`POST /api/books/{book_id}/suggestions` `{}` → `{created,exceptions}`。固定规则产生草稿与解释，未匹配时仅银行行并标异常。缺少证据的草稿禁止审核，可通过补证接口解决。

`PATCH /api/books/{book_id}/drafts/{draft_id}` `{expected_version,description,lines:[{account_code,debit,credit}]}` → draft。支持人工填补未匹配科目；不允许修改已审核草稿。

`POST /api/books/{book_id}/drafts/{draft_id}/approve` `{expected_version}` → draft。必须借贷平衡、合计与来源金额相等、银行方向正确、有来源证据、科目有效、期间开放。只支持有 reviewer 权限者。

`POST /api/books/{book_id}/exports` `{draft_ids:["..."],idempotency_key:"..."}` → `{id,csv,count,filename}`。仅批准凭证可导出，事务内冻结为 exported；相同幂等键与相同 ID 集返回同一收据，不同 ID 集返回 409。CSV 文本字段有公式注入防护。CSV 是通用交换格式，尚未对接任何财务软件。

`GET /api/books/{book_id}/exports` → `{exports:[{id,count,filename,created_at}]}`，按新到旧排列。

`GET /api/books/{book_id}/exports/{export_id}` → `{id,csv,count,filename}`。允许刷新页面或关闭期间后重新下载历史收据，不改变凭证状态。历史 CSV 下载仍须账套复核/导出权限；列表元数据只须账套读取权限。

`POST /api/books/{book_id}/periods/{period}/close` `{}` → `{period,status:"closed"}`。测试及本地验证用；已关期间不可导入、建议、修改、审核或新建导出。

## 本地数据与边界

默认数据库为 `nexus_backend/data/accounting.db`，可用 `ACCOUNTING_DB_PATH` 指定演示路径；`ACCOUNTING_DATABASE_URL` 指定 PostgreSQL 受限运行连接。SQLite 原有数据自动升级保留，PostgreSQL 需显式迁移。本地演示身份拒绝非本地环境；真实登录要求 PostgreSQL。生产要求 `ACCOUNTING_AUTH_MODE=supabase`、数据库 TLS 和明确域名/HTTPS 来源。只有本地演示模式初始化本地用户和示例账套。

账套访问在 API 调用服务以及服务事务内均校验当前数据库授权。真实模式还使用 PostgreSQL RLS 二次隔离；员工身份、企业财务身份和事务所会计身份分别定义，不依赖 JWT metadata 的业务角色。

工作台总览使用固定数量的批量查询：目前 SQLite 下 6 个 SELECT（含身份与角色查询）加 BEGIN / COMMIT 共 8 条 SQL。合成基准仅验证总览查询，不代表真实代账产能。单账套详情仍需后续分页。

默认开发来源为 HTTP 的 localhost / 127.0.0.1:8080 与测试端口 18080。本地模式须绑定回环地址；真实环境通过显式域名和来源配置运行。

## 真实身份与授权管理

`GET /api/auth/config` → `{mode:"local"|"supabase",supabase_url?,publishable_key?}`，公开且只包含可公开的登录配置。

`GET /api/me` → `{mode,user:{id,name,email},firms:[{id,name,role}],current_firm_id,permissions:{manage_firm,create_company,create_book}}`。Supabase 首次验证成功后同步用户资料，不授予任何既有事务所权限。无授权时仍返回空 `firms`，可以创建本人独立事务所。客户端以 `X-Firm-Id` 选择事务所，伪造或已撤销的范围返回 403，不能回退到其他客户资料。

`POST /api/firms` `{name}` → `{id,name,role:"owner"}`；`GET/POST /api/companies` 列表/创建客户企业；企业体 `{name,tax_id?}`。`POST /api/books` 增加 `company_id`，缺省时创建同名客户企业以兼容本地首轮流程。创建客户与账套须事务所 owner/admin。

工作台及详情中的 book 增加 `company_id/company_name/permissions:{edit,review,export,manage}`。事务所 owner/admin 可管理所属账套；其余成员采用事务所职责与具体账套角色的交集。普通企业员工没有账本读取权；企业财务须显式账套授权。业务权限每次从数据库读取。

| 接口 | 请求或结果 | 权限 |
| --- | --- | --- |
| `GET /api/firm/members` | `{members:[{user_id,name,email,role}]}` | 事务所 owner/admin |
| `PUT /api/firm/members` | `{email,role:admin/operator/reviewer/viewer}` | 目标须先登录；不变更 owner 或当前管理人的自身职责 |
| `DELETE /api/firm/members/{id}` | `{revoked:true}` | 不撤销 owner/当前账号；同时清除该事务所账套授权 |
| `GET /api/books/{id}/members` | `{members:[{user_id,name,email,role}]}` | 当前账套管理人 |
| `PUT /api/books/{id}/members/{user_id}` | `{role:editor/reviewer/viewer}` | 目标须为当前事务所成员或该企业财务身份；企业管理员不能改事务所人员分工 |
| `DELETE /api/books/{id}/members/{user_id}` | `{revoked:true}` | 当前账套管理人 |
| `GET /api/companies/{id}/members` | `{members:[{user_id,name,email,role,department_id}]}` | 企业或事务所管理员 |
| `PUT /api/companies/{id}/members` | `{email,role:owner/admin/finance/employee,department_id?}` | 企业管理员不可改自身职责、授予 owner、变更事务所人员企业身份 |
| `DELETE /api/companies/{id}/members/{user_id}` | `{revoked:true}` | 企业或事务所管理员；保护 owner 和当前管理身份 |
| `GET/POST /api/companies/{id}/departments` | 列表 `{departments:[{id,name}]}` / 创建 `{name}` | 企业范围内读取 / 管理人创建 |

角色及部门修改追加审计，与授权变更在同一事务中提交。部门不可跨企业分配。第三阶段已增加业务模板、私有附件、部门授权、分级审批及移交凭证草稿，详见 [第三阶段契约](../docs/STAGE3_CONTRACT.md)；部门归属本身不授予审批权。
