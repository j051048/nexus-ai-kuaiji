# RSI01–RSI03：离线候选改进与企业规则候选执行提示词

日期：2026-11-01。用途：交给 DeepSeek 逐包执行的工作令，非实施或验收报告。

使用：每次复制“通用执行约束”加当前工作包。顺序为 RSI01 → RSI02 → RSI03。前包未过规定门槛，不进入依赖它的实现。不得因此暂停、改写或要求其他执行者停止 DCP 主线。

本次编排只核对了代码和接口，未运行新测试、未修改产品代码、未连接数据库或真实模型。

## 当前事实，执行时重新核对

核对时 HEAD：`4800c10cb920ed1ac0f8b79cca86ede3118c1051`，不能据此回退工作区。

已存在且必须复用：

| 能力 | 现有入口 | 本轮边界 |
|---|---|---|
| H01 OCR 离线回放 | `tools/ocr_replay/replay.py`：`ExtractionInput`、`Policy`、`load_manifest`、`extract`、`run_manifest`、`validate_result_identity`、`compare` | 继续冻结原文/金标，保留原 CLI 和故意退步的示例 |
| AG05 身份与评分 | `tools/ai_inference_lab/examples.py`：`CandidateConfig`、`EvaluationSpec`、`UnknownAttempt`、`evaluate_samples`、`evaluation_report` | 不新增一套自报评分，不修改既有关键退步门槛 |
| 助手任务回放 | `tools/assistant_replay/baseline.py` | 作为共享工具不回归的检查，不在本轮扩展助手运行循环 |
| 企业规则 | `app/learning/models.py`、`service.py`、`router.py`、`governance.py` | 复用当前样例读取、模板验证、语义去重、保存草稿和评测，不自动调用 publish |
| 已审核样例 | `LearningService.get_sample`，含 `ordinary_rule_eligible`、原始/最终结果、修正、来源和证据指纹 | 不能用模型或 JSON 中自填的 reviewed=true 作为审核依据 |
| 已有规则状态机 | `save_rule → evaluate → publish / retire` | 本轮候选接线最多到本地隔离库中的 draft + evaluation；实验执行器无发布能力 |

核对时的他人修改包括：

```text
.github/workflows/accounting.yml
scripts/migrate_accounting.py
nexus_backend/migrations/postgres/191_historical_compatibility_labels.sql
nexus_backend/tests/test_historical_compatibility_postgres.py
nexus_backend/tests/test_historical_compatibility_repair.py
docs/HISTORICAL_COMPATIBILITY_REPAIR_20261031.md / .json
docs/DEEPSEEK_NEXT_EXECUTION_PLAYBOOK_20261031.md
nexus_backend/app/onboarding/router.py
nexus_backend/app/onboarding/service.py
nexus_backend/app/onboarding/setup.py
nexus_backend/tests/test_onboarding_setup.py
```

这不是完整保护名单。执行时以最新 status/diff 为准，尤其保护正在进行的 DCP 开账、月结、交付及前端修改。

FlashRSI 参考固定在 `dea1b1d0285e3328234a1e344562bb9e54ce14f7`，重点是 `core/snapshot.py`、`algorithms/gepa/_population.py`、`algorithms/ace/_playbook.py`。不安装整套 FlashRSI，不接入其 Agent runtime，不执行其自动安装脚本。首选借鉴设计并复用现有实现；确需摘取源码须记录来源 commit、许可证、版权和修改说明，并核对该文件的上游来源。

## 通用执行约束：每包都生效

```text
你负责完成指定的 RSI 工作包。用户要可运行的本地成果，不是再次写规划、只新增接口壳、只汇报“还有哪些没做”。

授权：
1. 本地离线工具、明确的专用测试、虚构原件/企业/账号。RSI01/02不连接任何数据库；RSI03仅在专门本地集成测试中允许使用隔离SQLite与PG16/17。
2. 不接真实模型/真实OCR服务，不下载真实企业数据，不发真实通知，不安装或配置生产服务。
3. 不提交、不推送、不部署、不操作线上，不自动发布任何规则/提示词，不改变生产模型配置。
4. 不读取生产凭据。测试配置私有保存，输出不得含密码、token、完整DSN、真实原件或客户资料。

协作与范围：
5. 先读AGENTS.md、当前HEAD/status/diff和本手册。逐文件记录改动归属、起始摘要与计划修改范围。
6. 禁止reset/clean/checkout覆盖、整文件回填旧版本、全仓格式化、暂存或提交他人改动。遇到并行修改停止编辑该文件并协调，不要求DCP整体停工。
7. 首选新增 tools/ai_inference_lab/rsi/ 下的薄编排模块、专用测试/虚构夹具和报告。H01/AG05函数直接调用，不能复制其评分器、来源校验、Decimal逻辑成为分叉实现。
8. 原则上不改 app/、src/、CI、数据库迁移、C01/P01门槛及DCP报告。小范围共享工具改动只有在必要、无归属冲突、保留旧接口且有回归证据时才允许；先说明复用为何不足。
9. 若发现生产领域规则的漏洞，保留独立复现证据并列为单独修复需求；不得为了完成实验而放宽领域服务或绕开它。非依赖工作继续，依赖该漏洞的验收不得假绿。

财务与身份：
10. scope由可信执行配置指定，并绑定firm/company/ledger/book/period；结果中的scope不是授权。检查样本、原件、页码、字段、版本、来源hash和祖先来源。
11. 企业原始样例不能进入全局共享池。跨企业泛化评测仅用明确虚构或另获授权的数据集，逐企业执行、逐企业计分；不拼接各企业上下文。
12. 保持企业隔离、当前权限重检、独立复核、会计复核后导出；禁止生产自动应用候选到分录或正式账。
13. 历史金额不是当前事实；当前120、历史100时，候选只能借鉴处理方式，不能把100带入当前账。
14. unknown/missing/cancelled/revoked不隐去，不当作成功或免费；发送后未知不自动重发。缺usage/cost保留null及原因，显式0与未知区分。

测试与证据：
15. 先为未实现或错误行为写测试、运行保存红测，再实现并复跑。红测须是目标行为失败；导入/环境/路径错误不算业务缺陷证据。
16. 已有行为正确则保存原功能通过证据并补覆盖，不能故意破坏代码制造红测。新增命令的缺失可以作为功能未实现红测，但仍需后续行为负向用例。
17. 本地回放使用mock或身份完整的archived_results，不伪造真实模型效果。只有prompt字符串改变、实际mock行为不变时应判没有效果变化，不声称优化成功。
18. 输出先校验身份与完整性，再评分。身份造假/范围错/额外结果属于无效实验，不是“分数稍低”。候选自报correct/risk/score不得作为金标。
19. 每次run独立目录；禁止覆盖旧报告/配置、读取旧completed标记冒充新候选。原子写入、故障恢复和输出路径边界必须有测试。
20. 记录实验依赖指纹（实际复用源码、prompt/schema/context/scorer、数据manifest/原文/金标、候选及运行参数）与全仓HEAD/状态。依赖文件变化使旧run不可恢复；无关DCP变更单独记录，不能混称所有项目同候选验收，也不能因此占用或冻结DCP文件。
21. 必验项0 failed/error/skip。SQLite不能代替RSI03规定的PG16/17受限角色及并发测试。缺环境明确报未验证，不改skip当成功。
22. 新增持久化实验产物只在隔离输出目录，不引入业务数据库表。现有业务数据库之外的快照不代表签名、授权或可信审核。
23. 代码没写、入口没接、测试没跑属于本包未完成，继续处理；只有外部环境或不可安全合并的修改才是阻断。不能用“建议下一轮补齐”代替当前已授权可完成工作。

每包输出：
- docs/RSIxx_ACCEPTANCE.md/.json：实际范围、归属、基线/依赖指纹、逐条红绿证据、结果及限制。
- tmp_test/rsi/<包>/<run-id>/：JUnit、报告、故障注入结果等；私有配置不提交。
- 一条可复跑的验收命令，明确它跑的是mock/归档还是本地PG。
- 结论仅可为本包本地工程/契约通过；真实模型质量、生产接入、发布均未验证/未实施。
```

## RSI01：冻结候选谱系与可修改白名单

```text
执行RSI01。目标：在H01/AG05之上建立最小候选资源包，知道改了什么、实际执行了哪个版本，并拒绝越界修改。

前置：
- 完成修改归属盘点，H01/AG05当前关键单测可复跑。
- 不依赖DCP完成，不启动数据库或业务worker。

修改范围：
- 首选新增 tools/ai_inference_lab/rsi/ 的候选契约、快照、命令入口及专用测试。
- 引用H01严格JSON、路径/来源/原子输出工具及AG05 CandidateConfig；必要时加薄适配，不重写它们。
- 此包不改提取质量、不做候选选优、不创建企业规则，不引入任意Python插件执行。

实现要求：
A. 候选资源与运行证据分开。
1. 候选包保存：schema_version、kind、root/parent、规范化资源清单及字节摘要、允许变化范围、实际changed_paths、变更理由。
2. 运行manifest另存：candidate ID、scope、数据集/原文/金标摘要、prompt/schema/context/scorer/adapter版本、model=none、模式、限额、依赖指纹。
3. 凭据、真实原件、金标内容和运行日志不放进可编辑资源包；金标摘要可以出现在只读运行记录中，金标内容仅评分器可读。
4. 摘要检测漂移，不证明来源身份。加载时重新算摘要；同名不同内容拒绝，父候选不匹配/循环/缺失拒绝。资源内容hash与含父链的candidate ID含义写清，不能混用去重口径。

B. 先只允许两类声明式资源：
- 票据提取prompt正文；
- 已有mock提取器明确支持的有限策略配置，用于验证真实执行差异。
配置键、类型、长度和取值白名单固定；模型/候选不能新增schema字段、改变评分、修改scope、增加工具、扩大预算或声明其他文件可写。
如果允许金额标签配置，优先有限字面标签或预注册策略ID，不接受任意正则/Python/表达式执行。
权限、复核、事实校验、金额计算、schema、评分器、源数据、金标、测试及C01/DCP代码全部只读。

C. 完整候选执行绑定。
- H01实际执行使用已验证候选内容，不能“报告候选B，实际仍跑默认A”。
- 未修改的资源保持原版本；不能只存prompt字符串却漏掉改变输出的策略配置。
- 组件可修改范围独立于候选声明，由可信runner决定；加载的candidate不能给自己提权。
- 禁止eval/exec、任意import、shell和HTTP；不把文件白名单误称成操作系统沙箱。

D. 产物安全。
- 规范化相对路径，拒绝绝对路径、父目录逃逸、Windows盘符/UNC、符号链接或junction逃逸及文件/目录冲突。
- 文件数量/单文件/总字节/嵌套/父链深度有明确上限。
- 首次发布快照原子且不能覆盖已存在不同内容；两个进程写同ID保持一致，不产生半成品。
- 新运行目录；完成标记绑定依赖指纹。写入失败保留最后有效产物，不将partial当complete。

建议CLI（是本包需实现的入口，不是假称已有）：
python -m tools.ai_inference_lab.rsi snapshot ...
python -m tools.ai_inference_lab.rsi inspect ...
python -m tools.ai_inference_lab.rsi diff ...
参数按本项目现有风格收敛，帮助信息注明“仅离线，不授权发布”。

先红测试矩阵：
S01 同名prompt内容变了，candidate ID/请求绑定未变；
S02 parent/root错配、循环、缺父版本；
S03 修改冻结schema/评分器/金标/权限文件或删除只读文件；
S04 候选自填扩大可修改范围、budget、company或工具；
S05 路径穿越、Windows路径、链接逃逸、重名/重复JSON键；
S06 保存覆盖旧文件、写到一半中断、双进程写入；
S07 恢复时prompt/策略/共享依赖变化却复用旧结果；
S08 baseline/candidate实际执行对象不一致；
S09 仅改展示名不改变内容指纹口径的解释/去重；
S10 snapshot里出现运行凭据或未经声明资源；
S11 超限包/坏类型/bool冒充数字/未知schema；
S12 import/validate/snapshot意外发起网络、数据库或业务写入。

验收：
- 原H01故意回归示例仍判regression，AG05原测试不回归。
- 在Windows本地实际测试文件原子写入和两个进程竞争；不能全mock文件系统。
- 实际提取器收到候选对应prompt和策略，观察到的请求指纹与冻结配置一致。
- 验收入口用专用网络/DB哨兵：任何非允许动作立即失败；不仅靠代码搜索“没import requests”。
- 全部S项有结果、无数据库、无源码自动修改、无发布。通过后允许RSI02。
```

## RSI02：有预算的候选比较与独立验收

```text
执行RSI02。目标：用RSI01冻结少量手工/mock候选，复用H01执行与AG05评分，产出可核验的选择/拒绝理由。它不是自主提示词优化器，更不是真实模型效果证明。

前置：RSI01全部通过；H01/AG05原验收可复跑。禁止跳过RSI01只给结果补一个candidate_id。

修改范围：
- tools/ai_inference_lab/rsi/ 内的实验调度/报告薄层；专用虚构夹具和测试。
- H01负责字段提取/证据，AG05负责身份、金标评分和回归判定；保留其现有schema和UnknownAttempt单独信封，不硬塞未知状态到H01合法输出中。
- 不改生产provider/worker/费用；实验预算与生产账务预算分开，不新造生产费用服务。

工作项：
A. 冻结实验计划。
- 基线+最多3个候选；候选集合、预算、样本分组、关键字段和选优次序在运行前固定。
- 首版候选由人显式声明或确定性mock提供，不调用模型生成prompt。
- 保留已有“修复价税合计但破坏票号前导零”的坏候选作为反例；可增有限mock策略，不能改坏现有baseline去伪造生产缺陷。
- prompt-only变化若mock不使用其内容，报告“行为未改变”；禁止人为捏造响应证明prompt有效。

B. 分三层执行。
1. 开发样本：明确缺陷小集合，快速拒绝无效候选；任何身份/越界问题立即终止该候选。
2. 完整开发与安全回归：同一固定分母、同样资源约束，对通过小集合的候选比较。所有候选必须跑关键财务/隔离反例，不能因平均分好就省略。
3. 独立验收：先冻结选出的candidate，再一次性评未用于选优的数据；不把验收失败反馈用于继续调该轮候选。若要另开研究轮，明确旧验收集已暴露，补全新验收集。
- 开发/验收按source hash及派生祖先分组；同票多页、裁剪、改名、改sample_id不能跨集。
- 至少两家虚构企业，独立scope；跨企业泛化用独立执行结果，不能把他企样例注入当前提取上下文。
- 运行器不给提取器金标；分组检查器可在受信侧读取hash防泄漏。

C. 选择顺序固定。
身份/完整性合格 → 安全与关键字段门槛 → 无关键退步 → 任务/字段效果 → 资源成本。
金额、票号、企业/期间归属、来源证据、风险漏报等门槛优先；不得把一处金额错误与多处文案改进平均掉。
若均未明确改善则保留baseline；平分保留既有版本。AG05已有拒绝规则不能被本层“综合评分”重新放行。
验证集失败或不完整不能宣称候选可用。mock accepted_offline仅为离线契约结论；报告顶层model_verified=false、publication_authorized=false。

D. 分母、预算、恢复。
- missing/unknown/failed始终保留；额外/重复样本拒绝。不能用task_id字典覆盖掉重复结果。
- 每候选每样本绑定attempt、请求指纹与结果hash。已完成且兼容则复用；未知attempt不自动重跑，不生成第二个attempt假装未发出。
- 调用/样本/时间预算由runner计数；恢复后不能归零增加额度。成本未知为null；mock成本不能写成真实免费模型费用。
- 时间使用单调计时，测试可注入时钟；取消在下一安全边界停止，不把partial报告标完成。
- archived_results须来源/版本/输入身份齐全，不能把别的prompt或模型归档套进候选。mock与归档不可直接混算效果。
- 没有跑完的候选写incomplete，不得用缺失样本的高均分赢过完整baseline。

E. 输出逐样本JSON与中文Markdown。
列出scope、样本来源hash、split、candidate/parent、请求和依赖指纹、实际模式、原文定位、逐字段真假、误报/漏报/弃答、异常分类、unknown/missing、耗时、usage/cost及未知原因、修复/退步和拒绝依据。
金额/原文只显示虚构夹具；报告渲染转义用户文本，不生成可执行HTML。
报告要区分“准备成功/执行完成/工程验收通过/模型效果未验证”，不把完成标记当质量分。

先红测试矩阵：
E01 gross改善但invoice_number前导零退步仍被选中；
E02 平均分提高但金额/归属/证据/风险漏报退步；
E03 缺/重复/额外结果让分母变化；
E04 候选自报correct/score/risk覆盖金标；
E05 同票改ID、改名、多页、派生原件跨开发/验收泄漏；
E06 企业A结果绑定B、ledger/book/period错位；
E07 只改prompt没有执行差异却声称提升；
E08 归档请求/输入/模型/版本不匹配；
E09 未知usage与显式0、坏整数/NaN/Decimal边界；
E10 预算耗尽/中途取消/恢复后预算变大；
E11 unknown恢复后再次执行；
E12 写报告失败/运行中输入变化/旧complete复用；
E13 选择器访问验收内容或使用验收反馈重新选候选；
E14 全候选无改善、平分、全部失败的正确保留baseline行为；
E15 恶意附件文字要求关闭校验/读金标/改租户；
E16 JSON与Markdown逐样本、总计及结论一致，null不显示为0或NaN。

验收：
- 完整演示包含baseline、可辨识行为变化候选、关键退步候选；用现有确定性提取器/替身实际执行，不直接手填“比较通过”。
- 独立验收集在选择之前不可访问；测试用拒绝读取的探针和文件访问记录证明边界，不只写注释。
- 原H01/AG05/助手任务基线回归通过；实验指纹变更不会伪复用旧结果。
- 以本包CLI一条命令跑完可复现demo并出两种报告，无网络/数据库；明确没有真实模型收益数字。
- 全E项通过后允许RSI03。任何提升结论都只能限定为合成/归档数据与具体模式。
```

## RSI03：已审核修正形成企业规则候选，仅到草稿与评测

```text
执行RSI03。目标：让已审核修正产生可解释、可回放的企业规则候选，复用当前LearningService；默认只输出离线候选文件，本地集成验收最多保存规则草稿和评测，不发布、不生成业务分录。

前置：
- RSI01/02通过；当前learning样例/规则/往来方主数据与权限相关回归可跑。
- RSI01/02评的是OCR字段，本包不能以其得分证明会计规则正确。本包必须走既有规则模板验证、正反样例评测及影子回放。
- 明确隔离PG16/17环境可用；缺环境时继续纯模块/SQLite部分，但本包整体不能宣布通过。

修改范围：
- 新增离线规则候选编译/校验/报告适配器及专用测试；调用当前RuleDefinition、RuleCreate、RuleEvaluate、get_sample、save_rule、evaluate等既有契约。
- 不新增数据库表/迁移/生产路由/首页入口，不改规则发布策略、角色、C01、DCP或助手运行循环。
- 默认CLI没有数据库连接；只有独立本地集成测试执行器可用显式本地配置调用领域服务。不给实验runner publish/retire/自动apply的入口。

范围收敛：
1. 首期只支持已有 ordinary_rule_eligible 的普通business分类样例，并选一个明确业务模板（如办公采购费用）做完整路径。
2. 银行核销、历史迁移清偿、分摊、复杂税务、多币种等当前不能被普通规则推广的情形输出unsupported/需人工，不扩大现有RuleDefinition语法。
3. 同企业、同账套、现有核算政策内提议；不自动跨账期继承或全所共享。候选可记录建议适用期，但不能伪造底层已有有效期支持；超出现有语义须拒绝并列后置。

A. 可信样例读取与候选生成。
- 复用LearningService.get_sample从本地受限身份读取真实走过审核流程的样例，包含sample_fingerprint、evidence_fingerprint、来源版本、original/final/corrections和ordinary_rule_eligible。
- 离线导出文件只是证据载体，不是审核或权限证明；本地保存前重新从服务端读取并核验，不能相信文件里自填reviewed/actor/资格。
- 只处理实际已审核、未撤销/隔离且当前符合条件的普通样例。holdout与同源泄漏保持AG05边界。
- 从现有最终审核分录推导已支持的模板结构，不自行猜科目；无法用gross/net/tax等既有amount_field表达则弃答。
- 绑定稳定往来方ID及经审核别名。仅原文同名、身份不唯一或其他企业ID都不得合并。
- 经验描述不是规则执行体；模型式建议、自由文本“总是这样做”不能代替类型化RuleDefinition。

B. 候选文件。
包含：RSI01候选谱系、firm/company/book/period、规则定义、正例ID及来源/审核/证据指纹、反例清单、现有科目/主数据/政策指纹、修改理由、适用与不适用情形、重复/冲突检查结果、验证报告引用。
正例与反例都必须有真实不同来源。反例覆盖同供应商不同业务/收付方向/付款状态、近似名称不同稳定ID、需核销的银行样例等边界。
报告“建议规则草稿，未发布，不影响账务”。纯编译/验证不得增加learning_rules、业务草稿、凭证、任务或费用记录。

C. 接入既有保存/评测契约的本地验证。
- 用户明确选择某候选、准确scope及版本后才可能保存草稿；本轮只在本地测试中模拟此动作，不提供生产自动导入入口。
- 调用前按当前actor重判权限并重取样例、主数据、科目、政策及指纹。准备和保存之间变化须拒绝；避免仅在事务外precheck后盲目写入。
- 复用save_rule的语义去重和版本/状态校验。与已发布规则重复时只提示复用，不自动更新、停用或退回已发布规则。
- 默认新建draft；如支持显式修订，只能修订指定且仍为draft的同scope对象，并绑定expected_version/state_version。禁止修改任何已发布版本。
- 反复提交同一候选：可以返回已有相同草稿引用，或按现有语义去重返回明确冲突；不能留下重复规则。不要为“HTTP总是成功”新建业务幂等表。
- 调用evaluate记录既有evaluation，仍保持draft。评测通过不触发publish。原有效规则及生产选择优先级完全不变。
- 保存与评测沿用既有动作边界：已成功保存的draft可以在后续评测失败时保留；失败评测不得残留伪通过evaluation或改变发布状态。不要为了“全流程原子”包一个长事务，也不要悄悄删除已保存草稿。
- 保留当前原因/审计字段，并能关联候选摘要。若持久绑定额外字段必须改生产模型，先停在候选+审计已有能力可表达的范围，不私自加迁移。

D. 安全与业务正确性。
- 历史审核值100只决定模板结构；用当前120的虚构事实做影子渲染，必须得到120并绑定当前证据。
- 不允许业务样例冒充银行清偿或反向规避ordinary_rule_eligible。
- 未发布候选不能命中正常自动草稿生成；验证调用不得生成proposal_jobs/ocr_jobs、分录或导出。
- 审核身份不能由文件指定；不补造历史审核或把当前资格倒填到旧记录。
- 如引用文件写helpful/harmful，只当解释性记录，不能取代会计评测和发布政策。

先红测试矩阵：
R01 未审核/撤销/隔离/伪造reviewed样例进入候选；
R02 A企业样例、科目或往来方进入B企业规则；
R03 同名不同稳定ID、失效/歧义别名仍匹配；
R04 positive/negative同源、改ID同票、holdout泄漏；
R05 来源/审核版本/主数据/科目/政策在准备后变化仍保存；
R06 准备后撤权或授权到期，旧请求仍保存/评测；
R07 当前120被历史100覆盖；
R08 清偿/核销/迁移样例被推广普通分类规则；
R09 只改金额描述但无法表达分录模板仍生成“有效规则”；
R10 语义重复、并发同候选、响应丢失后重试产生多个规则；
R11 评测通过自动publish，或保存候选修改已有published规则；
R12 新draft被正常自动草稿生成器采用；
R13 反例错误命中或已发布规则冲突被隐藏；
R14 保存失败/评测失败发生部分提交或伪通过报告；
R15 错账套/月份/源指纹的离线文件被当成授权；
R16 候选生成/保存/评测期间发生模型、通知、记账、复核或导出副作用。

验收：
1. 纯模块用虚构样例走candidate→typed RuleCreate→RSI02式证据报告，金标/样例资格不是候选自报。
2. SQLite与PG16/17分别通过受限服务/API执行：真实业务审核捕获样例→读取→候选→显式保存draft→评测；不靠SQL直接把状态改approved。
3. PG16/17至少真实双会话测试重复候选、撤权/版本变化竞争，观察屏障/锁等待及提交结果；不能用两次顺序调用冒充并发。
4. 新规则仍draft；published规则定义/版本、业务草稿、正式凭证、任务、费用、导出数量/摘要在本包动作前后不变。仅允许预期的规则草稿、evaluation、审计变化。
5. 原learning、规则治理、银行清偿排除、旧账学习和会计复核后导出相关回归保持。受限角色不能因实验获得额外GRANT或BYPASSRLS。
6. 本包不新增业务表，因此不另造恢复体系；仅核验新增关联使用的原规则/evaluation/审计字段已属于现有恢复清单。发现缺口则明确列发布前阻断，不假称完整恢复通过。
7. 不要求新前端页面或浏览器验收，因为本轮没有UI改动；交付CLI和本地服务契约证据，后续接UI另立任务。

注意：这次完成的是“离线规则候选 + 本地既有草稿/评测接线”，不是自动企业学习上线，也不是允许生产数据被自动提炼或共享。
```

## 组合验收与防止“做到一半”

三个包各自产出独立报告，最后再增加 `docs/RSI_FINAL_ACCEPTANCE.md/.json`，只汇总当次实际候选证据。

测试入口应复用实际存在的测试启动器；以下是必须纳入影响判断的已有用例，不是让执行 AI 不分环境一次全跑：

```text
nexus_backend/tests/test_ocr_replay_engine.py
nexus_backend/tests/test_ag05_examples.py
nexus_backend/tests/test_assistant_task_baseline.py
nexus_backend/tests/test_assistant_task_workflow.py
nexus_backend/tests/test_stage8_learning.py
nexus_backend/tests/test_stage8_learning_postgres.py
nexus_backend/tests/test_stage11_rule_governance.py
nexus_backend/tests/test_stage11_rule_governance_api.py
nexus_backend/tests/test_stage11_rule_governance_postgres.py
nexus_backend/tests/test_stage8_rule_queue_integration.py
nexus_backend/tests/test_stage10_settlement_learning_scope.py
nexus_backend/tests/test_stage10_settlement_learning_scope_postgres.py
nexus_backend/tests/test_stage13_legacy_learning.py
nexus_backend/tests/test_stage13_legacy_learning_postgres.py
```

```text
最终验收必须分别回答：
1. RSI01能否冻结准确资源、拒绝越界修改、原子保存并正确恢复？
2. RSI02是否真的执行了各候选，并拒绝“平均提升但关键字段退步”？
3. 独立验收集是否没有被用来选优？原文/同源/企业身份是否准确？
4. unknown、missing、费用未知、预算与attempt是否在报告和恢复中保持？
5. RSI03的审核依据是否来自领域服务，草稿/评测是否仍经过当前权限与版本验证？
6. 是否证明规则未发布、未生成业务分录、未影响原有效规则？
7. PG16与PG17分别执行了哪些真实并发/负向，不能拿其中一个替代另一个。
8. DCP/C01/191及他人修改是否未被覆盖？所有自己修改文件是否有明确归属？
9. 哪些是本地mock工程通过，哪些是本地数据库接线通过，哪些完全未验证？
10. 真实模型/生产接入/自动发布/提交推送均未实施；不得写“自我进化已上线”。

若失败源于本包代码或测试缺失，继续修复，不以“下轮建议”结束。
若是不可安全处理的并行冲突或缺少隔离环境，保留现场、输出最小阻断证据和已完成项；依赖门槛保持未通过。
```

## 给 DeepSeek 的首轮启动语

```text
请读取 docs/DEEPSEEK_RSI01_RSI03_EXECUTION_20261101.md。
本轮严格遵守通用执行约束，只完成RSI01，包括红测、实现、绿测和验收报告。
不要提前做RSI02/03，不改变DCP主线或其未提交修改。
已有H01/AG05直接复用，不接真实模型、不自动发布、不提交推送、不动线上。
先核对当前修改归属，再开始。
```
