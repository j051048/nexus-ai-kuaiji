# 当前验收索引

本索引区分本地工程验收与发布许可，不合并不同候选的通过记录。

| 记录 | 用途 | 状态 |
|---|---|---|
| [财务办理续批](FINANCE_CONTINUATION_ACCEPTANCE_20261108.md) | 往来核销、跨期计提、月度服务单往返、持续门禁与恢复容量 | 当前候选 `c337ee9c…` 本地通过：离线 365、前端 1329，PG16/17 各 27+34+32+35；两模式浏览器各 15；完整恢复、HTTP50、两组 200 家及 30 分钟持续读取通过。未推送，远端 CI 与生产仍未验证 |
| [两项红灯与独立环境收口](FINANCE_CLOSURE_ACCEPTANCE_20261108.md) | 历史迁移夹具、首屏体积、C01 默认新库与同候选证据 | 历史候选 `2633dc19…`；离线 334、前端 1322、PG16/17 各 34+14、两模式桌面手机通过；首屏 gzip 174883，原预算通过。其当时未重跑的完整恢复/容量不能直接继承为当前结论 |
| [财务内核复用与办理入口](FINANCE_AGENT_FINAL_ACCEPTANCE_20261107_kernel-reuse.md) | 往来账龄、资产计提与独立复核记账、尾差/有效期/审计/刷新 | 历史候选 `36e164d9…`；两项保留失败已在 2026-11-08 收口并重跑相关回归，旧记录不改写为通过 |
| [07–12 同候选恢复与容量](TASKS0712_RECOVERY_CAPACITY_ACCEPTANCE_20261107.md) | 完整恢复、PG16/17、50 并发 HTTP、200 家批量及 30 分钟连续读取；重跑 01–12 和浏览器 | 历史候选 `66eb1619…` 本地通过；本轮源代码已变化，不继承为当前完整恢复/容量结论 |
| [试点与内功 07–12](TASKS0712_ACCEPTANCE_20261107.md) | 统计、能力边界、录入、测试、自检、运行、助手证据；保留 01–06 固定回归 | 历史候选 `82d79c20…`；固定回归已随当前恢复与容量验收重跑，旧记录保留 |
| [邀请、回执与政策 04–06](TASKS0406_ACCEPTANCE_20261107.md) | 受控邀请、送达证据、审核分类；保留 01–03 回归 | 历史候选 `31d2256d…` 的本地验收；固定用例已随 07–12 重跑 |
| [待办办理 01–03](TASKS0103_ACCEPTANCE_20261107.md) | 待办直达、返回刷新、持续门禁 | 历史候选 `aae0f107…` 的本地验收；固定用例已随 04–06 重跑 |
| [IMP 最终修补](IMP_REPAIR_ACCEPTANCE_20261106.md) | 上一候选的完整恢复、分析与容量证据 | 历史本地验收；不是本轮候选的全量发布证明 |
| [IMP07–09 阶段记录](IMP_INTEGRATED_STATUS_20261106_imp0709-r2.md) | 保存问题发现与修复经过 | 已被最终修补报告取代，保留原始数值 |
| [IMP09 阶段容量](IMP09_INTEGRATED_ACCEPTANCE_20261106_imp0709-r2.md) | 保存旧候选负载结果 | 已被最终修补报告取代，不能引用为当前容量 |

任何后续源码变化都需重新核对候选指纹。远端 CI、生产发布、真实渠道与真实客户试点均不得由本地通过推断。

## 持续门禁与执行入口

- 改动快检：`python scripts/test_accounting_offline.py nexus_backend/tests/test_task_journey.py nexus_backend/tests/test_delivery_followups.py nexus_backend/tests/test_dcp_reliability_gate.py -p scripts.qa_no_skip -q`；`npx vitest run src/task-journey.test.ts src/delivery-followups.test.ts src/workspace-progress.test.ts src/workspace-types.test.ts src/firm-inbox-contract.test.ts`。
- 双版本动态门禁：复用 `scripts/ak05_postgres_gate.py prepare` 创建显式本地隔离库，再对每个版本执行 `python scripts/dcp_reliability_gate.py --suite tasks --major 16 --config <本地四角色配置> --out <本次新目录>`，17 同理。配置不可上传；报告仅保留配置摘要。固定 35 个用例（01–03 的 13 项 + 04–06 的 17 项 + 07–12 的 5 项），拒绝跳过、重复、替换、错版本及运行中候选变化。
- 桌面／手机、开发／构建预览：`node scripts/run_e2e.mjs --mode both --suite tasks`。复用既有运行器和精确用例验证器，固定 20 个用例／模式，拒绝跳过和重试变绿。构建与源码摘要随报告保存。
- 04–06 快检增加 `nexus_backend/tests/test_tasks0406_invites.py`、`test_tasks0406_receipts.py`、`test_tasks0406_review.py` 和 `src/tasks0406.test.ts`；实际最终测试清单和证据见 04–06 报告。
- 夜间／完整集成继续使用已有 Accounting foundation、QA 与 P01 门禁，不另造一套业务测试。
- 07–12 新增 `local-quick.yml` 和 `env_doctor.py --json`，快速反馈不替代完整门禁。层级约定见 `scripts/test_layers.json` 和 [本地说明](TASKS0712_LOCAL_GUIDE.md)。
- 发布前：对同一完整候选重跑所需 PG、联合恢复、浏览器及容量门槛，获授权推送后核对远端 CI；本轮没有推送或发布授权。

CI 的 Accounting foundation 已接入 tasks PG16/17 矩阵和两个浏览器模式；工作流文件接线不等于远端已经执行通过。

2026-11-08：C01 直接执行 `python scripts/c01_postgres_gate.py --config <本地四角色源配置> --out <新的证据目录>`，入口自动新建一个独立库，不再直接向源库累积夹具。私有配置独立存放；固定 34 项、最小权限、迁移完整性和无跳过要求不变，证据改为完整前后端候选。其他门禁仍沿用各自既有准备流程，不能据此视为都已采用新入口。
