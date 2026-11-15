# TAXI 批次 A 检查点

- 检查点时间：2026-11-10 21:25 +08:00
- 批次：A / TAX01–TAX02
- 当前状态：**本地可执行项完成；外部资料/适配未执行**
- 候选 HEAD：`781fd93499dcba7bb76056f0febe19e372202af2`
- source：`2062 / 2bb9f3866781d7fd0fdf34d20ad55bd94828bdf011b3623f7e7d830510edcaa0`
- migration：`351 / 298bb66c91b7393c5efb8965ec007c709315a3e48015c87c29d326c5f24ef5c0`
- 工作区：6 个已跟踪修改、18 个本任务未跟踪文件、10 个输入提示词未跟踪；未提交、未推送。

## 已完成

| 子项 | 状态 | 关键证据 |
| --- | --- | --- |
| TAX01 版本化边界/客户依赖/预览 | 本地、PG16、PG17、桌面、手机通过 | 7 SQLite；1+1 PG；2 浏览器 |
| TAX02 双向结转/辅助保留/源变更失效 | 本地、PG16、PG17、桌面、手机通过 | 9 SQLite；2+2 PG；2 浏览器 |
| 既有结转/底稿回归 | 通过 | `test_b04_pilot.py + test_tax_workpaper.py` 12 passed |
| 前端类型/构建/预算 | 通过 | `tmp_test/tax-budget.json` |
| 验收/证据/台账 | 已完成 | 本文件、`TAXI_BATCH_A_ACCEPTANCE_20261110.md`、两份细分验收、JSON |

## 未完成 / 外部依赖

- 真实客户对 10 项资料/规则的确认：全部缺失；默认 `unresolved`，不激活。
- 真实 T+ / OA / 出管：未接入；只保存计划、版本与模板摘要。
- 真实税务申报/缴款、通知、真人、真机、屏幕阅读器：未执行。
- 与本批无关的 `test_stage13_migration_workflow.py` 旧失败 8 项：未修，未计入本批通过。

## 可重跑命令

```text
.venv/Scripts/python.exe -m pytest -q \
  nexus_backend/tests/test_tax01_pilot.py \
  nexus_backend/tests/test_tax02_closing.py \
  --junitxml=tmp_test/tax-backend.xml

npx playwright test e2e/tax01-pilot.spec.ts --config=playwright.tax01.config.ts --reporter=line
npx playwright test e2e/tax02-closing.spec.ts --config=playwright.config.ts --reporter=line
```

PG 使用既有隔离 runner 与 `tmp_test/next02-env/runs/4fb7acee…/pg{16,17}-recovery_source.json`；不得改用生产 DSN。

## 下一步

- 若用户批准：按 `docs/TAXI_DEEPSEEK_BATCH_B_20261110.md` 执行 TAX03–TAX04 的本地通用工作。
- 批次 B 不得把合成编码/脱敏车牌/虚构合同写成真实客户主数据；真实接入仍需客户资料与授权。
- 先处理或明确隔离 `test_stage13_migration_workflow.py` 旧失败，避免与新批次证据混淆。

## 环境

- 本批启动的 Playwright webServer 已随测试退出；未保留 API/前端进程。
- 本批使用的四个 owned PG16/17 集群已用 `pg_ctl -m fast` 停止，25496–25499 全部 down。
- 未停止其他智能体服务；未删除临时审计/失败产物。
- 未提交、未推送、未部署、未执行线上 SQL、未调用真实模型/通知。
