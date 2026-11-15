# 批次 C 检查点（2026-11-11）

## 当前状态

| 工作包 | 本地实现 | SQLite | PG16 | PG17 | 浏览器 | 外部 |
| --- | --- | --- | --- | --- | --- | --- |
| C1 / TAX05 受控大凭证 | 完成 | 4 passed | 通过（同一 PG 套件） | 通过 | 1280/390 通过（516 行） | T+ 文件未验证 |
| C2 / TAX06 规费批次 | 完成 | 12 passed | 3 passed | 3 passed | 1280/390 通过 | 真实资料/规则未验证 |
| P01 恢复 | 完成 | SQLite 行级恢复 1 passed | 九项 checks 全 true | 九项 checks 全 true | — | 非生产 |

- HEAD：`781fd93499dcba7bb76056f0febe19e372202af2`
- 候选：source 2101 / `1ecf01e8f1432c068ebb179b52c0beeeab5bed551066122ba65cccac780ae64e`；migration 365 / `e5d9e52b4f838476c99bfdc5ab7e56bbf7259b7df4a452a2e07c60ea044cc39f`
- 工作区：94 项未提交（含本记录 3 个文档）；未提交、未推送、未部署。
- 本批结束前没有再进行代码修改；文档不参与候选。

## 当前进程 / 环境

- P01 新环境：`tmp_test/tax-c/p01-env2`，environment `8cf97847e94c4702ae41632cdbfbfc11`。
- P01 最终 run：`tmp_test/tax-c/p01-env2/runs/298392bfcd744d57b53ea688de175a08`；bridge 端口 25510。
- PG16 source/target：25496/25497；PG17 source/target：25498/25499。仍是本任务隔离集群。
- Dev API/preview 仅在浏览器测试期间由 Playwright 启动，测试进程结束后自动停止；最终收尾需确认无本任务 18000/4173 端口进程。
- 未触碰其他任务资源；旧 b9034 数据目录保留未删。

## 可重跑命令

```bash
cd nexus_backend
python -m pytest tests/test_large_voucher.py tests/test_tax06_fees.py \
  tests/test_tax06_recovery.py tests/test_tax04_sources.py -q --disable-warnings
```

```bash
npx playwright test --config playwright.tax05.config.ts
npx playwright test --config playwright.tax06.config.ts
```

```bash
# PG（配置需指向本任务隔离端口）
python scripts/test_accounting_postgres_isolated.py --config tmp_test/tax-c/pg17.json \
  nexus_backend/tests/test_tax06_fees_postgres.py -q
python scripts/test_accounting_postgres_isolated.py --config tmp_test/tax-c/pg16.json \
  nexus_backend/tests/test_tax06_fees_postgres.py -q
```

```bash
# P01（新 pair 后先停止旧 bridge，再在 WSL 启动新 bridge）
python scripts/p01_local_recovery_env.py --directory tmp_test/tax-c/p01-env2 new-pair
wsl.exe -d Ubuntu-22.04 --cd "<repo>" -- bash -lc \
  "python3 scripts/p01_local_recovery_env.py --directory <old-run> bridge-stop"
wsl.exe -d Ubuntu-22.04 --cd "<repo>" -- bash -lc \
  "python3 scripts/p01_local_recovery_env.py --directory <new-run> bridge-start"
python scripts/p01_recovery_gate.py --all --config-dir <new-run> --out tmp_test/tax-c/p01-final
```

## 证据索引

- `tmp_test/tax-c/p01-final/reports/summary.json`
- `tmp_test/tax-c/tax-c-combined.xml`
- `tmp_test/tax05-large-voucher-1280.png`、`tmp_test/tax05-large-voucher-390.png`
- `tmp_test/tax05-pager-1280.png`、`tmp_test/tax05-pager-390.png`
- `tmp_test/tax06-fees-1280.png`、`tmp_test/tax06-fees-390.png`
- `tmp_test/tax-c/candidate-final.json`（手工 `candidate_fingerprint()` 输出；如文件不存在，以验收记录中的 source/migration 指纹为准）

## 下一批 / 外部待办

1. 批次 D：T+ 文件模板、导入/审核/过账分隔、客户签收；不得用本批合成规费冒充实际 T+。
2. 真实客户资料和真实驾驶员/车辆/合同对照；本批只用合成数据。
3. 大凭证取消/恢复语义若要产品化，需要新增批次状态机与队列契约；当前仅同步原子生成/复核。
4. 真机、真实通知、远端 CI、生产发布授权。

## 归属提醒

批次 A/B/C 的改动都在同一工作区，共 94 项未提交；本批未删除或回退任何既有改动。新 pair/集群只在 `tmp_test/tax-c` 与自建 WSL root `p01-recovery-8cf...` 下。停止服务时不得按目录名前缀盲删或停止其他任务进程。
