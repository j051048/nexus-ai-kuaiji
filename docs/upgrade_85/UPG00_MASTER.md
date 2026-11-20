# UPG 总令：全维度冲刺 8.5+ 联合改良工作令（DeepSeek 分批执行）

- **编排日期**：2026-11-19
- **代码基线**：HEAD `074c7ad1`（PostgreSQL 迁移 `v303` / SQLite 迁移 `v218`，已承接 `docs/rework/RWK_STATUS.md` 中完成的 `RWK01-A`）
- **锁定解释器**：`.venv/Scripts/python.exe`（`Python 3.14.1`，与 `nexus_backend/requirements.lock` 对齐）
- **状态台账**：`docs/rework/UPGRADE_85_STATUS.md`（追加记录，不得覆盖历史）

---

## 1. 目标与诚实边界

本总令目标是将「衡账（Nexus-AI）」共享财务中台在**十大评估维度**（业务与场景价值 5 维 + 阿里/谷歌级 SaaS 平台工程 5 维）全面推进至 **8.5 分以上**。

必须遵守的客观工程边界：

- **纯本地工程可突破 8.5+ 的 8 个维度**：维度 1（易用与效率工程面）、维度 2（多法人合规管控）、维度 4（会计自动化与凭证）、维度 6（多租户与隔离）、维度 7（安全与合规工程）、维度 8（可扩展与高可用）、维度 9（可观测与运维）、维度 10（Agent + 工作流中台）。
- **需外部授权联调解封最后 0.5~0.8 分的 2 个维度**：维度 3（税务自动化：本地做到 8.0+ 沙箱闭环，真实电子税务局直连待授权）、维度 5（银企直联与资金：本地做到 8.0+ 沙箱与双行适配器闭环，成都银行/工商银行真实前置机联调待行方授权）。此外，5 人替代 15 人的最终定编结论必须依赖客户连续两个真实会计月的双月记分表签认。
- **严禁为“报满 8.5+”而造假**：任何缺乏真实客户/银行/税局/模型凭证的外部项，必须交付“标准协议 + 本地沙箱替身 + 现场移交清单”，并在状态台账中将**工程状态**与**外部联调状态**严格分开写明。

---

## 2. 全局执行铁律（每批无条件遵守）

1. **解释器与环境校验**：所有后端命令必须使用 `.venv/Scripts/python.exe`；每批开工前先执行 `.venv/Scripts/python.exe scripts/check_test_env.py` 确认退出码为 `0`。
2. **五步工程纪律**：核实现状 → 先写失败测试（红测）→ 最小精准实现 → 原断言转绿 → 同域回归与门禁复验。
3. **禁止注水与作弊**：严禁删断言、加 `skip`/`xfail`、放宽超时、修改 `scripts/frontend_budget.mjs` 体积上限、用截图替代断言、改写任务要求后再报完成。若认为某条要求不合理或遇到业务规则冲突，停止修改该项、登记至 `docs/rework/DECISIONS_NEEDED.md` 并标为`阻塞`，随后切入下一无依赖子项。
4. **双库与 PostgreSQL 防崩溃红线**：
   - 严禁修改任何已存在的历史迁移文件及其 checksum；
   - 确需新增结构变更时，**必须先检查 `nexus_backend/migrations/sqlite/` 与 `nexus_backend/migrations/postgres/` 最新编号再顺延双写前向迁移**；
   - 所有新增 PostgreSQL 业务表必须 `ENABLE ROW LEVEL SECURITY` 且 `FORCE ROW LEVEL SECURITY`，并严格拆分 `SELECT` 与写策略权限；
   - 严禁编写仅 SQLite 合法而 PostgreSQL 报错 `DatatypeMismatch` 的 SQL：禁止 `AND ?` / `OR ?` 传整型 `1/0`（必须写 `?=1`），禁止在 `UNION ALL` 中混排 `TIMESTAMPTZ` 与 `TEXT`（必须显式 `CAST(... AS TEXT)`）；
   - 所有 PostgreSQL 触发器函数中的 `TG_NAME` 白名单必须与实际 `CREATE TRIGGER` 名称严格一致。
5. **外部与仓库红线**：不访问真实生产库、真实大模型、真实银行/税局/OA/通知通道；禁止执行 `git commit / push / reset / clean / stash / checkout --`，禁止全仓格式化，禁止触发部署。
6. **状态值唯一枚举**：仅允许使用 `完成` / `部分完成` / `阻塞` / `已满足无需改动` / `工具就绪待客户输入`，并在每批记录中单列 `与提示词要求的差异`（一致写 `无`）。

---

## 3. 执行批次总览（Batch 01 ~ Batch 10）

| 批次 | 文件 | 核心主题 | 前置 | 主攻提分维度 |
| :---: | :--- | :--- | :---: | :--- |
| **Batch 01** | `docs/upgrade_85/UPG01_ENGINEERING_GATES.md` | 工程止血、回归清零与 PG 强制门禁（承接 RWK01-B/C/D + RWK02） | `RWK01-A` | 维度 8、6、9 |
| **Batch 02** | `docs/upgrade_85/UPG02_REPORT_AND_WORKBENCH.md` | 报表合规封堵与多法人高频工作台三件套（承接 RWK03 + RWK04） | Batch 01 | 维度 1、2、4 |
| **Batch 03** | `docs/upgrade_85/UPG03_AUDIT_AND_RECOVERY.md` | 验收包补全、有数据审计链验真与恢复后办单（承接 RWK05 + PLT06） | Batch 02 | 维度 2、6、7、8 |
| **Batch 04** | `docs/upgrade_85/UPG04_TAX_AUTOMATION.md` | 四税种全闭环底稿、进项风控与电子税务局连接器沙箱 | Batch 02 | 维度 3、2 |
| **Batch 05** | `docs/upgrade_85/UPG05_TREASURY_BANK_H2H.md` | 独立出纳、付款审批流与成都银行/工商银行双行适配器 | Batch 02 | 维度 5、2、1 |
| **Batch 06** | `docs/upgrade_85/UPG06_INDUSTRY_AND_TPLUS.md` | 出租车断点清零、五行业专用处理器与 PLT05 收口 | Batch 02、05 | 维度 4、1 |
| **Batch 07** | `docs/upgrade_85/UPG07_SECURITY_SSO_KMS.md` | 企业级 OIDC SSO、KMS 信封加密、多租户配额与 CI 安全门禁 | Batch 01、03 | 维度 7、6 |
| **Batch 08** | `docs/upgrade_85/UPG08_CLOUD_NATIVE_OBSERVABILITY.md` | 云原生无状态外置（Redis/S3）与可观测三支柱（OTel/Prometheus/告警） | Batch 01 | 维度 8、9 |
| **Batch 09** | `docs/upgrade_85/UPG09_WORKFLOW_AND_AGENT.md` | 配置化可视化工作流中台与 PLT08 调查助手 60 样本闭环 | Batch 02、05 | 维度 10、1 |
| **Batch 10** | `docs/upgrade_85/UPG10_SHARED_QUEUE_AND_FINAL_GATE.md` | 五人共享总控队列、集团合并试算与全仓同候选终验 | Batch 01–09 | 维度 1、2、6 及总验收 |

---

## 4. 状态台账记录规范

每完成一个批次，在 `docs/rework/UPGRADE_85_STATUS.md` 末尾追加如下固定结构（涉及 `RWK` 原模块的同步更新 `docs/rework/RWK_STATUS.md`）：

```markdown
## Batch XX <主题>
- 工程状态：<完成 / 部分完成 / 阻塞 / 已满足无需改动 / 工具就绪待客户输入>
- 外部状态：<无需外部 / 待资料 / 待授权联测 / 待业务签认>
- 与提示词要求的差异：<逐条写；完全一致写“无”>
- 改动与新增文件：<逐项列出>
- 红测与修复记录：<失败现象 -> 根因 -> 修复>
- 验收命令与真实输出：<原样贴入命令与核心汇总行>
- 剩余外部依赖或未决项：<无 / 清单>
```
