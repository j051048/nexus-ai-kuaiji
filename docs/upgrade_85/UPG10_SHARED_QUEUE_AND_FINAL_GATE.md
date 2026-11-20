# Batch 10：五人共享总控队列、集团合并试算与全仓同候选终验

- **先读**：`docs/upgrade_85/UPG00_MASTER.md`、`docs/pilot/PLT07_QUEUE_SUMMARY.md`、`docs/pilot/PLT09_MEASUREMENT.md`、`docs/rework/RWK06_PLT_REMAINING.md`（RWK06-A/D/F）、`nexus_backend/app/observability/insights.py`、`nexus_backend/app/observability/value.py`
- **前置**：Batch 01 ~ Batch 09 全部完成
- **提分目标**：收口 5 人共享总控队列、补齐跨法人内部往来对账与合并抵消试算表、完成全仓同候选终验（推动维度 1 突破 8.6，全工程面达 8.5+ 准入水位）

---

## 1. 实施任务

### 1.1 完成 PLT07-A/B/C/D（五人共享中心总控队列与集团管理汇总）
- 升级共享工作台待办三轨视图：**待我办 / 等他人 / 需主管介入**，支持按最早逾期、未分配、高风险一键筛选与主管派单/合法备岗替岗（严守“派单不自动授予对象明细权限”红线）；
- **PLT07-B（诚实 ETA）**：预计完成时间在历史同类样本不足时明确返回空值并展示“样本不足”，绝不凭空估算；
- **PLT07-D（严谨集团汇总）**：跨 5 家法人汇总严守“缺报/未复核不当 0 相加、不同会计准则口径不强行相加、`legacy` 历史报表与 `formal` 正式报表分列标注”。

### 1.2 多法人内部往来对账与管理合并试算表
- 新增 `nexus_backend/app/reports/consolidation.py` 及双库前向迁移（SQLite + PG + `FORCE RLS`）：
  - 基于集团内 5 家法人的关联方标识，自动匹配各法人间的内部应收/应付、内部收入/成本、内部资金往来，输出逐笔对平状态与未达差额明细；
  - 支持登记手工合并抵消分录（遵循独立复核、只追加审计，绝不修改任何单体法人正式账簿）并生成集团合并工作底稿，所有接口与导出醒目标注**“集团管理合并试算（非经审计法定合并报表）”**。

### 1.3 完成 PLT09 全核对与全仓合规文案清理（RWK06-F）
- 在 `nexus_backend/tests/test_value_baseline.py` 补齐“上期事项本期映射/退回不混入本期分子”的显式跨期 cohort 回归用例；
- 为 `docs/pilot/SCORECARD.md` 的五条“不能判定为成功”反作弊判定规则补全自动化测试；
- 扫描 `docs/` 与 `src/`，清理任何未经实测的夸大表述（如“全面合规”“已通过等保/认证”“保证减员至 5 人”），全部改为只陈述具体已验证的技术控制与实测边界。

### 1.4 全仓同候选终验与最终就绪报告
- 执行全套自动化终验：
  1. 依赖锁校验 `scripts/check_test_env.py`；
  2. 全量非 PG 后端测试 + `test_pg_sql_hygiene.py` + 有数据审计链验证；
  3. 升级版 `scripts/opt_acceptance.py` 全模块验收；
  4. 前端六项门禁（`typecheck / lint / format:check / test / build / frontend-budget`）；
  5. 安全扫描（`scripts/check_secrets.py` + `bandit`）；
- 生成最终汇总交付报告 `docs/rework/FINAL_85_READINESS_REPORT.md`，按 10 个评审维度逐项列出：
  - ① 本次同候选指纹下全部通过的自动化测试与命令证据；
  - ② 已达 8.5+ 工程就绪的维度清单；
  - ③ 需客户/银行/税局签认与现场联调解封的外部总清单（汇聚 `DECISIONS_NEEDED.md`、`CUSTOMER_INPUTS.md`、`BANK_H2H_HANDOFF.md`、`ETAX_LIVE_HANDOFF.md`）。

---

## 2. 验收命令与通过标准

```text
.venv/Scripts/python.exe scripts/check_test_env.py
.venv/Scripts/python.exe -m pytest nexus_backend/tests -q -p no:cacheprovider --ignore-glob="*_postgres.py"
.venv/Scripts/python.exe scripts/opt_acceptance.py --out tmp_test/rework/FINAL_85/acceptance
.venv/Scripts/python.exe scripts/check_secrets.py
npm run typecheck && npm run lint && npm run format:check && npm test -- --run && npm run build && npm run test:frontend-budget
```

- 除 `DECISIONS_NEEDED.md` 书面登记的待业务裁决项外，全仓自动化门禁 0 失败、0 违规；
- `docs/rework/FINAL_85_READINESS_REPORT.md` 与 `docs/rework/UPGRADE_85_STATUS.md` 证据指纹完全一致。
