# FSC12 同候选联合验收（2026-11-17）

状态：**部分通过（本地同候选联合回归）；不能宣布五企业受控试点或五人运营完成。**

## 候选

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source：`2359 / ce958c8b0605ff66b834203527b39bc5e6d70dea6265ee5f26a29ff8dc9c0bf5`
- migration：`469 / d266a53a73a5a3237cfe6e3676497acc43294ddb6d25466dbf88fb5b5fefb6d7`
- 工作区未提交；未推送/部署。

## 本候选实际通过

| 项目 | 命令 | 结果 |
| --- | --- | --- |
| FSC 后端 SQLite 联合 | `pytest tests/test_fsc01_profiles.py ... test_business.py::test_fsc12_five_person...`（17 个文件/用例） | **52 passed/82.68s** |
| PG17 FSC13–17 + 跨年租赁联合 | `test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json ...` | **21 passed/10.14s**，artifact `tmp_test/ak05-pg-runs/42881745e44f43199d51e3c11fb93350` |
| PG16 FSC13–17 + 跨年租赁联合 | 同上 `env16` | **21 passed/10.07s**，artifact `tmp_test/ak05-pg-runs/a8fe5344140e4555ba9c3039ae5d0ffb` |
| 两家企业跨年租赁链 | `test_two_companies_cross_year_lease_chain_keeps_drafts_isolated` | PG17 1 passed/1.98s、PG16 1 passed/1.97s；2026-12/2027-01 草稿企业隔离 |
| 浏览器联合 | `fsc13-closing tax-f fsc15-rates fsc16-packages finance-operations` | **12 passed/3.5m**（1280/390/1365/390） |
| 五人复核与正式过账仿真 | `test_fsc12_five_person_two_tasks_are_reviewed_and_posted_without_duplication` | **1 passed/8.56s**：员工/部门/财务/经理/会计五身份，两笔事项、两草稿、并发复核、冻结期初与科目映射后正式过账；两张正式凭证，未重复 |

> 五人正式过账已通过：fixture 使用冻结期初、显式长期科目与 `ledger_account_mappings`，未放宽 `post_draft`。
| 前端完整单测 | `npm test -- --run` | **152 files/1403 tests passed** |
| 前端类型/构建 | `npm run typecheck`、`TMP/TEMP=tmp_test/tmp-build npm run build` | 通过；index 566.72kB/gzip 175.55kB；CSS 99.95kB/gzip 20.32kB |
| 分项恢复 | FSC13/15/16/17 各自 SQLite 备份/verify/restore | 已通过（见各批证据） |

## 同候选真实业务链覆盖

- FSC13：未确认权威阻断、internal 外部证据仅核对、幂等/异内容冲突、posted 内部后外部证据 conflict、权威变化使旧草稿复核失效。
- FSC14：租赁 v1 草稿在 v2 改版后审核/记账/导出拒绝；浏览器重复动作与竞态修复。
- FSC15：费率草稿/独立确认/生效/续期/停用/历史/影响；停用后复核拒绝；跨年 PG；前端生命周期。
- FSC16：包干合同版本/独立确认/生效；额度占用/释放/历史；跨所/跨账套拒绝；并发仅一笔成功。
- FSC17：资产—车辆稳定关联、版本/指纹、撤销历史、折旧 aux/source_ref、审核/记账/导出、恢复。
- 浏览器失败修复：FSC15 选择器按 `details.tax-cost-rates` 作用域；tax-f 慢请求等待 20s；`review-policy` 对旧版缺少 `firm_control` 的 API 兼容默认 false，不把历史口径误判为分离开启。

## 恢复尝试（未通过）

- 命令：`C01_FULL_RECOVERY_ACCEPT=1 P01_CONFIG_DIR=.codex/c01-final-pg python -m pytest -q nexus_backend/tests/test_c01_full_recovery_postgres.py --maxfail=1`
- 结果：**blocked**。C01 源库目标 `p01_source_17_...` 在 25498 不存在（该端口当前承载 FSC02 的 auth 库）；25499/25496/25497 的 `p01_admin` 密码认证失败。恢复在 migrate 源库前终止；未执行 pg_dump/restore，未生成恢复证据。
- 这是本地私有环境端口/库名与凭据状态变化，不是产品恢复失败；不得当作通过。源/目标不得复用其他任务集群，也不改密码或伪造库。

## FSC12 未完成 / 不能冒充通过

- 五名真实测试身份的分工仿真、请假/峰值/接手/责任不冲突未执行。
- 同时备份“完整数据库 + 原件存储”并恢复到**全新隔离库/存储**、身份重映射、hold 解除、恢复后继续办理未执行；当前只有分项 SQLite 恢复。
- **HTTP 容量未通过（有改善）**：隔离 SQLite API（`ACCOUNTING_QUEUE_EMBEDDED_WORKER=0`，单 uvicorn 进程，显式 `ACCOUNTING_SQLITE_READ_REUSE=1`）实测 50 并发读 200 次：p50 **588.94ms**、p95 **4,716.32ms**；100 并发读 300 次：p50 **1,175.77ms**、p95 **5,418.26ms**。写 50/100 并发 p95 分别 2,651.31ms / 3,805.74ms，全部 200/201。读 p95 从 16.5s/29.1s 降至 4.7s/5.4s，但仍达不到 QA05 PG 基线（100 并发 p95 ≈1.6s），且本轮为单进程 SQLite，不足以证明多 API/PG 容量。未通过加连接/无限排队或放宽错误率改善数字。
- 有界容量/月末峰值/慢存储/worker 租约/多 API+worker/资源增长与恢复时间仍未完成。
- 两企业跨年**租赁草稿链**已通过；但同链的多银行、换车/合同变更、预收退款、模板升级、撤权、旧下载链接、外部结果 unknown 仍未完整执行。
- FSC13–17 外部真实 T+/OA/保险单/合同/客户资料/真机/人眼读屏均未验证。
- 因此不能宣称“五企业受控试点已具备”或“15→5 已实现”。

## 结论

本地工程候选在同候选下通过了 FSC13–17 核心联合回归、SQLite/PG16/17 与桌面/手机浏览器主链；但 FSC12 的完整双库+原件恢复、容量、五人正式过账/真实工时和真实外部/客户门槛仍未完成。FSC12 状态保持**部分通过**。
