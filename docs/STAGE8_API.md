# 第八阶段：集中复核、异常补件与企业学习

本阶段面向少数试点企业。后台和规则产生的结果仍为待审核草稿；只有当前获授权的会计完整复核后才能导出 CSV。本站内待办、审核样例及规则发布不代表外部财务系统已经接收或正式过账。

全部接口带当前登录令牌，并按现有会话选择事务所。请求体禁止额外字段；金额沿用人民币精确字符串，业务状态、事实版本、草稿版本和规则状态版本分别校验。客户端保存的旧角色、任务指派和旧预览都不构成持续授权。

## 跨企业批量复核

| 方法和路径 | 用途 |
| --- | --- |
| `GET /api/review/drafts` | 当前有复核权限账套的待审核列表 |
| `POST /api/review/previews` | 保存所选凭证的逐张核对预览 |
| `GET /api/review/previews/{preview_id}` | 读取本人创建且仍有全部相关账套权限的预览 |
| `POST /api/review/previews/{preview_id}/confirm` | 明确执行人工批量审核，返回每个账套的结果 |

列表参数支持 `company_id`、`period`、`template=all|bank|reimbursement|procurement_expense|daily_cashflow`、`risk=all|basic_clear|basic_exception`、`limit=1..100`、`offset`。列表的基础检查分组用于定位待办，不能替代正式审核。

预览请求示例：

```json
{
  "items": [
    {"book_id": "book-a", "draft_id": "draft-a", "expected_version": 3},
    {"book_id": "book-b", "draft_id": "draft-b", "expected_version": 1}
  ]
}
```

单批最多 100 张，凭证不可重复。服务先检查全部账套的当前复核授权，再生成预览；混入无权访问的凭证时整次请求拒绝。预览包含分录、金额、来源引用、事实版本、异常和当前证据指纹，默认有效期 30 分钟。预览本身不改变草稿状态。

确认请求：

```json
{"idempotency_key": "review-confirm-unique-key"}
```

提交时再次检查当前权限、期间、凭证版本、科目与辅助核算、模板及已发布规则、证据、异常补件状态和本人申请限制。同一账套内先核对全部所选凭证，再在一个事务内审核、捕获样例并写入回执；任何一张失败，该账套整批回滚。不同账套分别执行，返回 `completed|partial|failed`、`approved_count`、`failed_book_count` 与逐账套 `results`。

一份预览只对应一个确认命令。同键重试读取原回执，已成功账套不重复审核；在进程中断后可恢复没有回执的账套。失败结果也保留回执，修正后须创建新预览。重试仍检查当前所有相关账套授权。审核通过后保留人工导出操作，不能通过批量确认调用绕过导出校验。

## 异常指派与补件

| 方法和路径 | 用途 |
| --- | --- |
| `GET /api/cases` | 按当前权限分页查看待办 |
| `POST /api/cases` | 创建内部异常或公开补件请求 |
| `GET /api/cases/{case_id}` | 当前权限对应的任务详情 |
| `GET /api/cases/{case_id}/assignees` | 当前有资格处理或收集资料的账号 |
| `POST /api/cases/{case_id}/assign` | 指派、调整期限与优先级 |
| `POST /api/cases/{case_id}/comments` | 追加内部或对请求人的处理说明 |
| `POST /api/cases/{case_id}/attachments` | 上传当前补件响应的私有原件 |
| `GET /api/cases/attachments/{attachment_id}/content` | 授权下载并校验附件内容 |
| `POST /api/cases/{case_id}/respond` | 提交完整字段与资料，等待独立核验 |
| `POST /api/cases/{case_id}/review-response` | 授权处理人接受或退回补件 |
| `POST /api/cases/{case_id}/close` | 记录依据并关闭已处理待办 |

列表支持企业、账套、事项、状态、`overdue`、`mine` 和分页，按优先级、期限及更新时间排列。创建时指定业务 `event_id`，或者同账套的 `book_id` 与 `draft_id`；源归属由服务端读取，不能伪造其他企业的来源。

`kind=supplement` 绑定原业务事项，接收人是原申请人；初始状态 `awaiting_info`。`kind=exception` 是内部调查，初始状态 `open`；银行来源用内部异常任务处理。创建可填写 `title`、`reason`、`priority=normal|high|urgent`、`due_on`、`required_fields`、`attachment_required`、`idempotency_key`。

修改命令均携带 `expected_version`、`idempotency_key` 和处理 `reason`；同一账号和事务所内幂等键不能复用于不同命令。指派增加 `assignee_id`，评论增加 `audience=internal|requester`，提交补件增加 `fields` 对象。字段未补齐或要求的原件未上传时不能提交。

### 权限投影

- 企业财务或账套复核人员按当前权限处理任务。指派不能增加其原本没有的企业或账套访问权限。
- 员工仅能看到本人收到的公开补件请求、公开历史、本人响应与上传资料；不会获得账套 ID、凭证 ID、内部指派及会计内部备注。
- 银行异常可指派给同企业且有该账套编辑权的财务人员收集资料。这类人员获得收集投影：通用补件标题、公开请求、当前银行来源摘要、本人响应；无权据此读取内部调查内容、重新指派、关闭任务或核验本人响应。
- 核验人不能是该响应的提交人。失去企业或账套权限后，旧任务、旧下载链接和旧回执不能继续开放原件。

### 原件与接受流程

上传为 multipart `file`，查询参数带 `expected_version`、`idempotency_key`。格式和存储沿用现有私有票据存储：PDF、PNG、JPEG、WebP，单文件最大 10 MB；每次响应最多 10 件、50 MB，任务累计 100 MB。重复上传命令核对内容哈希与文件名，重复写入的临时文件清理。

核验请求示例：

```json
{
  "expected_version": 4,
  "idempotency_key": "case-accept-unique-key",
  "reason": "已独立核对清晰扫描与原业务事项一致",
  "decision": "accept",
  "supersedes_attachment_ids": ["old-business-attachment"]
}
```

接受业务补件会追加业务附件、保留响应及核验记录，并递增业务 `version` 和 `fact_version`。字段说明作为独立接受上下文保存，不改原始业务 payload、提交快照或历史部门/财务审批。新的事实和证据版本使旧分录候选或审核预览失效。

明确替代只能在接受同一业务事项的新附件时指定；必须实际上传新原件。旧原件不删除，替代关系留痕，后续 OCR 核对和费用建议使用当前有效原件集合。银行补件接受后追加来源证据并递增未审核草稿版本，不改原始银行来源。

响应状态为 `draft → submitted → accepted|returned`。接受后任务进入 `in_review`，退回回到补件等待状态。关闭待办不能自动解决金额、税额、重复单据或银行核对异常；所有未关闭任务继续阻断关联凭证的审核及导出。已审核、已导出或已关期间不再接受新的处理写入。

## 审核样例与版本化企业规则

接口前缀为 `/api/books/{book_id}/learning`。

| 方法和相对路径 | 用途 |
| --- | --- |
| `GET /` | 当前账套样例分页与最近规则列表 |
| `GET /samples/{sample_id}` | 原/最终分录、修正理由及审核来源 |
| `POST /rules` | 从已审核正例保存待发布规则 |
| `PUT /rules/{rule_id}` | 创建下一份不可覆写的规则定义 |
| `GET /rules/{rule_id}` | 当前规则与历史版本 |
| `POST /rules/{rule_id}/evaluate` | 使用实际已审核反例并核对已有样例 |
| `POST /rules/{rule_id}/publish` | 写明依据并显式发布当前通过评估的版本 |
| `POST /rules/{rule_id}/retire` | 写明原因并停止后续使用 |

样例只在完整审核通过的同一事务内自动捕获，包含来源事实、原/最终分录、修改者和理由、审核版本、证据指纹。它不是未经审核模型输出的训练集。修正能形成无歧义的固定公式时生成待发布建议；未知税额或复杂分录只保留样例，不推测税额。

规则明确限定当前事务所、企业、账套与期间，不能通过其他账套授权开放原样例。`source_kind=business|bank` 分开匹配，交易对方按精确名称判断。业务规则限定 `business_type`、`direction`、`category`、`payment_status` 和 `counterparty`；银行规则使用 `daily_cashflow`、已支付、精确交易对方与方向，`category=null`。金额字段只允许 `gross|net|tax`，不能写执行表达式或固定样例金额。拆税必须有已核对的净额及税额，科目和辅助核算沿用客户配置。

创建带 `definition`、`sample_ids` 和 `reason`；修订另带 `expected_version` 与 `expected_state_version`。修订保存为 `draft`，暂停原规则，须重新评估发布。定义历史不可覆写。

评估带 `expected_version` 和 `negative_sample_ids`。正反例必须是同账套不同的已审核来源；评估核对本账套全部已有审核样例，每个命中处理必须与已审核最终分录及辅助项一致，已知冲突或命中反例会阻断发布。本期 MVP 单次评估最多 2,000 条已审核样例；这是范围限制，不是准确率结论。

发布带 `expected_version`、`expected_state_version`、`evaluation_id` 和 `reason`。发布时重新检查当前角色、期间、科目、样例及数据库评估上下文。历史评估、伪报告或一次人工误改都不能自动发布；停用与修订立即影响后续任务策略指纹。没有跨企业全局学习或在线模型训练。

后台常规业务按“已发布企业规则 → 明确客户模板 → 必要时模型”生成草稿。企业规则成功回执使用 `result_rule_id/result_rule_version`，与客户模板回执分开保存；租约、事实、草稿版本、人工修改标记及当前异常继续校验。会计已修改的草稿不会被后台覆盖。银行来源建议也只消费本账套已发布的银行规则。最终审核和导出流程保持人工控制。

## 试点检查

先配置试点客户真实科目与模板，选取会计完整核实过的正反例。验证两家企业不可互读原件、任务和样例，正常批次确认后仍未自动导出；主动制造版本变化、补件替代、规则停用和进程重启，核对旧操作不会覆盖新事实。真实模型供应商、企业业务与完整月结对照另行验收，本说明不包含验证统计或容量承诺。
