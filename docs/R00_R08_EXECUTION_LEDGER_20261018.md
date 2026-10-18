# R00–R08 执行台账（2026-10-18/19）

参考起点：`f37ee6e8`；执行期间并行提交 `bb19fa43`（未由本任务提交）  
实际工作区：本地未提交 diff；**未提交、未推送、未部署、未操作线上**

---

## 1. 环境与基线

| 项 | 实际 |
|---|---|
| HEAD | `bb19fa43`（并行提交）+ 本地未提交 R00–R08 修复 |
| PostgreSQL 最新迁移 | `126_batch_claim_cap.sql` |
| SQLite 最新迁移 | `086_subledger_reallocation.sql` |
| 本机 PG | WSL Ubuntu 22.04 / PostgreSQL **14.24** |
| 测试库 | `accounting_b08_local`，空库迁移 1–126 |
| 受限角色 | `b08_admin`（迁移/测试管理）、`b08_runtime`（API）、`b08_worker`（worker） |
| CI | PG16 容器配置存在；本机未跑 CI |
| Docker | 未安装/未验证 |
| 证据边界 | PG14 不能替代 PG16/17；SQLite 不能替代真实 PG |

---

## 2. F01–F14 当前状态

| 编号 | 状态 | 修复/证据 | 未完成 |
|---|---|---|---|
| F01 报表复核权限 | 局部修复 | `reports.review/publish` 同事务复核权限/版本/自审；`b04_close.publish` 复核；PG `book_review_allowed` 触发器 | PG 触发器未单独动态测试 |
| F02 期末原子记账 | 局部修复 | `post_manual_in_transaction`；`periodic.post` 单事务；PG/SQLite 唯一索引 | PG 并发/故障注入未跑 |
| F03 锁期预检失效 | 局部修复 | 锁期事务内重新检查未审草稿/期末草稿；测试预检后新增草稿被拒 | 子账/银行/报表完整 revision 未覆盖 |
| F04 缺子账通过 | 修复 | 按 1122/1123/2202/2203 反向枚举控制科目；测试通过 | 自定义控制科目配置未建模 |
| F05 未分类现金净额抵消 | 修复 | 增加笔数/绝对额并纳入 `cash_flow_passed`/validate；正负各 100 元测试通过 | 无 |
| F06 多项核销唯一约束 | 修复 | `subledger_allocation_requests` 请求头；明细键加 item 后缀；测试通过 | PG 并发核销未跑 |
| F07 撤销额度不释放 | 修复 | 额度扣除已撤销 allocation；PG 124/SQLite 086 移除旧唯一约束；同事项重配测试通过 | PG 并发重配未跑 |
| F08 月结详情越权 | 修复 | `get` 走 `_run` 领域授权；测试他企/不存在 ID 安全 404 | PG RLS 下同一测试未单独跑 |
| F09 无银行资料通过 | 修复 | 覆盖数/期初现金/差额检查；测试无资料 `passed=false` | 多银行账户/期间覆盖未建模 |
| F10 consumed intent 备份失败 | 修复 | 对象内容身份与 references 分离；测试同键附件+intent 通过 | 完整新目标恢复未跑 |
| F11 一般异常绕过重试上限 | 修复 | worker 达上限转 failed；finish 封顶；SQLite/PG claim 排除耗尽；PG retry_wait 转 failed 触发器 | PG 并发 claim 未跑 |
| F12 stale 重放 | 局部修复 | stale 不自动换键、禁用重复识别、明确提示 | 无 stale 浏览器动态测试 |
| F13 识别恢复无刷新 | 局部修复 | running 时 2.5s 有界轮询，切 scope/卸载取消 | 无 running 恢复浏览器动态测试 |
| F14 原件失败仍可确认 | 局部修复 | `canApprove`/勾选同时要求无 `error`/`evidenceError` | 无原件 5xx 浏览器动态测试 |

---

## 3. R00–R11 状态

| 批次 | 状态 | 说明 |
|---|---|---|
| R00 基线与真实 PG | 局部通过 | PG14 空库 1–126、20 个 PG 测试；PG16/容器未跑 |
| R01 权限与原子记账 | 局部通过 | F01/F02/F08 修复；F03 部分 |
| R02 备份与恢复隔离 | 局部通过 | F10 修复；文件+DB hold；完整恢复未做 |
| R03 子账/核销/银行 | 局部通过 | F04/F06/F07/F09 修复；PG 并发未跑 |
| R04 月结/现金流/报表 | 局部通过 | F03/F05 代码修复；完整连续期未验收 |
| R05 任务恢复/用户路径 | 局部通过 | F11–F14 代码；inline/intake 浏览器通过，故障场景未跑 |
| R06 AI 政策 | 局部通过 | 生产无 managed policy 阻断；7 个 policy 测试 |
| R07 旧账/连续期/证据 | 部分 | B08 工具 19 测试；真实资料未提供 |
| R08 完整技术验收 | 未完成 | 完整恢复/容器/浏览器故障/连续期/性能未齐 |
| R09 真实试点 | 仅准备 | 无真实资料/真人授权 |
| R10 Agent 扩展 | 仅规划 | 未新增平台 |
| R11 私有化/规模化 | 仅规划 | 未验证断网/容量 |

---

## 4. 新增迁移

| 迁移 | 内容 |
|---|---|
| PG 120 / SQLite 083 | 报表/月结复核权限触发器；periodic posted voucher 唯一索引 |
| PG 121 / SQLite 084 | 核销请求头表 |
| PG 122 / SQLite 085 | `restore_state` 持久恢复 hold |
| PG 124 / SQLite 086 | 移除核销旧唯一约束，支持撤销后同事项重配 |
| PG 125 | PG 批量 retry_wait 达上限转 failed 触发器 |
| PG 126 | PG claim 排除已耗尽 attempt |

历史迁移未修改；空库迁移成功，`migrate status` 无 checksum 不匹配。

---

## 5. 实际测试证据

### R01/R03 定向

```powershell
pytest test_r01_r03_regressions.py -q
# 9 passed
pytest test_b04_pilot.py -q
# 3 passed
pytest test_b04_api.py -q
# 1 passed
```

覆盖：F01 editor 复核拒绝、F02 注入审计失败回滚、F04 无明细失败、F06 两项核销、F07 同事项撤销后重配、F08 他企详情 404、F09 无银行资料失败、F11 重试上限。

### 备份/恢复

```powershell
pytest test_stage10_backup_recovery.py -q
# 11 passed
pytest test_restore_hold.py -q
# 5 passed
```

### 批量、AI、B08

```powershell
pytest test_batch_operations.py -q   # 4 passed
pytest test_b05_policy.py -q         # 7 passed
pytest test_b08_acceptance.py -q     # 19 passed
```

### 真实 PostgreSQL

```powershell
pytest test_postgres_adapter.py test_postgres_integration.py -q
# 20 passed
```

环境：WSL PostgreSQL 14.24；空库迁移 1–126；`b08_runtime`/`b08_worker` 受限角色。  
**未跑：** PG16、并发故障注入、完整恢复、容器。

### 浏览器

```powershell
npx playwright test e2e/stage12-inline-review.spec.ts
# 1 passed (59.5s)
npx playwright test e2e/stage12-intake.spec.ts
# 2 passed (1.1m)
```

覆盖：正常同屏复核、员工上传/恢复/部门审批、手机录入。  
**未覆盖：** F12 stale、F13 running 恢复、F14 原件 5xx 的专门故障场景。

并行提交的 `firm-mfa-recovery.spec.ts` 本次运行 **1 passed / 1 failed**（“客户标准化开通”标题未出现）；该文件由其他执行者提交，本任务未修复，也不计入通过。

### 前端

```powershell
npm run typecheck
npm run lint
npm test
# 43 files / 345 tests passed
npm run build
# 通过（主 chunk >500 kB 警告）
```

---

## 6. 当前阻断

1. F03 仅补未审草稿/期末草稿重检，子账/银行/报表完整 revision 仍缺。
2. R02 完整恢复（身份/角色/RLS/对象存储/队列费用）未执行。
3. PG16/容器、CI 未运行；PG14 不能替代。
4. F12–F14 专门浏览器故障场景未跑。
5. F01/F02/F06/F07 PG 动态并发未跑。
6. R08 未在同一候选版本完成全链路、故障注入、升级回退、性能。
7. R09 真实资料/人员/签收未提供；R10/R11 仅规划。

**结论：R00–R07 局部完成，R08 未完成；不能宣布本地技术候选验收通过，不能进入真实试点或切换。**
