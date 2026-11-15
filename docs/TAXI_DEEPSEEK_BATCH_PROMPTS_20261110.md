# 出租车试点 DeepSeek 分批执行提示词

这组文件先把 TAX01–TAX24 编排为 A–H 八批，再追加前端增量收敛 I–L 四批，以及待用户单独启用的 M 批 React 19 兼容升级。每份都含完整通用规则，可单独复制给 DeepSeek；无需依赖上一聊天的记忆。代码和报告路径按当前仓库解析，换工作树时先确认根目录。编号代表执行顺序，不代表任何批次已经验收。

## 使用方式

先交 A，再按顺序 B、C、D、E、F、G。H 按自身准入和六个子阶段执行，之后依次 I → J → K → L。真实试点、客户模板或外部环境缺失时，只完成已授权本地部分，保留真实适配待验。I–L 不要求为补齐 H 的外部待验而连接真实环境，但其所依赖的本地能力必须真实可用。

可直接复制下面的启动语，将文件名中的 A 换成已授权的 A–L 批次。M 不在此连续执行范围内，须使用后面的单独启动语：

> 请完整读取并执行 `docs/TAXI_DEEPSEEK_BATCH_A_20261110.md`。先核对当前代码、前置证据和修改归属，按其中要求先写失败测试再实现。只执行本批，持续完成所有已授权且不依赖缺失外部资料的工作；不得以只写报告、只测拒绝路径或模拟成功代替交付。保留企业隔离和会计复核后导出，不覆盖其他智能体修改、不接真实模型或通知、不提交、不推送、不动线上。结束时按文件要求提供分层验收与下一批准入判断。

文件中的安全边界只是本次本地执行范围，不是永久禁止真实试点；以后需用户单独授权和相应环境。不得用本地通过冒充实际客户适配、真实发送或真实两期试点通过。

## 文件清单

| 批次 | 范围 | 独立完整提示词 |
|---|---|---|
| A | TAX01–02｜业务边界与结转正确性 | [TAXI_DEEPSEEK_BATCH_A_20261110.md](<C:/Users/Fei/Desktop/AI应用/nexus-ai-kuaiji/docs/TAXI_DEEPSEEK_BATCH_A_20261110.md>) |
| B | TAX03–04｜主数据、期初与来源接入 | [TAXI_DEEPSEEK_BATCH_B_20261110.md](<C:/Users/Fei/Desktop/AI应用/nexus-ai-kuaiji/docs/TAXI_DEEPSEEK_BATCH_B_20261110.md>) |
| C | TAX05–06｜大凭证与规费应收 | [TAXI_DEEPSEEK_BATCH_C_20261110.md](<C:/Users/Fei/Desktop/AI应用/nexus-ai-kuaiji/docs/TAXI_DEEPSEEK_BATCH_C_20261110.md>) |
| D | TAX07–09｜核销、T+文件与P0办理闭环 | [TAXI_DEEPSEEK_BATCH_D_20261110.md](<C:/Users/Fei/Desktop/AI应用/nexus-ai-kuaiji/docs/TAXI_DEEPSEEK_BATCH_D_20261110.md>) |
| E | TAX10–12｜维修、摊销与OA费用薪酬 | [TAXI_DEEPSEEK_BATCH_E_20261110.md](<C:/Users/Fei/Desktop/AI应用/nexus-ai-kuaiji/docs/TAXI_DEEPSEEK_BATCH_E_20261110.md>) |
| F | TAX13–15｜租赁、税费与利润下钻 | [TAXI_DEEPSEEK_BATCH_F_20261110.md](<C:/Users/Fei/Desktop/AI应用/nexus-ai-kuaiji/docs/TAXI_DEEPSEEK_BATCH_F_20261110.md>) |
| G | TAX16–18｜月度双档、岗位办理与综合验收 | [TAXI_DEEPSEEK_BATCH_G_20261110.md](<C:/Users/Fei/Desktop/AI应用/nexus-ai-kuaiji/docs/TAXI_DEEPSEEK_BATCH_G_20261110.md>) |
| H | TAX19–24｜试点后的效率与持续改进 | [TAXI_DEEPSEEK_BATCH_H_20261110.md](<C:/Users/Fei/Desktop/AI应用/nexus-ai-kuaiji/docs/TAXI_DEEPSEEK_BATCH_H_20261110.md>) |
| I | 前端基础与壳层收敛：现有 token、组件规则、身份与范围、首屏 | [TAXI_DEEPSEEK_BATCH_I_20261110.md](<C:/Users/Fei/Desktop/AI应用/nexus-ai-kuaiji/docs/TAXI_DEEPSEEK_BATCH_I_20261110.md>) |
| J | 会计表格与原件对照：差异定位、大批次、选择集合、办理返回 | [TAXI_DEEPSEEK_BATCH_J_20261110.md](<C:/Users/Fei/Desktop/AI应用/nexus-ai-kuaiji/docs/TAXI_DEEPSEEK_BATCH_J_20261110.md>) |
| K | 员工与财务办理减负：票据确认、退回补件、核验移交 | [TAXI_DEEPSEEK_BATCH_K_20261110.md](<C:/Users/Fei/Desktop/AI应用/nexus-ai-kuaiji/docs/TAXI_DEEPSEEK_BATCH_K_20261110.md>) |
| L | 交互细节与综合验收：中文输入、弹层、状态、微动效、同候选门禁 | [TAXI_DEEPSEEK_BATCH_L_20261110.md](<C:/Users/Fei/Desktop/AI应用/nexus-ai-kuaiji/docs/TAXI_DEEPSEEK_BATCH_L_20261110.md>) |
| M（待启用） | L 验收后单独开展 React 19 兼容升级；不附带 UI 重构、Astryx 或 Compiler | [TAXI_DEEPSEEK_BATCH_M_20261110.md](<C:/Users/Fei/Desktop/AI应用/nexus-ai-kuaiji/docs/TAXI_DEEPSEEK_BATCH_M_20261110.md>) |

## I–L 如何接在 H 后面

- I 先核对 H5/TAX23 已有成果；J/K 复用 C/D/G 的业务与办理能力。只补真实缺口，不把做过的内容换个名称重写。
- 四批延续“压缩壳层、统一 token、重排任务信息、动效降噪”，吸收 Astryx 的布局、表格、字段和交互方法；不安装 Astryx、不升级 React、不重造导航或审批状态机。
- I/J/K 各自通过本地必验后再交下一批；L 在最终同一候选重跑集成验收，不拼接四份旧报告的通过结果。
- 所属单位与主要身份保持可见，当前办理客户/期间明确；错误、撤权、版本冲突、unknown 不能被视觉精简掩盖。
- 真机、真实用户、真实 OCR/通知和 T+ 适配待授权；不能因为缺它们停掉可独立完成的本地工作，也不能将模拟验证冒充真实通过。

从 I 开始可直接复制：

> 请完整读取并执行 `docs/TAXI_DEEPSEEK_BATCH_I_20261110.md`。它接在原 H 批之后，先核对 H 的本地验收与当前修改归属。按文件要求先写失败测试再修复，复用现有 token、导航、组件和业务服务，不安装 Astryx、不升级 React。持续完成 I 批可执行工作及本地、PG16/17、桌面手机验收，保留企业隔离与会计复核后导出，不覆盖其他智能体修改、不接真实模型或通知、不提交、不推送、不动线上。只执行本批，完成后交付验收证据和 J 批准入判断。

之后把文件字母依次换成 J、K、L，并按各文件要求核对前置；L 为本轮已授权前端改造的最后一批，不得自动启动待启用的 M 批。

## M 批：留待最后单独启用

M 的编排已经就绪，当前没有升级授权。I–L 的“不升级 React”规则保持有效；只有用户明确启用 M 且 L 的本地必验通过后，才可按 M 的最小范围升级。读索引、完成 L 或此前要求连续执行 I–L，均不构成 M 的启用。

M 依次完成：稳定基线与归属核对 → 官方版本及依赖兼容评估 → 行为测试与升级失败复现 → 最小兼容修复 → PG16/17、桌面手机、跨浏览器与 dev/preview 回归 → 同候选证据与回退说明。执行时选择经过核验的具体 React 19 稳定版本，不预先固定未来的“最新版”。

**下面的启动语仅供用户日后决定启用时发送，现在不要执行：**

> 现在执行 M 批。请完整读取并执行 `docs/TAXI_DEEPSEEK_BATCH_M_20261110.md`，先确认 L 的本地验收、当前候选和修改归属。仅做 React 19 兼容升级，复用现有实现，先建立行为基线、复现兼容失败再修复，完成本地、PG16/17、桌面手机和跨浏览器回归。不得引入 Astryx、React Compiler 或重做业务流程；不覆盖其他智能体修改、不接真实模型或通知、不提交、不推送、不动线上，保持企业隔离、费用和 attempt 防重发、会计复核后导出。持续完成本批并交付同候选验收与回退说明，不自动启动下一批。

## 交接需要保留什么

每批应产出对应 ACCEPTANCE 与 CHECKPOINT 文档及机器可读证据。下一批读取它们但仍核对代码：当前候选、修改归属、成功与拒绝路径、PG16/17、浏览器、外部待验和真正阻断项。未提供原始模板/政策，不得在后续批次悄悄转为“已确认”。

A–D 是最小核心链，E–G 是完整月度业务，H 是基于证据的持续优化。每批都含前端入口和相应验收，不能将“后端写好了，界面下次补”算作完成。

I–L 是已有能力上的界面收敛与交互补强，不替代 A–H 的业务验收，也不以外观改善推断外部系统已经接通。

M 是待单独启用的兼容升级，不是 I–L 的准入条件，也不以 React 版本升级推断界面、性能或生产可用性已改善。

本组提示词仅新增规划文档，未修改产品代码或执行外部业务。
