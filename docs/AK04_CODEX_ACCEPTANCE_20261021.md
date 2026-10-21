# AK04 统一执行记录、错误分类与人工接手验收

日期：2026-10-21。基线：`baf3f414187a198dbde1c41f8a90e42dee2629ca` 与已有未提交修改。

## 结论

**AK04 本轮本地接入门槛通过。** 助手、业务票据 OCR、分录建议及员工附件预填复用现有持久记录生成统一执行视图；助手及票据页面增加按需查看和显式人工接手入口。接手创建既有内部异常任务，不修改模型运行终态，不重发模型，不替代会计复核。

本地后端按 test ID 保留最后一次明确复验：**447 passed / 0 failed / 0 skipped**；前端受影响组合 **67 passed**；实际 Chromium 组件验收 **11 项通过**；最终同期隔离 PostgreSQL 14 **41 passed / 0 skipped**。后端为分批回归去重，不是一次全仓测试或生产验收。

未提交、推送、部署、执行生产迁移或调用真实模型。没有修改 C01、账务/关期实现、数据库迁移、PG 投影或原有 UX 改造。新增路由复用原验证身份依赖，会计复核后导出保持。

## 1. 记录来源及发布边界

`app/execution/records.py` 是可部署的纯投影实现，后端不引用 `tools/ai_inference_lab/trace.py`。DL03 的隔离投影思路可借鉴，其离线 Principal、状态机不作为生产权限来源。

| 记录 | 权威来源 | AK04 行为 |
|---|---|---|
| 助手 | assistant_turns、model_costs | 关联 turn、保存的输入/模型指纹、发送标记、终态与费用；成功事实/弃答机器码持久化 |
| OCR/分录建议 | ai_runs、ocr/proposal jobs/attempts、model_costs、当前 findings | 统一执行标识、校验/异常及有权诊断者的运行关联 |
| 员工预填 | intake_runs/checks、本人 intake 草稿、model_costs | 本人私有记录与核对建议；不向会计自动公开尚未确认的准备资料 |
| 人工工作 | case_tasks/commands/history | 复用既有指派、补件、处理、关闭和审计，不另造任务状态机 |

- source status 保留原含义；`succeeded` 只代表该次处理完成。
- 助手内部 `ak04_fact_*` 完成码区分 verified、missing、conflict、invalid、unsupported、accountant；公开成功响应原 error_code 仍为空，reply 保留 answer/citation_ids/proposals 三字段。
- 时间线只显示已有创建、发送标记和结束时间；不虚构模型收到请求、各步耗时或逐 attempt 阶段。
- 诊断输入指纹与模型指纹分别命名。完整 ContextPack 哈希没有独立持久字段，明确显示 null，不把 input_fingerprint 冒充 ContextPack 内容哈希。
- 助手当前可核验 claims 的既有白名单、独立票据证据与模板金额约束继续保留；统一视图不赋予新的审核或账务权限。

## 2. 错误、费用与权限

统一分类为权限/范围、配置/政策/预算、输入/上下文、供应商/传输、输出校验、取消及后台内部失败。未知 code 和原 error_detail 不直接回显，使用服务端固定说明；供应商响应、密钥、提示词、堆栈不进入普通执行视图。

- 超时/断连且无明确未发送证明时保守显示结果待核对；`queue_interrupted` 本身不证明已经发送。
- 已知正用量、显式零和未知费用分别保留；费用来自现有 model_costs，不另行估算、结算或清零。
- 错误分类仅解释既有事实，不替换现有 retry_is_safe、租约、幂等及供应商发送护栏。
- 缓存复用、本地 XML 解析和缺少费用记录分别呈现；缺记录不等于免费。
- 当前资源授权先于投影与诊断查询；诊断沿用事务所 owner/admin 权限。普通员工、企业角色和普通会计不获得 provider/model/usage 或技术费用字段，原直接执行/幂等返回路径同时脱敏。
- 所有引用、账套权限、会话私有及 intake 本人权限继续由服务端和 RLS 执行。客户端不能传 can_diagnose 或任意 Principal。

## 3. 显式人工接手

新增统一入口：

```text
GET  /api/executions/{chat|ocr|journal|intake}/{id}
POST /api/executions/{chat|ocr|journal|intake}/{id}/handoff
```

保留来源专用读取入口：助手 turn execution、业务 event/run execution、intake draft/run execution。

POST 只接受 `expected_fingerprint/reason/idempotency_key`；企业、事项、账套、凭证、处理人均不能从请求正文指定。前端显示的 supported/can_create 不是写权限凭证。

- 接手目标从当前已授权来源推导。单业务事项、明确来源凭证可以接入；多事项/企业汇总或未发布 intake 不猜测目标。
- 指纹覆盖接手目标的当前业务/事实版本、附件摘要、映射、草稿版本/状态、来源指纹及期间状态；不是整份助手快照中所有账套汇总的签名。
- 在既有 CaseService 公司写锁事务中通过内部 source_guard 重查来源、当前授权和指纹；然后保留原 _manage/_editable、RLS、命令幂等、历史和审计。
- queued/running/retry_wait 要先使用原任务取消流程确认状态；接手接口不隐式取消或重发。
- 同键同请求返回同一 case；同键不同运行/理由冲突。撤权、资料变化或期间不可编辑时，即使是原键重放也拒绝。
- 正文仅含安全执行引用、目标指纹与人工主动填写的依据，不复制私有会话问题、模型回复、预填字段或原件全文。
- 关闭 case 不会解除原件核验、建议采用、凭证审核和导出闸门。

## 4. 页面与手机体验

新增 `ExecutionRecordPanel`，复用现有助手回复和票据处理记录区域。默认折叠、零自动请求；只在展开或重新查看时 GET，没有新增轮询。

- 默认显示处理结果、资料核对、下一步和待核对费用提示；技术摘要仅在服务端实际返回 diagnostics 时出现。
- 合法会计填写依据后明确提交，成功显示任务编号；员工显示联系会计说明。
- 使用用户/事务所/角色/企业/运行/上下文作用域键，AbortController、epoch 和 API 当前作用域检查阻止旧响应回填。
- 401/403/404 清记录、理由、任务编号及请求 intent；403 保留原有全局撤权清理，不压制安全事件。
- 409/503 保留同范围理由，清失效预览，要求重新查看。连续同 body 重试复用原键；页面卸载、真实范围变化或用户改变请求不承诺沿用旧键。
- 错误提示与输入关联；375px 无横向溢出。全局导航、角色首页和原工作进度逻辑未改。

## 5. 先失败后修复与扩展回归

证据根目录：`tmp_test/ak04-codex-20261021/`。

| 证据 | 结果与处理 |
|---|---|
| records-red.log | 18 项新能力契约缺失；不是宣称原来已有此 API 却出错 |
| assistant/red.log | 新实际 worker/服务/HTTP 契约红测；新增夹具误用不计产品缺陷 |
| tickets/red.xml、red-error-code.xml | 7 项接入缺失；2 项错误码原文泄漏断言失败后净化 |
| handoff-red.log、review-red.log | 接手缺失；补真实草稿变更、锁内重检、chat 重放与费用不变验收。不存在的测试角色/夹具属性错误已纠正，不计产品缺陷 |
| frontend/red.log、red-revoked.log | 新能力导入失败；随后 401/403/404 刷新仍保留私密状态的 3 项真实红测，修复后通过 |
| facts-template-final-v2.xml | 163 passed / 5 failed：3 项 XML 标签兼容影响、2 项员工诊断公开契约变化；对应修复并整文件重跑 |
| core-final.xml | 104 passed / 11 failed：旧 journal 进程夹具未先独立核对原件，未到达目标传输故障点；补真实会计原件确认后完整 42 项进程矩阵通过 |

XML 修复只把本地解析器使用的三条完整固定 FIELD_PATHS 加入金额标签表。没有放开裸金额、任意 XML 后缀、换列或金额/标签截断；新增三项路径换列负向，继续保留未验签/验真标记。旧员工 provider 断言改为公开脱敏，同时增加数据库原始 provenance 保留断言。

没有放松原件/权限闸门以使进程故障测试通过。Windows 临时目录问题沿用现有离线 runner 的独立可写目录；未变更数据库业务 SQL。

## 6. 最终验证与归属

| 范围 | 最终证据 |
|---|---|
| 本地后端受影响回归去重 | 447 passed / 0 failed / 0 skipped；test-evidence-summary.json 保存每项替换关系及 XML 哈希 |
| AK01 独立进程故障矩阵 | attempts-final.xml：42 passed；含真实本机 HTTP、持久库、独立解释器/竞争/崩溃/取消/撤权 |
| XML 与 AK03 票据 | xml-final.xml：47 passed；完整结构化文件与字段来源负向 |
| 既有 cases/HTTP 与接手 | handoff-regression.log：47 passed；新增后续用例由 core-final/最终去重覆盖 |
| 前端受影响组合 | 67 passed；typecheck、ESLint、Prettier、全构建通过 |
| 实际浏览器 | 11 项检查，外域请求 0、页面异常 0；桌面及 375px 截图 |
| 最终同期受限 PostgreSQL 14 | 41 passed / 0 skipped；含 XML、本期双连接并发同键接手、费用、RLS、独立原件核验、会计复核后导出 |

PG 细节见 [AK04_PG_LOCAL_VALIDATION.md](AK04_PG_LOCAL_VALIDATION.md)。本机一次性 15438 已停止、明文测试凭据已删除，原 5432 未改动。前端临时本机验收服务也已停止。测试存在既有 Starlette 弃用提示及构建大 chunk 提示，未静音或跳过失败。

接手时冻结 1453 个可读文件及原 diff；两个既有不可读临时文件未处理。`ownership-check.json` 中 unowned_changes 为空，既有 C01、迁移、投影、数据库、App/UX 等并行变更保留。最终文件白名单和 SHA-256 见 final-source-hashes.json。

未验证真实供应商效果/账单、PG17、线上整站身份与部署或 C01 联合放行；本轮通过不替代这些门槛。可以继续 AK05 调度与性能的本地开发。
