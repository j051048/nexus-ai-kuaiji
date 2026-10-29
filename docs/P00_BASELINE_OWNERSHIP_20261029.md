# P00 基线、改动归属与安全交接（2026-10-29）

## 结论

1. 交接基线 HEAD `e66c9369` 之上，本轮启动前已有 **2 个提交**（`ba1b7b32`、`e60be052`），内容恰好是交接里“15 个未提交路径”的字节；工作区干净。
2. 候选指纹与交接完全一致：`source_sha256 = 57fa2ed00b183ac9e3a5aac1c797edb5295ac3a081c0b45b6e9eb88f575d6684`、`migration_sha256 = 465f702a16d3710bd551bb6988f369257752d0206d6996817c72cd8c0c065993`（1544 个源文件 / 280 个迁移文件）。
3. **平台自动部署为开启状态**：Zeabur 通过 GitHub App 集成，四次推送后 9–22 秒内各出现一次自动部署；GitHub webhook 列表为空不构成反证。
4. 本轮（P00–P05）**不推送、不部署、不改线上配置**；若后续需要推送，必须先迁库再推（10-27 12:04Z 事故的直接教训）。

## 1. 基线核对

| 项 | 交接值 | 实测值 | 判定 |
| --- | --- | --- | --- |
| HEAD | `e66c9369…` | `e60be0523196391403d434f39c5daa285615baac` | 前进 2 个提交，已核对内容 |
| 工作区 | 15 个未提交路径 | 干净（`worktree_clean() == True`） | 一致 |
| source_sha256 | `57fa2ed0…6684` | `57fa2ed0…6684` | 一致 |
| migration_sha256 | `465f702a…6593` | `465f702a…6593` | 一致 |
| 源文件 / 迁移 | — | 1544 / 280 | — |

指纹在 HEAD 前进后仍一致，是因为这两次提交只新增 `.github/workflows/accounting.yml` 的字节、`scripts/`、`nexus_backend/tests/` 与 `docs/`；前 11 个文件已在指纹范围内，文档不在。**结论：交接的 15 个路径已被原样提交，未夹带其他改动。**

复算命令：`python -c "import sys;sys.path.insert(0,'scripts');from release_candidate import candidate_fingerprint;print(candidate_fingerprint())"`。

## 2. 改动归属

### 2.1 交接的 15 个路径（已提交，归属：上一轮 N02/N03 门禁任务）

| 路径 | 提交 |
| --- | --- |
| `.github/workflows/accounting.yml` | `ba1b7b32` |
| `scripts/check_release_candidate.py`、`scripts/n02_n03_postgres_gate.py`、`scripts/pg_suite_target.py`、`scripts/release_candidate.py`、`scripts/n02_n03_postgres_required_cases.json` | `ba1b7b32` |
| `nexus_backend/tests/test_n02_n03_ci_contract.py`、`test_n02_n03_postgres_gate.py`、`test_pg_suite_target.py`、`test_release_candidate.py`、`test_n03_notification_postgres.py`、`test_tax_workpaper_postgres.py` | `ba1b7b32` |
| `docs/CONTROLLED_RELEASE_CHECKS.md`、`docs/N02_N03_GATE_ACCEPTANCE_20261029.md`、`docs/N02_N03_PG_GATE.md` | `e60be052` |

### 2.2 同一会话更早的提交（归属：N03/N04 交付任务，非本轮）

`28b0bcd9`（N03 派发围栏，PG 174–178 / SQLite 110–114）、`d607ab1e`（N01/N03/Paddle 文档）、`62a4bc66`/`be0cb0ca`/`e26cc9f6`/`e66c9369`（N04 税务底稿与客户签收前端）。

### 2.3 本轮 P00–P05

本轮起始时无未提交改动，因此 **本轮新增文件即本次改动全集**，不与上述任务混写；本轮不修改已应用迁移的历史内容或 checksum。

## 3. 自动部署核验（只读）

| 推送提交（作者时间，+08:00 / UTC） | Zeabur api 部署记录（UTC） | 间隔 |
| --- | --- | --- |
| `e60be052` 16:08:51 / 08:08:51 | `08:08:59.702` | 8.7 s |
| `e66c9369` 12:33:45 / 04:33:45 | `04:34:07.561` | 22 s |
| `d607ab1e` 10:45:44 / 02:45:44 | `02:45:59.268` | 15 s |
| `28b0bcd9` 10:45:40 / 02:45:40 | 同上（同一部署） | — |

- 四个服务（api/worker/assistant/migration）均跟随同一提交重建，当前运行 `e60be052`。
- `gh api repos/j051048/nexus-ai-kuaiji/hooks` 返回空列表；但部署确实被触发，说明接的是 **GitHub App 集成**而非仓库 webhook。空 webhook 列表、`PREBUILT` 服务类型、单次 READY 都不能作为“不会自动部署”的证据。
- 前端：仓库内 `.vercel/project.json` 存在（`prj_5hFbkotai2ZU3zc7oir8ijFLg0uS`），Vercel CLI 本机无凭据，**无法确认**其 Git 集成状态；按“可能自动部署”对待。

结论：**任何推送都会在约 10–25 秒内触发生产重建**。推送前必须完成迁移应用与回滚预案。

## 4. 安全边界

- 本轮不推送、不部署、不改平台配置；所有验证使用本地隔离 PostgreSQL 与虚构数据。
- 不读取、不输出 `tmp_test` 下的凭据文件内容；确认 `tmp*` 已被 `.gitignore:134` 覆盖（`tmp_test/n01-pg.local.json`、`tmp_test/qr01-pg-credentials.local.json` 均被忽略）。
- 不通过 `skip-ci`、新建分支或关闭自动部署来规避验收。

## 5. 未验证事项

1. Vercel 的 Git 自动部署是否开启（本机无凭据，需平台侧确认）。
2. 交接所述“两套测试集群已停止”仅验证到端口无监听，未核对数据目录完整性。
3. 交接的 PG 结果（N02 45 / N03 55）未在本轮重跑，仅确认脚本与用例清单存在；复现计划放在 P01。
