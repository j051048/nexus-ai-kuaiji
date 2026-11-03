# NEXT 执行状态台账

- 更新时间：2026-11-03 18:02（Asia/Shanghai）
- 仓库：`C:\Users\Fei\Desktop\AI应用\nexus-ai-kuaiji`
- 分支／HEAD：`main` / `0a0c6cab4ba18d2dfe5ce695b6844e01895c204b`
- 最终候选（`tmp_test/next/next11-final/candidate.json`）：`source_files=1821`，`source_sha256=d3f7a1fbd9163d2b4f78d2fe26a39e85aafc3e7a0671993267f7406f61fc2f07`；`migration_files=328`，`migration_sha256=8bbd6f3fdb46addee9bb8737775ed7966f4a2938c8ea413d7c4d4952dd76444f`。该候选含并发任务的 `TaxFilingRecords.tsx` 修改与另一任务的提交 `0a0c6cab`，归属见 `NEXT_OWNERSHIP.md`。
- 工作区：已提交 `154537f7` 并推送 `origin/main`；并发任务仍有未提交修改（CI 文档、`NEXT_PHASE_DECISIONS.md`、`test_next_p0_boundaries.py` 等），不纳入本轮提交。未部署。
- 基线候选（2026-11-02）：1790/`3fc6fc89…`、316/`f07ba1cd…`；历史 QA01–QA05 只对应旧 HEAD，不作为本轮通过依据。

## 状态表

| 工作包 | 工程状态 | 外部验收 | 关键证据／剩余 |
| --- | --- | --- | --- |
| NEXT01 统一候选／资源治理 | **本地验收通过（最终候选）** | 不适用 | 57bb manifest passed、stable、cleanup passed；14+4+3 通过 |
| NEXT02 真实浏览器主链 | **57bb 单期主链通过（8 passed/28.8s）** | 不适用 | 两企业/独立角色/桌面手机/权限负向/银行原件/月结/报表/包/签收均在 57bb 完成；跨年 HTTP/PG 已补 6+6 passed，但跨年浏览器、连续两期、多银行仍未验 |
| NEXT03 安全边界 | 进行中（57bb 静态/HTTP 回归通过，动态探测已 57bb 重做，限流外部门槛） | 未授权：生产身份边缘限流 | 57bb 7 文件 **188 passed**／60.23s；19 项动态探测 **19/19 passed**（57bb，`next03-security-probe-57bb.json`）；登录限流无 429 证据 |
| NEXT04 人工接手时间线 | 本地工程通过（四类来源；税审/交付包浏览器通过） | 不适用 | d3f7 SQLite **15 passed**/33.81s、PG16/17 各 **28**/34.20s/33.34s；税审浏览器 1 passed/31.8s（cf0fe 历史）；交付包 reviewer 队列+SPA 原记录 **1 passed/14.9s**（d3f7，`tmp_test/next04-delivery.spec.ts` 不在候选内）；批量/通知浏览器未验 |
| NEXT05 税务证据闭环 | **后端、离线与浏览器通过（57bb）** | 不适用（不接真实税局） | 57bb harness 70 passed + tsc；浏览器 1 passed／33.0s：登记、上传 2×2 PNG、下载 SHA-256 一致、更正历史；PG 月度包 4 passed 为历史候选 |
| NEXT06 通知上线准备 | **本地验收通过（57bb）** | 缺少真实供应商沙箱 | 57bb manifest passed、stable、cleanup passed；notification 280.42s、delivery 180.51s，66+42 通过；未发真实消息 |
| NEXT07 弱网／移动／纠错 | **本地浏览器验收通过（d3f7）** | 真机/微信/屏幕阅读器未验证 | d3f7 25 passed／1.4m（3 引擎+手机）；修复修订迟到响应覆盖人工修改；真实断网/上传中断未注入 |
| NEXT08 批量导出交付 | **本地验收通过（57bb）** | 不适用 | 57bb SQLite 69、PG16/17 各 10、真实浏览器批量开期 1 passed／28.8s；真实对象存储/多进程 worker 未验 |
| NEXT09 HTTP／多实例／容量 | 进行中（50 clients 通过；100 clients 边界失败） | 生产容量/多节点未验证 | 57bb 三阶梯 5/20/50 clients、0 error；100 clients 诊断 132 次 queue_full、68 成功（单进程 4 连接池）；未测万行导入、多 API、数小时稳定性；无 PG CPU 采样 |
| NEXT10 助手任务效果 | **本地离线验收通过（57bb）** | 真实模型/客户未授权 | 57bb 353 passed、六场景、answer 3/3；`real_model_evaluated=false` |
| NEXT11 同候选联合验收 | 进行中（A 仍未整体通过） | B/C/D 未完成 | d3f7 上 N04/N07 与 P01 通过；N01/02/03/05/06/08/09/10 证据在 57bb 同后端候选；N02 仅单期，N04 批量/通知来源浏览器未验，N09 100 clients 失败；未推送、未接真实供应商 |

## 最终候选上的重跑

| 套件 | 结果 | 证据 |
| --- | --- | --- |
| P01 完整恢复门禁 | **passed**；regression 155／153.216s、joint 2／82.368s、identity 4／58.671s；PG16/17 的 database/originals/identity/permissions/hold/review_export/tax_delivery/no_replay 全 true | `tmp_test/next/next11-recovery-d3f7-r2/reports/summary.json` |
| NEXT01 | 57bb 14+4+3 passed；manifest passed/stable/cleanup passed | `tmp_test/next/next01-acceptance-20261103-57bb/` |
| NEXT02 | **8 passed / 28.8s（57bb）**，真实 GoTrue＋PG17＋preview，fresh fixture | Playwright line 输出；截图 `.codex/p02-r1-browser/results/`；fixture `tmp_test/next/next02-browser-57bb/fixture-private.json` |
| NEXT03 安全回归（静态/HTTP 层） | 57bb 188 passed／60.23s | `tmp_test/next/next03-security-57bb.xml` |
| NEXT04 | 31 passed；manifest passed/stable；PG16/17 各 28 | `tmp_test/next/next04-acceptance-20261103-linkfix/`；`tmp_test/ak05-pg-runs/fdf30380…`、`eb332ec7…` |
| NEXT05 | 57bb 70 passed／203.82s + tsc／exit 0；manifest passed/stable | `tmp_test/next/next05-acceptance-20261103-57bb/` |
| NEXT06 | 57bb notification 66／280.42s，delivery 42／180.51s；manifest passed/stable | `tmp_test/next/next06-acceptance-20261103-57bb/` |
| NEXT07 | 25 passed／1.4m，3 引擎 + 手机 | `tmp_test/next/next07-browser/report.json` |
| NEXT08 | 57bb SQLite 69／145.71s；PG17 10／13.14s；PG16 10／13.07s；浏览器 1／28.8s | `tmp_test/next/next08-sqlite-57bb.xml`、`ak05-pg-runs/93a8cb31…`、`6ccdbdd2…` |
| NEXT09 | 57bb `qa_mixed` 10 公司/1 轮 passed；5/20/50 两轮均 0 error/0 reject；两 worker 各 5 项；守恒通过 | `tmp_test/next/next09-capacity-57bb/report.json` |
| NEXT10 | 57bb 353 passed；answer 3/3；`real_model_evaluated=false` | `tmp_test/next/next10-assistant-57bb/` |

## 本地环境（本轮自建）

- Python：仓库内 `.venv`（Python 3.14.1）；Node v26.5.0／npm 11.17.0；Playwright 1.58.2。
- WSL PG16/17 四集群：本轮自建端口 25496–25499，root `$HOME/.local/share/nexus-local-tests/p01-recovery-b9034e71209942e98d312d1f7b047ea1`；验收后已停止，四个端口均 down。`p02pg16/17`（25531/25532）、ci-fix（25616/25617）属其他任务，未触碰。
- 收尾（18:02）：P01 d3f7-r2 完成后，preview 18180、auth 转发 25511/25512 已停止；P02 API 18100 此前已停；自建 PG16/17 四个 postmaster 以 `pg_ctl -m fast` 停止。25510 由系统 `wslrelay.exe` 持有，非本任务进程，未终止。其他任务的 p02pg/ci-fix 未触碰。
- 浏览器 fresh SQLite：API 18000、Vite 18080（preview），N07 结束后由 runner 自行停止。
- 证据目录：`tmp_test/next/`，公开文档不含密码、DSN、令牌或真实财务原文。

## 剩余工作与下一步

1. NEXT02 单期主链已在 57bb 通过（8 passed/28.8s）；跨年 HTTP/PG 补跑 6+6 passed；连续两期、跨年浏览器、多银行仍是缺口。
2. NEXT03 登录限流（429）属外部身份服务；最终候选动态探测已 19/19，不能把有限探测称为完整渗透。
3. NEXT04 四类来源适配器已接，d3f7 SQLite 15、PG16/17 各 28、税审与交付包真实浏览器通过；批量失败/通知 unknown 两类来源仍无浏览器端到端证据。
4. NEXT09 未达 100 并发、多实例、长时间稳定性和 PG 资源采样门槛。
5. 已提交 `154537f7` 并推送 `origin/main`；未部署、未执行线上迁移、未调用真实模型或真实通知。远端 CI：QA success、P01 failure、Accounting foundation 运行中。
