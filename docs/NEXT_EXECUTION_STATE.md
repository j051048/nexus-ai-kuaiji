# NEXT 执行状态台账

- 更新时间：2026-11-03 09:50（Asia/Shanghai）
- 仓库：`C:\Users\Fei\Desktop\AI应用\nexus-ai-kuaiji`
- 分支／HEAD：`main` / `df3fface95d1f69c28e859e89015465955c517b4`
- 最终候选（ddb，`tmp_test/next/next11-final/candidate.json` 待复写）：`source_files=1816`，`source_sha256=dddef1a88eee0bfdc2c2db37d09560dc0290b434fe800993c05b52b7ba2a963e`；`migration_files=325`，`migration_sha256=48fc7b47abd864e39ec6701939dd42f0bb65c1bc16732bcff2c6686350a0c8e1`。该候选含并发任务的 `TaxFilingRecords.tsx` 修改，归属见 `NEXT_OWNERSHIP.md`。
- 工作区：134 项未提交／未跟踪（含本轮 NEXT 文件、并发任务文件与私有运行材料）；未提交、未推送、未部署。
- 基线候选（2026-11-02）：1790/`3fc6fc89…`、316/`f07ba1cd…`；历史 QA01–QA05 只对应旧 HEAD，不作为本轮通过依据。

## 状态表

| 工作包 | 工程状态 | 外部验收 | 关键证据／剩余 |
| --- | --- | --- | --- |
| NEXT01 统一候选／资源治理 | **本地验收通过（最终候选）** | 不适用 | final manifest passed、stable、cleanup passed；14+4+3 通过 |
| NEXT02 真实浏览器主链 | 进行中（单期通过，历史候选 1794） | 不适用 | 8 passed 覆盖两企业/独立角色/桌面手机/权限负向；连续两期、跨年、多银行、最终候选重跑未完成 |
| NEXT03 安全边界 | 进行中（本地探测通过，限流外部门槛） | 未授权：生产身份边缘限流 | 19 项动态探测（1796 候选）；最终候选安全回归 186 passed；登录限流无 429 证据 |
| NEXT04 人工接手时间线 | 进行中（税审来源通过，ddb 最终候选） | 不适用 | 31 passed／74.93s；批量失败/交付阻断/通知 unknown 适配器未接，无浏览器证据 |
| NEXT05 税务证据闭环 | 后端与离线通过（ddb 最终候选） | 不适用（不接真实税局） | 70 passed／197.06s + tsc；PG16/17 月度包各 4 passed 为历史候选；浏览器登记/上传/下载未验 |
| NEXT06 通知上线准备 | **本地验收通过（ddb 最终候选）** | 缺少真实供应商沙箱 | final3 manifest passed、stable、cleanup passed；66+42 通过；未发真实消息 |
| NEXT07 弱网／移动／纠错 | **本地浏览器验收通过（最终候选）** | 真机/微信/屏幕阅读器未验证 | 25 passed／1.3m；修复修订迟到响应覆盖人工修改；真实断网/上传中断未注入 |
| NEXT08 批量导出交付 | **本地验收通过（最终候选）** | 不适用 | SQLite 69、PG16/17 各 10、真实浏览器批量开期 1 passed；真实对象存储/多进程 worker 未验 |
| NEXT09 HTTP／多实例／容量 | 进行中（小规模 PG17 通过） | 生产容量/多节点未验证 | ddb 三阶梯 5/20/50 clients、0 error；未测 100 并发、万行导入、多 API、数小时稳定性；无 PG CPU 采样 |
| NEXT10 助手任务效果 | **本地离线验收通过** | 真实模型/客户未授权 | 353 passed、六场景、answer 3/3；`real_model_evaluated=false` |
| NEXT11 同候选联合验收 | 进行中（A 部分） | B/C/D 未完成 | 最终候选上 N01/04/05/06/07/08/09/10 已重跑；N02/N03 动态探测与完整恢复未在最终候选重跑；未推送、未接真实供应商 |

## 最终候选上的重跑

| 套件 | 结果 | 证据 |
| --- | --- | --- |
| NEXT01 | 14+4+3 passed；manifest passed/stable/cleanup passed | `tmp_test/next/next01-acceptance-20261103-final/` |
| NEXT03 安全回归（静态/HTTP 层） | 186 passed／51.48s | `tmp_test/next/next03-security-final.xml` |
| NEXT04 | 31 passed／74.93s；manifest passed/stable | `tmp_test/next/next04-acceptance-20261103-final3/` |
| NEXT05 | 70 passed／197.06s + tsc；manifest passed/stable | `tmp_test/next/next05-acceptance-20261103-final3/` |
| NEXT06 | notification 66／287.10s，delivery 42／189.17s；manifest passed/stable | `tmp_test/next/next06-acceptance-20261103-final3/` |
| NEXT07 | 25 passed／1.3m，3 引擎 + 手机 | `tmp_test/next/next07-browser/report.json` |
| NEXT08 | SQLite 69／112.96s；PG17 10／11.94s；PG16 10／12.38s；浏览器 1／32.2s | `next08-final3.log`、`ak05-pg-runs/03e38034…`、`fcd4974f…` |
| NEXT09 | `qa_mixed` 10 公司/1 轮 passed；5/20/50 clients 0 error；两 worker 各 5 项；守恒通过（ddb 候选） | `tmp_test/next/next09-capacity-ddb/report.json` |
| NEXT10 | 353 passed；answer 3/3；`real_model_evaluated=false`（ddb 候选） | `tmp_test/next/next10-assistant-final3/` |
| 前端 | `npm run typecheck` exit 0；`npm run build` exit 0 | 终端输出；构建产物 `dist/` |

## 本地环境（本轮自建）

- Python：仓库内 `.venv`（Python 3.14.1）；Node v26.5.0／npm 11.17.0；Playwright 1.58.2。
- WSL PG16/17 四集群：端口 25496–25499，root `$HOME/.local/share/nexus-local-tests/p01-recovery-b9034e71209942e98d312d1f7b047ea1`；主机休眠后需用 `/usr/lib/postgresql/{16,17}/bin/pg_ctl` 手动启动。`p02pg16/17`（25531/25532）、ci-fix（25616/25617）属其他任务，未触碰。
- 浏览器 fresh SQLite：API 18000、Vite 18080（preview）；P02 真实身份/PG17 环境当前未启动。
- 证据目录：`tmp_test/next/`，公开文档不含密码、DSN、令牌或真实财务原文。

## 剩余工作与下一步

1. NEXT11 完整恢复门禁（数据库＋原件＋身份＋权限）尚未在最终候选执行；P01 四集群与 GoTrue bridge 需重启后跑 `scripts/p01_recovery_gate.py --all`。
2. NEXT02 连续两期/跨年/多银行与 NEXT03 登录限流仍是缺口；不得用历史候选证据冒充最终通过。
3. NEXT09 未达 100 并发、多实例、长时间稳定性和 PG 资源采样门槛。
4. 未提交、未推送、未执行线上迁移、未调用真实模型或真实通知。
