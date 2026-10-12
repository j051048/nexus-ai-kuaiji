# 第四阶段 HTTP 契约：票据、异常与记账建议

所有接口位于 `/api`，沿用经验证的 `Authorization: Bearer <token>` 和 `X-Firm-Id`；无会话返回 401。每次读取和写入重新查询当前企业/部门/账套授权，不能把前端给出的 IDs 当作授权。前端切换范围或退出登录清空旧资料。金额使用非负十进制字符串，存储和交叉核对使用整数分。

## 配置与企业启用

| 方法 | 路径 | 内容/返回 |
| --- | --- | --- |
| GET | `/intelligence/config` | `{configured,vision_ready,journal_ready,provider_label,enabled_company_ids}`；仅暴露可见范围及供应商主机名，不返回密钥、完整接口 URL |
| GET | `/companies/{company_id}/intelligence/settings` | `{enabled,can_manage}` |
| PUT | `/companies/{company_id}/intelligence/settings` | `{enabled: boolean}`，企业管理权限校验并留审计 |

部署管理员显式配置供应商，企业管理者单独启用该企业。其他普通成员不能更改企业启用状态。企业默认关闭；未配置服务返回不可用状态，不伪造成功。浏览器不能配置供应商 URL、模型或密钥。

## 识别、核对与异常

| 方法 | 路径 | 请求 |
| --- | --- | --- |
| GET | `/business/events/{event_id}/intelligence` | 无 body；返回 `runs/findings/bank_candidates/bank_links/permissions/context_fingerprint` |
| POST | `/business/events/{event_id}/ocr` | `{attachment_id,expected_version,idempotency_key}` |
| POST | `/business/events/{event_id}/reconcile` | 无业务字段，可发送 `{}`；返回更新的事项核对详情 |
| GET | `/intelligence/exceptions` | 可选 `company_id`；`limit=1..100` 默认 50，`offset=0..100000` 默认 0；返回 `{findings,total}` |
| POST | `/intelligence/findings/{finding_id}/resolve` | `{reason}`，1–1000 字，保留处理人和依据 |

识别请求仅接收附件 ID，后端在当前事项授权下读取私有原件并校验摘要，再发送内联图片。附件不属于该事项或无权读取时拒绝。申请人、授权部门审批人、企业财务以及拥有具体账套编辑/复核授权的人员按事项状态处理识别；已冻结凭证来源不能重新处理。模型结果仅是待核对转录，字段引用须能对应页码和原文；原生 PDF 文字提供额外交叉验证。

每次 OCR 返回一个处理记录，状态为 `running/succeeded/failed/stale`。记录含模型、提示版本、输入版本、token 用量和受限公开错误；成功结果为 `{documents:[...]}`。失败记录保留 `error_code/error_detail`，`result=null`。正常完成的失败任务仍可返回 HTTP 200，因为任务本身已记录；调用方必须查看 `status`。同一幂等键和相同输入返回原任务；不同输入复用键返回 409，同一运行任务不会再次发送。没有自动重试或协议降级。

核对包括金额/税额关系、业务发生日、交易双方、企业税号、币种、证据完整性和重复票据等。问题分为 `blocking` 或 `warning`；授权财务/复核者记录原件核对或差异依据，申请人不能自行消除异常。新资料、识别或关联改变核对上下文时，旧处理依据不能消除新异常。

## 人工流水关联

| 方法 | 路径 | 请求 |
| --- | --- | --- |
| POST | `/business/events/{event_id}/bank-links` | `{bank_source_ids:[...],reason}`，1–20 个流水 ID |
| DELETE | `/business/events/{event_id}/bank-links/{source_id}` | JSON body `{reason}`，不使用 URL 查询参数保存解释 |

仅已移交事项、同账套授权的财务/会计处理者可关联开放草稿的银行来源。服务器验证同企业、同账套、方向、金额与关联占用；匹配建议不自动建立关系。关联更新防重复记账状态并重新核对；解除必须保留依据。已审核/导出的相关凭证不能通过解除关联绕过冻结保护。

## 模型分录与采用

| 方法 | 路径 | 请求 |
| --- | --- | --- |
| POST | `/books/{book_id}/drafts/{draft_id}/ai-proposals` | `{expected_version,idempotency_key}` |
| POST | `/intelligence/proposals/{run_id}/apply` | `{expected_version}` |

生成权限要求具体账套可编辑、企业已启用处理且当前草稿来自该事项。模型只获得当前事项、当前票据结果及可用科目；生成 `{description,lines,reasoning,evidence_refs,warnings}`。`lines` 中每行具有 `account_code/debit/credit`，禁止权限或审批字段。

采用要求当前授权、开放期间、未冻结草稿、版本和来源指纹匹配、可用科目、精确借贷平衡、来源总额与证据范围通过确定性校验，且阻断异常已处理。模型返回警示或错误分录时转人工，不直接改变审核状态。采用、会计审核及导出分别留记录；重复采用不会覆盖已经变更的草稿。原有 `/books/{book_id}/drafts/{draft_id}/approve` 与导出接口继续使用，最终通过会计复核后才能导出。

## 错误与运行界限

请求 schema 错误为 422；当前范围无授权为 403 或无可见记录时 404；资料版本、状态、幂等键冲突为 409；数据库和供应商服务不可用为 503。金额输入不接受浮点数、负数或指数。应用返回固定公开错误，不反射供应商正文、密钥或附件原文。

视觉请求限制单帧图片 2000 万像素、最长边 2000 像素、PDF 1–6 页、总图像 12 MiB；供应商响应 1 MiB、超时 1–90 秒、并发 1–2。PDF 子进程 30 秒超时，超限整体拒绝而非截断。更多配置见 [供应商接入](AI_PROVIDER_SETUP.md)。
