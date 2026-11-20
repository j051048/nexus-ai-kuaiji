# Batch 01：工程止血、回归清零与 PG 强制门禁

- **先读**：`docs/upgrade_85/UPG00_MASTER.md`、`docs/rework/RWK00_MASTER.md`、`docs/rework/RWK01_ENV_GATES.md`、`docs/rework/RWK02_BROKEN_TESTS.md`、`docs/rework/RWK_STATUS.md`、`.github/workflows/accounting.yml`
- **前置**：`RWK01-A` 已完成（继续使用 `.venv/Scripts/python.exe`）
- **提分目标**：清零测试回归欠债、修复前端体积超标、根治 SQLite 通过而 PostgreSQL 线上 500 的双库割裂问题（拉升维度 8、6、9）

---

## 1. 实施任务

### 1.1 完成 RWK01-B（全量非 PG 后端基线）
- 使用 `.venv/Scripts/python.exe` 完整跑通非 PG 后端测试（禁用 `-x` 与 `--maxfail`），输出至 `tmp_test/rework/RWK01-B/`；
- 生成 `docs/rework/BACKEND_BASELINE.md`，记录总数、通过、失败、报错、跳过、耗时，并逐行列出每条 `FAILED` / `ERROR` 及其归类。

### 1.2 完成 RWK01-C（前后端静态检查清零）
- 核查并修复 `nexus_backend/app/accounting/review.py` 未使用导入 `REVIEW_RISK_RULE_VERSION`（若应写入返回字段则补上，否则删除），使 `.venv/Scripts/python.exe -m ruff check nexus_backend/app` 达到 `0 error`；
- 按 `RWK01-C` 区分仅换行符差异与真实格式错误文件并修复；
- 清理 `src/` 中 6 条多余 `eslint-disable`，使 `npm run lint` 达到 `0 error, 0 warning`。

### 1.3 完成 RWK01-D（前端体积预算恢复绿灯）
- **严禁修改 `scripts/frontend_budget.mjs` 任何上限**；
- 识别被静态打入首屏入口的新增面板与首页多余样式，改为 `lazy()` 按路由懒加载并拆回组件专属 CSS；
- 确保 `npm run build && npm run test:frontend-budget` 状态为 `passed` 且 `violations` 为空，在台账贴出优化前后六项体积数字。

### 1.4 完成 RWK02-A ~ E（全量回归清零）
- **RWK02-A（`test_admin_derived_permissions.py` 5 条）**：属于“客户财务能否担任复核人”的业务规则裁决，不改规则也不改测试，完整写入 `docs/rework/DECISIONS_NEEDED.md` 并标记`阻塞：待业务决定`；
- **RWK02-B（制单与复核分离）**：更新相关测试夹具由独立复核人完成复核，不削弱原断言；
- **RWK02-C（冲销分录逐行相反）**：逐条确认是测试手工构造问题还是产品更正路径缺陷，分别修夹具或修产品；
- **RWK02-D（税务 6 项遗留失败）**：重点修复输入校验产品缺陷（畸形行绕过行数上限、`bool` 绕过整数版本号须用 `type(x) is int`、`counterparty` 主体标识）并查清其余 3 项，保证 `-k "tax"` 零失败；
- **RWK02-E（其余基线失败）**：逐条查清并修复（含 Windows 并发原子文件替换、异步调度时序回归、OCR 供应商审计为 `None` 等），不得靠拉长 sleep 掩盖竞态。

### 1.5 新增防 SQLite / PostgreSQL 割裂守卫与 CI 门禁补强
- 新增 `nexus_backend/tests/test_pg_sql_hygiene.py`，静态扫描 `nexus_backend/app/` 与 `nexus_backend/migrations/postgres/`：
  1. 禁止在 SQL 布尔逻辑中出现 `AND ?)` / `OR ?)` 直接绑定整型布尔（必须改用 `?=1` 等显式比较）；
  2. 扫描所有 PG 迁移中的 `CREATE TRIGGER <name> ... EXECUTE FUNCTION <fn>`，若目标函数内含 `TG_NAME` 校验，断言 `<name>` 必须被白名单接受；
  3. 扫描最新 PG 迁移新增的所有多租户业务表，断言均包含 `ENABLE ROW LEVEL SECURITY` 与 `FORCE ROW LEVEL SECURITY`。
- 核查 `.github/workflows/accounting.yml`，确保 `test_pg_sql_hygiene.py` 与 `workspace / plt04 / plt05` 的 PG 迁移测试被纳入正式 CI 检查路径。

---

## 2. 验收命令与通过标准

```text
.venv/Scripts/python.exe scripts/check_test_env.py
.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_pg_sql_hygiene.py -q
.venv/Scripts/python.exe -m pytest nexus_backend/tests -q -k "tax"
.venv/Scripts/python.exe -m pytest nexus_backend/tests -q -p no:cacheprovider --ignore-glob="*_postgres.py"
.venv/Scripts/python.exe -m ruff check nexus_backend/app
npm run typecheck && npm run lint && npm test -- --run && npm run build && npm run test:frontend-budget
```

- 除 `DECISIONS_NEEDED.md` 书面登记的 `RWK02-A` 项外，后端非 PG 全量测试 0 failed、0 error；
- 前端六项门禁全绿，体积预算 `violations=[]`；
- 同步更新 `docs/rework/RWK_STATUS.md` 与 `docs/rework/UPGRADE_85_STATUS.md`。
