# N02/N03 PostgreSQL 持续门禁与同候选检查：本地验收

日期：2026-10-29。范围：本地隔离环境、虚构资料；未提交、未推送、未部署，未访问线上、真实模型或真实通知。

## 结论

本批本地验收通过。真实 PostgreSQL 16.15、17.11 各完成 N02 45 项、N03 55 项，均无跳过、失败或错误。门禁及既有 AK05/C01/独立复核门禁的离线组合回归为 237 passed。同一候选技术证据检查通过。

GitHub Actions 配置已接入，但本轮没有推送，因此没有新的远端 CI 运行结果。没有修改 GitHub 分支保护、Vercel/Zeabur 自动发布设置，不能声称平台已停止自动部署。技术通过不等于发布批准。

## 候选与修改归属

- 基线 HEAD：`e66c93699398bc462032078d8e510b51bdb1909a`，开始时工作区干净。
- 本轮仅修改 `.github/workflows/accounting.yml`、N02/N03 的两份 PG fixture，新增门禁、候选检查脚本、测试与文档。
- `nexus_backend/app`、`nexus_backend/migrations`、`src` 无修改；C01、既有 AK05 gate/runner 和财务业务规则未改动。
- 当前为该 HEAD 加本轮未提交修改，不能仅凭 HEAD 重建此候选；以后提交或修改代码后须重新取得匹配证据。

完整候选 `release-candidate-v1`：

```text
source_files: 1544
source_sha256: 57fa2ed00b183ac9e3a5aac1c797edb5295ac3a081c0b45b6e9eb88f575d6684
migration_files: 280
migration_sha256: 465f702a16d3710bd551bb6988f369257752d0206d6996817c72cd8c0c065993
required_cases_sha256: dd12dc10354e901df1b5efcef7311fb13f4c502c06582c64822ae67b53532ee0
```

`migration_files` 是 PostgreSQL 与 SQLite 迁移树的文件总数；本轮两个 PG 空库实际各应用并核验 166 个 PostgreSQL 迁移，不是只检查编号，也未修改历史摘要。

## 先红后绿

1. 配置缺陷先复现 3 failed：旧 N03 拒绝 CI 端口；必跑模式缺配置静默 skip；N02 在安全校验前迁移。统一 fixture 后目标校验专项 34 passed。
2. CI 合同先 8 failed：没有独立双版本 N02/N03 任务、没有相同候选汇总。接入后 8 passed。
3. 新精确用例门禁最初为模块不存在导致 collection error；实现后 59 passed。此红灯属于新功能契约，不冒称业务缺陷。
4. 候选检查先复现前端/依赖/部署文件不进入旧指纹的 3 failed；检查器新契约 32 failed；随后补网关制品与遗漏源码范围的 7 failed。新独立指纹不修改旧 AK05 合同，最后 55 passed。

这些负测涵盖：错目标/错角色/错版本、缺配置、漏项/重复/替换/skip、假计数、坏 XML、迁移或源码漂移、报告篡改、隐私日志、发布计划缺项和不一致。

## 实际 PostgreSQL 验收

本轮创建全新专属集群，未复用或修改既有 PG14:5432；只监听 127.0.0.1，端口分别 25541、25542。`fsync/synchronous_commit/full_page_writes` 均 on，SCRAM 随机密码。配置文件独立于报告目录且仅当前用户可访问。复用既有 AK05 prepare 建新库和受限运行、worker、projection 身份，未放宽授权。

| 实际版本 | N02 | N03 | 跳过/失败/错误 | N02/N03 JUnit 秒数 |
|---|---:|---:|---|---|
| PostgreSQL 16.15 | 45 | 55 | 0 / 0 / 0 | 16.088 / 68.123 |
| PostgreSQL 17.11 | 45 | 55 | 0 / 0 / 0 | 15.624 / 67.381 |

覆盖真实受限角色及原生 SQL 负向、来源版本/摘要、独立复核和旧稿导出阻断、撤权与来源竞争、双会话复核、通知两进程领取、发送围栏、发送标记后进程退出、未知结果不重发、恢复 hold/generation、慢本地通道不持企业写锁。通知通道为本地测试替身，不代表外部供应商验收。

原始证据（本机忽略目录，不含在源码提交中）：

```text
tmp_test/n02-n03-20261029-4686f01e94/cluster-evidence.json
tmp_test/n02-n03-20261029-4686f01e94/pg16-reports/{n02.xml,n03.xml,summary.json}
tmp_test/n02-n03-20261029-4686f01e94/pg17-reports/{n02.xml,n03.xml,summary.json}
tmp_test/n02-n03-20261029-4686f01e94/technical-summary.json
tmp_test/n02-n03-20261029-4686f01e94/release-blocked-summary.json
tmp_test/n02-n03-20261029-4686f01e94/stop-evidence.json
tmp_test/n02-n03-gates-final.xml
```

验收后核对数据目录与端口，已正常停止本轮两套集群，保留数据库和私有配置供复查；原 PG14:5432 仍运行。

## 持续门禁与发布边界

- `n02-n03-postgres-gate` 为独立 PG16/17 矩阵；复用准备工具，但不共享 AK05/C01 测试数据。
- generic verify 精确移交四个 PG 专项文件；专用任务冻结完整 100 个参数化用例身份，任何漏跑/跳过均失败，不能用旧 20 项或 SQLite 代替。
- `controlled-candidate-check` 依赖 verify、e2e、core regression、AK05 PG 和 N02/N03 PG 全部成功；取消或跳过也失败。
- 汇总重新核对原始 JUnit、报告 hash、迁移清单和当前完整候选；前端、依赖、网关、运行脚本、提示词或部署配置变化会使旧报告失效。
- `--mode technical` 本轮真实 CLI 返回 0，范围明确为 `n02_n03_only`；`deployment_authorized=false`。
- `--mode release` 本轮真实 CLI 按预期返回 1，原因是未提交工作区。单测另验证缺人工计划、前端网关/API/两个 worker 制品不一致、平台自动部署未知、缺兼容性/备份/回退说明均阻断。
- 人工发布计划只是本地记录。工具恒保留 `remote_state_verified=false`、`approval_verified=false` 和 `deployment_authorized=false`，不会自签批准或访问平台。

## 回归命令与边界

```powershell
.\.venv\Scripts\python.exe scripts/test_accounting_offline.py `
  nexus_backend/tests/test_n02_n03_ci_contract.py `
  nexus_backend/tests/test_pg_suite_target.py `
  nexus_backend/tests/test_n02_n03_postgres_gate.py `
  nexus_backend/tests/test_release_candidate.py `
  nexus_backend/tests/test_ak05_postgres_gate.py `
  nexus_backend/tests/test_c01_postgres_gate.py `
  nexus_backend/tests/test_review_policy_pg_gate.py -q
# 237 passed
```

Ruff check 通过；Ruff format 检查 app、gateway、scripts 及本轮测试共 325 文件通过；workflow YAML 解析及 `git diff --check` 通过。

实际两版执行命令和准备方式见 [N02/N03 门禁](N02_N03_PG_GATE.md)，发布预检方式见 [受控发布检查](CONTROLLED_RELEASE_CHECKS.md)。

本轮不重跑整仓前端、全套后端或完整 C01 恢复演练，不将旧证据算作本候选全产品验收。没有执行线上迁移，线上数据库状态未核验。后续发布仍需在提交后的同一候选完成远端必需门禁，取得明确发布授权并核验平台状态。
