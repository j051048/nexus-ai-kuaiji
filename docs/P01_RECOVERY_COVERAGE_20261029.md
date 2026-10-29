# P01 恢复验收：证据清单缺口（2026-10-29）

## 结论

发现并修复了一个**真实缺口**：发布恢复的证据清单只覆盖在途任务与回执，
没有覆盖税与交付的业务证据（税务档案/底稿、月度包/授权/签收/查看）。
清单缺表时，恢复可以“看起来成功”而无法证明“同一个版本被申报、冻结、签收”。

PG16/17 的联合恢复（新集群 + 原件存储 + 身份替身）**本轮未执行**：
本机缺少 `.codex/c01-final-pg/*.json` 与 25496–25499 集群，属环境缺口，
已列入下方“未验证”，未以任何形式宣称通过。产品代码零改动。

## 已有能力核对（先确认再动手）

| 能力 | 位置 | 状态 |
| --- | --- | --- |
| 数据库恢复保护 | `restore_state.hold` + `release_local_hold` | 已有，保持 |
| 仅本机隔离库可释放 | `release_local_hold` 校验 `127.0.0.1`、端口≠5432、库名前缀 `ak01_local_ak05_` | 已有，保持 |
| 释放需六项人工核对 | `REQUIRED_CHECKS = {database, originals, identity, permissions, workflow, outbound_reconciled}` | 已有，保持 |
| 清单内容绑定 | `pending_inventory` 对 `payload_json/lease_token/...` 取 sha256 后入摘要 | 已有，扩展 |
| 恢复后旧代次不得结算 | `restore_epoch` + 租约围栏（N03） | 已有，保持 |
| 未知外发不自动重发 | `state in ('sending','unknown')` 隔离分支 | 已有，保持 |

## 缺口与修复

修复前 `scripts/release_recovery_hold.py` 的 `INVENTORY_TABLES` 只有 10 张运行表
（ocr/proposal/assistant/intake/model_costs/bridge_outbox/receipts/notifications），
税与交付证据一张没有。

先写失败测试 `nexus_backend/tests/test_recovery_inventory_coverage.py`：
修复前 7 条全红（6 张表缺失 + `REQUIRED_COLUMNS` 不存在）。

最小修复（只动清单，不动业务代码）：

1. `INVENTORY_TABLES` 增加 `tax_profiles`、`tax_workpapers`、
   `monthly_delivery_packages`、`monthly_delivery_grants`、
   `monthly_delivery_acknowledgements`、`monthly_delivery_opens`。
2. 新增 `BUSINESS_EVIDENCE_FIELDS` 与 `REQUIRED_COLUMNS`：逐表声明必须参与恢复
   核对的列（如底稿的 `version/status/profile_id/tax_cents/source_digest`、
   包的 `manifest_sha256/contact_user_id`、授权的 `recipient_user_id/version/state`）。
3. `pending_inventory` 对声明表**失败即停**：缺列直接抛
   `恢复清单缺少 <table> 的绑定、版本或证据字段（...），必须先完成核验`，
   不再“能查多少算多少”（旧 schema 无法再伪造完整证据）。
4. `snapshot_json`、`lines_json` 与既有 `payload_json` 一样只入 sha256，
   摘要可比对、正文不外泄。
5. `nexus_backend/tests/test_n03_recovery.py` 的假连接改为按声明列投影，
   并保留“缺列即报错”的反向用例（该用例断言消息含“通知”，故通知表保留原文案）。

## 门禁与证据

| 命令 | 结果 |
| --- | --- |
| `pytest nexus_backend/tests/test_recovery_inventory_coverage.py` | 修复前 7 failed；修复后 **7 passed** |
| `pytest test_n03_recovery.py test_c01_recovery_security.py test_recovery_inventory_coverage.py` | **58 passed**（31.87s） |
| `pytest -k "recovery or restore or notification or release_hold"` | 174 passed, 41 skipped, **2 failed（既有，与本轮无关）** |
| `ruff check` / `ruff format --check scripts` | 全绿 |

两处既有失败（文件相对 HEAD 无改动，断言落在 stage8/intelligence 而非恢复清单）：

- `test_intelligence_ledger.py::test_ledger_unlink_keeps_original_and_restores_independent_bank_workflow[sqlite]`
- `test_stage8_workflow_integration.py::test_open_case_blocks_review_and_export_after_ocr_then_close_restores_review_gate`
  （`latest["issues"]` 在案件关闭后仍保留“票据核对未完成”类条目）

另：`test_c01_full_recovery_postgres.py` 的 skip 原因是显式门闩
`C01_FULL_RECOVERY_ACCEPT != "1"`，并非缺依赖导致的静默跳过。

## 未验证（阻断与补齐方式）

1. **PG16.15 / PG17.11 联合恢复**：需要 `.codex/c01-final-pg/pg{16,17}-recovery_source.json`
   与 `-fresh-target.json`（四角色同库、端口 25496–25499），本机当前不存在。
   补齐：建两对一次性集群（`ak01_local_ak05_*` 库名）→ 生成配置 →
   `C01_FULL_RECOVERY_ACCEPT=1 pytest nexus_backend/tests/test_c01_full_recovery_postgres.py`。
2. **原件存储与身份替身联合流程**：同一用例内，需上述配置齐全后才能跑。
3. 恢复后“企业 A 不得读写企业 B / 撤权身份仍被拒”的 SQL 级断言：依赖第 1 项。
4. 旧 worker 租约结算恢复后任务：N03 离线用例已覆盖 SQLite 路径，PG 路径待第 1 项。

## 变更文件

- `scripts/release_recovery_hold.py`（清单与失败即停校验）
- `nexus_backend/tests/test_recovery_inventory_coverage.py`（新增，7 例）
- `nexus_backend/tests/test_n03_recovery.py`（仅测试假连接投影与缺列模拟）

未提交、未推送；未连真实模型、未发送真实通知、未改线上配置。
