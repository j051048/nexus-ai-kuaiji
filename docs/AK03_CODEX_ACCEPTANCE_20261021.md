# AK03 事实校验接入与本地验收

日期：2026-10-21。基线 HEAD：`baf3f414187a198dbde1c41f8a90e42dee2629ca`。

## 结论

**AK03 本地离线集成门槛通过。** 已把事实校验接入现有助手、票据识别/员工预填及分录建议的实际业务流程。

定向回归按 test ID 保留最后一次结果：本地离线 **636 passed / 0 failed / 0 skipped**；隔离 PostgreSQL 14 **27 passed / 0 failed / 0 skipped**。这是分批测试去重后的结果，不是一次全仓测试或生产验收。早期失败和修复后的结果均保留，未把失败记录删掉或忽略。

未使用真实模型、未访问线上、未提交、未推送、未部署。本轮没有修改迁移、C01、PG 投影、费用/队列内核、UX 或其他智能体的工作。会计复核后导出边界保持。

## 已完成的业务行为

### 助手：只发布经过程序核验的事实

- 模型使用内部 `AssistantModelReply` 返回 `fact_status/claims`，明确当前事实、资料缺失、证据冲突、范围不支持或需要会计判断。
- claims 绑定当前 ContextPack 的企业、账套、fact key、JSON 叶路径、证据 ID、事实版本、来源资格、币种、单位和精确值。
- 目前发布白名单为金额、数量、日期。回复由服务端固定标签和当前值组成，不发布模型的自由财务结论或任务理由。
- `source_gross_total` 只能显示为资料来源绝对金额合计，不能改写为收入、利润。保存数据不等于审核通过，OCR 始终标为待人工核验。
- 无 claims、任一 claim 不合格或模型弃答：返回固定人工核对提示。非法 citation 仍硬失败；并非把所有事实失败都变为成功验证。
- 模型给出的任务建议不直接发布为可执行结论；现有工作台、受控任务预览/确认服务及会计作业入口仍保留。
- 最终结果投影回原 `answer/citation_ids/proposals` 三字段，兼容既有前端和 PostgreSQL 069 约束，不需要数据库迁移。
- 内部严格 JSON schema 把新增属性列为必填；Python 解析保留默认值，仅用于旧响应安全降级。

### 票据：模型自报原文不能作为独立证明

- 金额 quote 必须包含对应字段标签；相同数字出现在净额、税额或价税合计的其他列，不能互相替代。
- 拒绝裸金额、金额 token 截断（如 `10.001 → 10.00`）、长标签裁剪（如 `不含税金额 → 含税金额`）和号码子串引用。
- 原件资格按页核验：纯视觉输出缺少独立 PDF 文字层/Paddle 文字时，保留识别结果供查看和预填，同时生成 blocking `original_unverified` 异常。
- 该异常会阻止采用/自动采用。独立会计须核原件并记录确认理由；员工本人不能确认。之后仍需正式凭证复核才允许导出。
- Paddle 可以补充缺失的 PDF 文字层；若存在 PDF 文字层且两种独立来源冲突，保留冲突，不以一个覆盖另一个。
- 新旧识别版本区分：更新 OCR prompt/cache 指纹；旧识别记录未通过当前规则时进入重新识别或人工核验路径，不静默升级资格。
- 同一份确定性检查用于员工 intake、业务 OCR、识别缓存和异常处理；员工预填仍不直接改业务事实或账务。

### 分录：借贷平衡之外再核对金额来源

- 逐票引用必须绑定当前附件、识别记录、文档、字段及页码。新增可空 `document_index`；单文档旧引用兼容，多票同页有歧义则必须明确文档。
- 引用的价税合计必须完整覆盖当前参与核算的票据集合，合计须等于来源金额，不能只拿一张小票为整个事项背书。
- 净额/税额拆分需要完整引用两类字段，精确满足净额加税额等于总额，并与当前已发布、适用业务的模板逐科目/方向/金额一致。无模板或未知税额则交人工，不能凭借平衡总额发明分摊。
- 生成和采用时都执行校验；事实修订、撤权、期间锁定及既有版本/幂等防护保持。
- 金额验证不证明自由文字。通过校验后的 description、reasoning、warnings 使用固定待复核文字，不能夹带“利润增加”“税额已抵扣”“已审核已过账”等无依据结论。
- 人工说明不能把错误票据金额变成正确证据；金额不一致时需要正确原件/事实，或走现有人工编辑流程。

### 共享内核：计算输入及含义均受限

- `app/intelligence/fact_validation.py` 是可部署的单一实现；原 tools/answers 仅兼容导出，后端不依赖离线工具目录。
- 计算操作数必须逐项引用当前服务端证据；禁止模型自报数字后自证算式。
- 默认只允许净额、税额、价税合计三种明确关系。其他计算必须由服务端显式提供 `CalculationRule`；模型不能声明规则，当前禁止收入/利润派生。
- 企业、账套、版本、币种、单位严格匹配；同 ID 异内容拒绝；未知和推断不能获得 ready 资格。
- Decimal 拒绝 bool、float、NaN、指数文本、超界值、除零和需隐式舍入的结果。
- 助手当前只发布 `source_fact`，没有扩展成通用财务计算或自动执行工具。

## 失败测试先行与回归

证据根目录：`tmp_test/ak03-codex-20261021/`。

| 问题 | 红证据 | 绿色验证 |
|---|---|---|
| 自报计算输入、越界数值、错误身份等 | `compiler-red.txt`：23 failed / 10 passed；另有 Decimal Overflow 红测 | 最终共享内核及旧 AG03/AG05：184 passed |
| 有来源的算术被包装成利润或错误净额 | `compiler-semantics-red.txt`：6 failed | 184 组合包括该修复及明确服务端规则测试 |
| 助手有合法 citation 仍发布无依据结论 | `assistant-red.log`；PG 旧候选也复现 | 新助手 26 专项及旧助手回归去重 86 passed |
| 内部严格 schema 不完整 | `assistant-schema-red.log`：1 failed | 新 26 + 原 provider 8：34 passed |
| 字段标签换列、原文自证、均衡虚构拆分、多票引用歧义 | `ticket-red.xml`：12 failed / 3 passed | 最终票据、checks、完整 workflow：69 passed |
| 合法金额夹带虚假 journal 理由 | `prose-red.xml`：1 failed | 固定安全渲染已通过最终票据组 |
| 数字截断、中文标签截断 | `quote-boundary-red.xml`、`label-boundary-red.xml`：各 1 failed | 最终票据组及专项通过 |
| 合并独立来源时丢失 PDF 冲突 | `provider-cost.xml`：222 passed / 1 failed | 修复后完整 Paddle + AK03：56 passed |
| PG 实际发布和自动采用缺口 | `pg/pg-ak03-red.xml`：5 failed / 2 passed | 新 PG 7 项及原有 20 项，去重 27 passed |

助手初始红日志的 18 failed 中，12 项是新增测试错误使用事务所 owner 读取未移交员工事项，**不能算产品缺陷**。修正为员工本人后通过，没有改权限实现。最终 26 项整文件复测通过。

流程回归中更新了明确变更的旧契约：

- 空白位图 fixture 不再默认有可信 OCR；正常人工流程显式调用独立会计确认原件。
- 自动化/缓存正常路径使用独立、明确声明的合成原文 fixture，不从可变模型输出伪造独立依据；另保留纯视觉无依据的负向场景。
- 金额差异经说明后仍不能采用错误建议，必须补正确证据；银行关联改变事实范围后重新确认原件。
- AK02 发送前最新 finding 断言按 ID 查找，不依赖 finding 数组第一项。
- 保留月结、导出和费用断言，未用跳过或降低权限保护换取通过。

最终去重计数、各 XML 哈希、最后一次状态与剩余失败列表：`test-evidence-summary.json`。列表中没有剩余 failed/skipped。受影响代码及测试 Ruff、`git diff --check` 通过；保留已有 Starlette 弃用警告。

PG 详情见 [AK03_PG_LOCAL_VALIDATION.md](AK03_PG_LOCAL_VALIDATION.md)。采用 HEAD 干净候选叠加明确文件白名单，避免混入 C01/并行 138 迁移；真实受限 runtime/worker 登录，无 RLS 绕过。费用、一次发送、企业隔离、原件确认、独立复核后导出均有实际 PG 证据。最后 OCR 冲突修复后再复跑新 7 项；临时集群已停止，临时凭据已清理，原 5432 不变。

## 修改归属与复跑

基线冻结 1443 个可读文件；`baseline.diff` 保留全部原有未提交变更。`ownership-check.json` 的 `unowned_changes=[]`。新增/修改仅涵盖上述 intelligence、assistant、兼容导出及针对性测试/文档；既有 C01、数据库、迁移、AK01 费用/队列及 UX 变更保留。

候选源与测试 SHA-256：`final-source-hashes.json`。PG 使用的最终白名单另保存在 `pg/final-overlay-hashes.json`。

离线专项复跑：

```powershell
.\.venv\Scripts\python.exe scripts/test_accounting_offline.py nexus_backend/tests/test_ak03_fact_validation.py nexus_backend/tests/test_ak03_assistant_facts.py nexus_backend/tests/test_ak03_ticket_facts.py -q
```

runner 清理继承的数据库/供应商变量，并将 socket 限于 loopback；这是测试防护，不宣称操作系统级沙箱。PG 专项必须另用显式隔离库和受限角色，不能注入生产 DSN。

## 边界及后续

- 真实模型效果、真实票据识别准确率、PG17、线上浏览器与生产表现未验证。
- 当前金额标签规则较保守，未知版式会转人工；不能把确定性字段通过解释为会计处理或税务判断自动正确。
- 没有改审核、正式记账、过账或导出权限，也未授权任何 Agent 自主审核或执行账务。
- AK03 本地集成已闭环，可以继续 AK04 执行记录与人工接手流程的本地开发；不代表 C01 或后续包已通过。
