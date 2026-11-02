# DCP12-A 验收（部分）：候选冻结、证据盘点与发布计划草案

## 结论（分开写，不合并）

| 问题 | 结论 |
| --- | --- |
| 本地技术候选是否通过？ | **未通过完整验收**。核心回归与迁移门禁通过，但完整恢复演练、双企业/双期浏览器链、holdout 评测未完成。 |
| 外部真实验证缺什么？ | 缺 DCP08-C、DCP11-C、DCP06-C 成功路径、DCP04 PG17、真实通知与模型效果的独立证据。 |
| 是否获上线授权？ | **没有**。本任务只做本地可评审，未获部署授权；下方仅为计划草案。 |

## 一、候选指纹（`release_candidate.py`，本地字节）

```
head            5aa620b25553207b32da4656f1d5d8de05896eed
source_files    1735
source_sha256   0195a51ea3d1bf33c96250bc390043421107e328a2dbf43561bbf1760641cd70
migration_files 308
migration_sha256 4d2cb8c8a42438883c926f2d7783c1b2e8ce1c1b5c7d39436059c4cb8ef0a8f3
worktree_clean  false
```

**注意**：工作区不干净——并行智能体的 RSI 实验文件（`test_rsi_*`、`tools/ai_inference_lab/rsi/`、未跟踪迁移 `196_settlement_company_lock.sql`）仍在树里，因此该指纹包含未提交字节，**不能当作干净候选**。`check_release_candidate.py --mode release` 会因此直接拒绝。

## 二、本次已跑通的机器证据

| 项 | 结果 |
| --- | --- |
| 迁移幂等门禁 `check_migration_idempotency.py` | exit 0 |
| 助手套件（含 DCP11） | **161 passed / 39 skipped**（164 s） |
| 税务套件（DCP08 A+B） | 21 passed |
| 批量作业（DCP04 契约） | 7 passed |
| JIT 作用域（DCP09，含 PG16 池泄漏） | 4 passed |
| DCP01 组件测试 | 31 passed（4 文件） |
| DCP01 浏览器（1440×900 / 390×844） | 2 passed（2.9 min） |
| 客户简报浏览器（桌面+手机） | 1 passed（2.4 min） |
| DCP01+DCP03 回归 | 3 passed |
| ruff / tsc | 全绿 |

## 三、未完成（不得省略）

1. **完整恢复演练未做**：没有在 PG16/17 分别「建源库 → 备份 → 恢复到全新目标 → 原件 hash 校验 → 重新登录 → 继续业务」的闭环；DCP12-A 要求的 0 skip 恢复证据不存在。
2. **双企业隔离 / 两期衔接 / 跨年边界 / 多银行差额**的浏览器或服务级证据未跑。
3. **通知 unknown 不重发**、**AI attempt 恢复不重发**没有端到端证据（DCP07 只有 socket 级替身）。
4. **DCP11-C**（任务级回放/H01/AG05/holdout）完全未做；调查质量无证据。
5. **DCP08-C**（月度服务进度时间线）未实现；**DCP06-C 成功路径**未验证。
6. **PG17**：多包都只跑了 PG16；`p02_chain` 在 17 上仍停旧版本，迁移到 195 失败。
7. **远端 CI** 未运行，本报告不提供 Actions 链接。

## 四、生产与迁移现状

- 生产台账已到 **195**（191–195 均按字节应用并校验 checksum/compatibility）。
- 代码侧 PG 迁移目录因并行工作含 **196**（未跟踪、未提交）：**若 196 被提交而未先应用到生产，会重演 11-02 08:21 的崩溃**（`数据库包含当前代码未知的迁移版本`在旧镜像侧；新镜像侧则是 196 缺失）。推送前必须重新做「全量迁移清单 vs 台账」比对。

## 五、受控发布计划草案（仅文档，不执行）

1. **前置**：候选指纹重算且 `worktree_clean=true`；196 要么剔除，要么先应用并验证。
2. **迁移**：最低 191 → 目标版本 = 代码最大 PG 版本；逐文件按字节执行，写入 `schema_migrations(version, checksum, compatibility)`；执行后跑一次全量比对（missing/mismatched/extra 三项必须为空）。
3. **镜像顺序**：`accounting-migration` → `nexus-ai-kuaiji`（API）→ `accounting-worker` → `accounting-assistant`；每个服务启动后验 `/api/health` 与新路由 401。
4. **备份**：发布前对生产库做一次逻辑备份并记录 sha256；原件存储保持只读挂载。
5. **回退**：迁移只允许 `-- compatibility: incremental`；回退条件 = health 连续 3 次非 200 或启动日志出现迁移校验失败；回退动作 = 回滚镜像到上一 RELEASE，**不回滚已应用的增量迁移**（向前修复优先）。
6. **未知结果**：外发/模型 unknown 一律保留并转人工，不自动重试，不换模型。

## 六、当前缺陷清单（不掩盖）

- 批处理结算在 PG 上的权限缺陷已修（DCP10），但只做过单机验证；
- 月结失败的 `blocker detail` 仍偏粗（P02 F1）；
- 助手无模型 attempt 链，多步循环默认关闭；
- 本地演示缺少第二个复核人的问题已用 `reviewer` 身份缓解，但只覆盖示例账套。

## 七、本轮增补（2026-11-02 09:2x）

### 1. PG17 无法从 Windows 侧连通的真实原因（不是集群挂了）

```
pg_lsclusters:  16 p02pg16 25531 online / 17 p02pg17 25532 online
ss -lntp:       0.0.0.0:25531  0.0.0.0:25532（双栈监听正常）
Windows 127.0.0.1:25531/25532  connect 超时
Windows 172.24.221.111:25531/25532  connect 成功
```

即 **WSL2 的 localhost 转发失效**，而集群本身健康。后续 PG16/17 门禁应改用 WSL IP（`172.24.221.111`）连接，并确认 `pg_hba.conf` 对 `172.24.0.0/16` 放开 scram；不要再把「连接超时」当成数据库故障。

### 2. 迁移 196 的归属（并行工作，不进候选）

`196_settlement_company_lock.sql` 头部是 `-- compatibility: incremental`，内容为 `CREATE OR REPLACE FUNCTION accounting.settlement_open_item(...)` 等四个锁函数，属结算作用域的并行改造；同一时间工作区里 `test_stage10_settlements*.py`、`test_stage8_learning*.py` 等 8 个文件仍在被修改。结论：

- **196 及其测试不得进入本候选**，也不得由我提交；
- 推送前比对必须把 196 当成「代码领先于生产」的反例：若它被对方提交，生产台账（当前 195）会落后一版，必须先迁移再上线。

**本报告不宣布 DCP12-A 通过。**
