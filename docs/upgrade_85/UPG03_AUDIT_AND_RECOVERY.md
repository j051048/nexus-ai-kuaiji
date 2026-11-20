# Batch 03：验收包补全、有数据审计链验真与分级恢复办单

- **先读**：`docs/upgrade_85/UPG00_MASTER.md`、`docs/rework/RWK05_ACCEPTANCE_PACK.md`、`docs/pilot/PLT06_ACCESS_RECOVERY.md`、`scripts/opt_acceptance.py`、`scripts/verify_audit_chain.py`、`scripts/backup_accounting.py`
- **前置**：Batch 02 完成
- **提分目标**：把“空库验链/局部验收”升级为全模块防作弊验收、真实业务数据篡改断点检测与停机恢复后真实办结一单（推动维度 2、6 稳定至 8.8+，补强维度 7、8）

---

## 1. 实施任务

### 1.1 完成 RWK05-A（全模块机器验收包）
- 改造 `scripts/opt_acceptance.py`：
  1. 启动首步调用 `scripts/check_test_env.py`，依赖与锁文件不一致时直接失败退出；
  2. 逐项纳入 `OPT01~OPT10` 及 `RWK01~RWK04` 每个子模块的机器验收命令与静态断言（`window.confirm` 零命中、`parseFloat(` 金额守卫、`ruff` 零错、前端体积预算、`test_pg_sql_hygiene.py`）；
  3. 按模块汇总输出命令数/通过/失败/PG 跳过/无自动检查，**无自动检查项严禁标为通过**；脚本只读输出到 `tmp_test/rework/RWK05-A/`；
- 做一次真实负向验证：临时在业务文件加一行 `window.confirm('x')`，确认脚本捕获失败后撤回并复跑变绿，保留完整输出证据。

### 1.2 完成 RWK05-B（有数据多事务所审计哈希链篡改实证）
- 新增 `nexus_backend/tests/test_audit_chain_real_data.py`（SQLite 必跑，配置 PG 时同跑 PG）：
  1. 严格通过真实服务接口（严禁直接写审计表）在 2 个事务所下完成建企、导入、制单、复核、过账、冲销、权限变更、原件下载、差异处置等操作，每所生成 ≥50 条真实业务审计行；
  2. 运行 `verify_audit_chain.py` 断言返回码 `0`；
  3. 复制数据库副本，分别实施三种恶意破坏（改单行内容、删中间一行、交换两行顺序），断言校验器均返回非零并精准报出断点主键；
  4. 扫描全仓 `INSERT INTO audit_events`，在状态台账列出全部审计动作名及步骤 1 的覆盖清单。

### 1.3 完成 PLT06-A/B/C/D（分级授权防反推与停机恢复后办单演练）
- **PLT06-A/B/C**：补齐分级授权测试，覆盖：① 汇总视图对无明细权限者仅展示脱敏/受限聚合且禁止反查单条明细；② 单记录聚合对无明细权者显示受限；③ 实时撤权后既有会话与深链立即失效返回 403/404；
- **PLT06-D**：新增 `nexus_backend/tests/test_plt06_recovery_business_continuation.py`，完整演练“加密备份 → 恢复至隔离新库 → 校验恢复后默认 `serving_enabled=false` 且 `outbound_disabled=true` → 写审计解除保持 → 在恢复库跑通权限矩阵并真实办结 1 笔新业务（复核/过账） → 断言停机前处于 `unknown` 状态的外部通知绝不被自动补发”；
- 针对历史已知的 `pg_restore` 跨主版本问题（PG17 工具还原 PG16 报 `transaction_timeout`），在 `scripts/backup_accounting.py` 中增加版本兼容性探测与清晰阻断提示/安全过滤选项。

---

## 2. 验收命令与通过标准

```text
.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_audit_chain_real_data.py nexus_backend/tests/test_plt06_recovery_business_continuation.py -q
.venv/Scripts/python.exe scripts/opt_acceptance.py --out tmp_test/rework/RWK05-A/run-1
.venv/Scripts/python.exe scripts/opt_acceptance.py --out tmp_test/rework/RWK05-A/run-2
```

- `run-1` 与 `run-2` 稳定字段完全一致；
- 同步更新 `docs/pilot/OPT_ACCEPTANCE_HANDOFF.md`、`docs/rework/RWK_STATUS.md` 与 `docs/rework/UPGRADE_85_STATUS.md`。
