# 第十二阶段：员工一体化录入

员工在同一界面选择企业、部门和业务模板，上传多份原件或拍照，再核对字段并保存或提交。未配置票据模型时，手工填写和原件上传仍可完成全部业务审批流程。

## 草稿与原件

`intake_drafts` 保存允许缺少金额、日期、交易对方的准备资料。这些行不进入 `business_events`、流水或会计凭证，系统不会为了先上传票据创建假金额事项。模板切换保留摘要、金额、日期、交易对方和用途，仅调整模板要求的方向与付款状态。

未提交草稿和原件仅当前申请人可读，每次读取、恢复、修改和下载都重新检查企业成员授权。部门审批人、财务和会计不会提前看到尚未提交的准备资料。PostgreSQL 使用 FORCE RLS；申请人失权时，原件与识别输出停止显示。草稿保存在服务端，不向 localStorage 写入业务字段或原件。

原件使用既有私有附件存储、内容格式验证、SHA-256 完整性校验及 10 MB 单文件 / 50 MB 单事项 / 10 文件限制。照片采用手机后置摄像头输入。附件上传幂等复用同一草稿中相同内容；原件保留且不可覆盖。备份和恢复在同一数据库快照中纳入 `intake_attachments`，兼容尚未有该表的旧版本。

## AI 预填与费用

识别复用当前 OpenAI 兼容视觉供应商及请求并发限制。须同时满足企业处理开关和供应商配置；未满足时明确显示手工路径。每张单据逐字段展示金额、日期和交易对方候选及页码、原文依据，由员工选择采用并在提交前核对确认。多张单据保持独立，不盲目合计金额、猜测交易对方或把未知税额当零。非人民币金额及未通过原文依据校验的字段不提供采用按钮。

识别结果不写业务事实，也不审批或导出。真实模型调用通过既有 `MeterService` 预留/标记发送/结算；新 `model_costs.intake_run_id` 受来源约束保护，和业务事项 OCR 共用企业月度上限、未知费用保留、历史未决费用及告警统计。管理员可在既有模型费用页面核定未知费用。请求超时或用量未知时保持费用占用，同一附件的新请求必须先由管理员核定原请求费用，避免自动重复发送。进程中断三分钟后读取草稿会将仍执行中的录入识别记录标为失效，原件继续留存；失权或关闭企业开关后不能发布输出。

## 原子提交与审批

`POST /api/intake/drafts/{id}/complete` 严格校验现有 `BusinessPayload`、模板和人工字段确认，再在同一数据库事务内调用既有 `BusinessService.create`、关联所有原件、可选调用 `BusinessService.submit`，最后冻结录入完成记录。若缺少独立审批人、附件或有效授权，事务整体回滚，保留准备草稿，不产生半完成业务事项。重试相同完成请求返回同一事项。

成功识别的原件输出在同一事务转入业务事项的 OCR 证据记录，标注 `intake-transfer-*` 幂等键和 `cache_hit`，不再次调用模型或计费。后续财务核验继续执行现有完整证据检查，员工确认不能替代部门审批、企业财务核验或会计复核。已保存事项的退回修改仍使用现有事实版本与审批留痕。

部门账号默认进入待部门审批，财务账号默认进入待财务核验；每件事项说明下一步。手机使用独立事项详情和“返回事项列表”，录入表单占满可用屏幕，底部固定提交按钮。

## 接口与集成

构造 `IntakeService(db, storage, provider, meter=..., business=..., intelligence=...)` 并挂到 `app.state.intake`，注册 `build_intake_router(actor_dependency)`。应传入应用现有 BusinessService、IntelligenceService 与 MeterService 保持审批、自动草稿和费用策略一致。

| 接口 | 用途 |
|---|---|
| GET /api/intake/drafts | 当前用户获授权企业的未完成草稿，最多 50 条 |
| POST /api/intake/drafts | 允许不完整的字段和固定企业/部门，带幂等键 |
| GET /api/intake/drafts/{id} | 精确读取及恢复，不依赖列表首屏 |
| PATCH /api/intake/drafts/{id} | 带期望版本保存字段及模板 |
| POST /api/intake/drafts/{id}/attachments | 带期望版本上传一个原件，前端顺序上传多个 |
| GET /api/intake/attachments/{id}/content | 实时授权和完整性检查后下载 |
| POST /api/intake/drafts/{id}/attachments/{attachment}/recognize | 带期望版本、处理幂等键的受预算识别 |
| POST /api/intake/drafts/{id}/complete | 人工字段确认后完整保存，submit=true 提交审批 |

`BusinessWorkspace` 支持 `initialIntakeId`、`initialNew`、`initialTemplate`。精确待办跳转使用 `initialIntakeId`，快捷录入使用 `initialNew=true` 与对应模板；原有 `initialEventId` 用于已保存事项。

数据库迁移：PostgreSQL 059 / SQLite 044。SQLite 在迁移框架已关闭外键检查的事务内重建 model_costs，仅放宽旧来源列支持经过外键和互斥约束的新来源，复制全部既有记录、索引和审计/监控触发器，迁移结束检查所有外键。

验证使用虚构企业、私有真实文件及显式合成模型输出，不连接真实供应商或客户数据。真实企业仍需验证手机照片清晰度、供应商结构化契约、组织审批配置和完整月份的交付差异。
