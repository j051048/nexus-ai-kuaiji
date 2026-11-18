# OPT 同候选验收交接（PLT01-D）

## 运行方式

```powershell
python scripts/opt_acceptance.py
# 指定输出目录
python scripts/opt_acceptance.py --out tmp_test/pilot/PLT01-D/<run>
# 有本地 SQLite 测试库时追加审计链校验
python scripts/opt_acceptance.py --sqlite <path-to-accounting.db>
```

脚本只读源文件与测试库，只在 `tmp_test/pilot/PLT01-D/<run>/` 下写 `summary.json`、`summary.md`、`sources.json`。任何已跟踪迁移被修改、命令失败、超时或零用例都会使退出码为 1；PG 命令缺少 `ACCOUNTING_TEST_POSTGRES_URL` 时明确记为“skipped”，不计通过。

## 本次运行

- HEAD：`7cce49a57543f8ec3a02c68114eb1500b308eb12`
- 源文件指纹：2326 个文件（`nexus_backend`/`src`/`scripts`/`e2e` 与根配置），`fb0d270984e070caa6c92010fb58fd7f2576acec4c8d3d4725f329648fe2a43c`
- 迁移：483 个文件，`ac77fb05ab2718754c5a7872f6c2f17be2e514a90fdca967f72375be025fc6b3`
- 结果：**passed**；命令 17 个（10 个 SQLite/前端通过 + 7 个 PG 跳过），失败 0，零用例 0；审计链在空的可迁移 SQLite 测试库上 `OK audit chain`（exit 0）。
- 运行目录：`tmp_test/pilot/PLT01-D/run-12/`、`run-13/`；两次稳定字段一致（`repeatability.json`）。审计库路径 `tmp_test/pilot/PLT01-D/audit/accounting.db`，只证明验证器可对合法空链返回 0，不等同于有业务数据的恢复演练。

| 模块 | 命令 | 通过 | 失败 | 跳过命令 | 跳过测试 |
| --- | --- | --- | --- | --- | --- |
| OPT01 | 1 | 1 | 0 | 0 | 0 |
| OPT02 | 2 | 1 | 0 | 1 | 0 |
| OPT03 | 2 | 1 | 0 | 1 | 3 |
| OPT04 | 2 | 1 | 0 | 1 | 0 |
| OPT05 | 2 | 1 | 0 | 1 | 0 |
| OPT06 | 2 | 1 | 0 | 1 | 0 |
| OPT07 | 2 | 1 | 0 | 1 | 0 |
| OPT08 | 1 | 1 | 0 | 0 | 0 |
| OPT09 | 1 | 1 | 0 | 0 | 0 |
| OPT10 | 2 | 1 | 0 | 1 | 0 |

### 跳过与边界

- 7 条 `PG:` 命令因未设置 `ACCOUNTING_TEST_POSTGRES_URL` 记为跳过，覆盖 OPT02/03/04/05/06/07/10；它们没有被计为通过。
- 跳过标记 137，基线 `767f7cdf` 为 129，差 +8；新增位置均为 `pytest.mark.skipif` 的隔离 PG 条件，未新增 `xfail` 或产品路径静默跳过：
  - `nexus_backend/tests/test_opt03_audit_chain_postgres.py` +3
  - `nexus_backend/tests/test_opt03_audit_immutability.py` +1
  - `nexus_backend/tests/test_opt04_posting_postgres.py` +1
  - `nexus_backend/tests/test_opt05_batch_lease_postgres.py` +1
  - `nexus_backend/tests/test_opt05_scheduler_postgres.py` +1
  - `nexus_backend/tests/test_opt06_voucher_gaps_postgres.py` +1
- 已跟踪迁移修改：无；未跟踪迁移：0。历史迁移/checksum 未改动。
- 审计链：`--sqlite tmp_test/pilot/PLT01-D/audit/accounting.db` 返回 `OK audit chain`、exit 0；该库由当前迁移新建且没有业务审计行，不能据此宣称已有数据的链恢复/连续性通过。无 `--sqlite` 时脚本明确记 `skipped`，不冒充通过。
- `*_cents` 64 位扫描：`nexus_backend/tests/test_migration_money_types.py` 已包含在 OPT01 命令并通过。

## 需要人处理/外部门槛

- **FE01**：共享预算 `homeCss 145755 > 145000`，超 755 B；需授权改 `src/` 后才能修复，属于 FE01 外部门槛，不影响 OPT 脚本。
- **全后端 tax 定向**：此前 `-k tax` 仍 10 failed（4 个本机 Starlette 0.35 `TestClient(client=...)` 不兼容、6 个真实夹具/产品问题）；本脚本只跑定向 OPT 子集，不能据此宣称全后端全绿。
- **PostgreSQL**：本次只跑 SQLite/前端子集；设置 `ACCOUNTING_TEST_POSTGRES_URL` 后重跑同一脚本可执行 7 条 PG 命令。PG16 产品 `restore()` 仍受本机 PG17 客户端 `transaction_timeout` 阻断；多进程限流未验证。
- **远端/人工**：推送后 CI、分支保护、Firefox/WebKit、真机、屏幕阅读器、真实通知/模型/客户试点均未由本脚本验证。

## 当前仓库状态

- 未提交、未推送、未部署；工作区含本任务与既存未提交改动。
- 本脚本不修改产品代码、数据库或迁移，只生成上述证据。
