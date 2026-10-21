# AK00 基线与归属

## HEAD

```text
baf3f414187a198dbde1c41f8a90e42dee2629ca
branch: main
```

## 工作区

未提交已跟踪修改（前序轮次 + 本轮）：

- PG 投影：`database.py`、`main.py`、`maintenance_worker.py`、`workspace/*`、`138` 迁移；
- AG04/AG05：`tools/ai_inference_lab/*` 及测试；
- UX：`src/App.tsx`、`WorkspaceHome.tsx`、`WorkspaceRouteContent.tsx` 等；
- R5 报告和验收测试。

未覆盖、未回退、未 stash 其他任务修改。

## AG01 七文件现状

```text
intelligence/provider.py   71F2ACA6...E291663
intelligence/models.py     6D2991D8...91FD0680
intelligence/endpoints.py  33A3CE0C...9F895640
intelligence/service.py    200C9397...46B5E0
operations/meter.py        119B17CA...396CB7
assistant/provider.py      BBB5E987...D6099CE
assistant/worker.py        8A1891DF...EE406F4
```

与 AG01-R1 报告记录一致，说明既有修复仍在。

## 归属

- AG01 历史接收状态：pending；
- 本轮按用户指令接管 AK01 相关的最小 journal limits 修复；
- 未修改 AG01 生产协议结构、worker 队列和会计核心；
- C01、迁移、PG 投影、会计核心：未由 AK01 修改。

## 保护文件

- C01 / 会计核心 / 月结 / 子账 / 正式凭证；
- AG01 provider/meter 协议；
- `138_projection_identity.sql`；
- 前序 UX 文件。

## 门槛

- L：AK01 本地回归执行；
- D：本轮未跑真实 PG 的 AK01 并发；
- P：真实供应商/生产未验证。
