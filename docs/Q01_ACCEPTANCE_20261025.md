# Q01 验收报告：把独立复核、正式账簿、月度交付与前端改动收敛为同一可重复候选

- 日期：2026-10-25
- 候选基线：`3fc1ab6e11761ba36490f1858b85fcff356441cc`（`feat: close C01 accounting and recovery acceptance`）
- 工作目录：`C:\Users\Fei\Desktop\AI应用\nexus-ai-kuaiji`
- 范围：`.github/workflows/accounting.yml` 依赖的门禁、`scripts/check_migration_idempotency.py`、相关检查器测试、必要的格式修正与核心浏览器门槛
- 本批**不增加业务功能**，不改动 C01 已验收的领域契约、权限边界与冻结用例集合

## 一、结论

| 项 | 状态 | 证据 |
| --- | --- | --- |
| Ruff 完整格式门禁 | 通过 | `ruff format --check nexus_backend/app services/api_gateway scripts` → `301 files already formatted` |
| 迁移静态检查（历史债登记） | 通过 | `scripts/check_migration_idempotency.py` → exit 0；7 个历史迁移、64 条违规精确登记 |
| 历史迁移字节 | 未变 | `git status --porcelain nexus_backend/migrations` 为空；登记条目绑定 `sha256` |
| 核心浏览器门槛 dev | 通过 | `node scripts/run_e2e.mjs --mode both` → `dev: PASS; 0 gate issues`（106/106） |
| 核心浏览器门槛 preview | 通过 | 同上 → `preview: PASS; 0 gate issues`（106/106）；构建指纹绑定 |
| 前端单测 | 通过 | `npm test` → `89 passed (89)` / `902 passed (902)` |
| 类型检查 / 构建 | 通过 | `npm run typecheck`、`npm run build` 均 exit 0 |
| 后端组合回归 | 见 §6 | 定向用例通过；全量套件未在本机跑完（见 §7） |
| PostgreSQL 16/17 门槛 | **未验证（阻断）** | 本机无 Docker，WSL 仅 PostgreSQL 14.24（见 §5） |
| 远端 CI | **未执行** | 本批不提交、不推送 |
| 真实模型 / 生产 | **未触碰** | 全部使用本地 SQLite、虚构企业与身份 |

## 二、前置核对

- `git rev-parse HEAD` = `3fc1ab6e`，与 GPT 上一轮交付一致，无版本漂移；本批不需要「先说明差异」。
- 开工时工作区干净（`git status --porcelain` 为空），不存在其他智能体的未提交修改，故本批未发生归属冲突，也未覆盖、重置或搬移他人改动。
- 已阅读：`AGENTS.md`、`docs/C01_FINAL_ACCEPTANCE_20261025.json`、`docs/C01_CLOSURE_20261025.md`、`README.md`、`docs/BACKUP_RECOVERY.md`、`.github/workflows/accounting.yml`、`playwright.config.ts`、`scripts/e2e_gate.mjs`、`scripts/e2e_core_cases.json`、`nexus_backend/migrations/postgres/153–161`。
- 旧审计结论只作线索，所有判断都以本候选的实际代码与实跑结果为准；`docs/AUDIT_*` 中「尚未实现」的表述已按 HEAD 重新核对，本批不据此排期。

## 三、修改清单

合计：32 个已跟踪文件被修改（其中 30 个为纯格式）、4 个新增文件；`nexus_backend/migrations` 字节零改动。

### 1. 迁移静态检查（业务等价修复）

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `scripts/check_migration_idempotency.py` | 重写 | 规则精确化 + 历史债台账 + 起点护栏 |
| `scripts/migration_idempotency_legacy.json` | 新增 | 7 个历史迁移的路径、`sha256`、规则码、违规集合、原因 |
| `nexus_backend/tests/test_migration_idempotency_ledger.py` | 新增 | 14 个检查器用例（先红后绿） |

检查器行为：

- 规则码固定为 `function_not_create_or_replace`、`functiondef_rewrite`、`trigger_without_drop_if_exists`。
- 触发器按 `DROP` 与 `CREATE` 的**名称 + 表名**配对：一个 `DROP TRIGGER IF EXISTS` 只覆盖同名同表的 `CREATE`，其余仍报错。
- 历史债只按 `sha256` 全等认定：文件一旦改动、出现新违规、登记缺失、重复登记、无效登记、为干净文件登记，全部失败。
- 默认扫描起点保持 `BASELINE_VERSION = 135`；`--after` 高于起点直接退出 2，不允许用参数跳过历史。
- 运行时迁移 `checksum` 校验（`nexus_backend/app/accounting/database.py` 的 `migration_digest`）未改动。

### 2. 纯格式修正（与业务修改分开记录）

`ruff format`（0.17.0，与 `nexus_backend/requirements.lock` 一致）修正 30 个文件，全部为版式调整，无逻辑改动：

- `nexus_backend/app/` 27 个：`accounting/db_diagnostics.py`、`assistant/{context_pack,fact_validation,models,provider,service,worker}.py`、`b04_close/service.py`、`execution/{handoff,records,router}.py`、`intake/service.py`、`intelligence/{checks,context_pack,fact_validation,journal_facts,models,provider,runtime_context,service}.py`、`ledgers/service.py`、`observability/service.py`、`operations/meter.py`、`reports/service.py`、`tax/estimate.py`、`workspace/{projection,service}.py`
- `scripts/` 3 个：`check_inner_strength.py`、`check_pg_test_target.py`、`test_accounting_offline.py`

该债务在 HEAD 就存在，与本批无关：

```text
git show HEAD:nexus_backend/app/tax/estimate.py | ruff format --check --stdin-filename ... -
→ exit 1（未改动的历史文件同样不满足当前格式）
```

即：CI 的格式步骤在 `3fc1ab6e` 上是红的，本批只是把它变绿。

### 3. 浏览器门槛（复现后的最小修复）

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `e2e/warmup.ts` | 新增 | 仅 dev 模式：开跑前加载一次入口 bundle，吃掉 Vite 冷编译 |
| `playwright.config.ts` | 修改 | `runtime.mode === 'dev'` 时挂 `globalSetup` |

**没有**提高 30s 用例超时、没有 `retries`、没有 `test.skip`、没有改断言。

## 四、失败测试先行

新增检查器用例在实现前**全部为红**（`pytest nexus_backend/tests/test_migration_idempotency_ledger.py -q` → 11 failed），覆盖：

1. 精确登记可通过；
2. 文件被改 → `changed`；
3. 新迁移带违规 → `unregistered`；
4. 登记比实际多 → `stale`；
5. 登记比实际少 → `unregistered`；
6. 重复登记 → `duplicate`；
7. 干净文件被登记 → 失败；
8. 无效字段（`sha256`/`reason`/`rule`）→ `invalid`；
9. 台账缺失 → 失败；
10. `--after` 高于默认起点 → 退出 2；
11. 一个 `DROP TRIGGER` 不得覆盖另一个同名不同表的 `CREATE TRIGGER`。

修复后：`14 passed in 0.29s`（含既有的 `test_ag00_migration_idempotency.py`）。

## 五、门禁实跑结果

| 命令 | 退出码 | 结果 |
| --- | --- | --- |
| `npm run format:check` | 0 | `All matched files use Prettier code style!` |
| `npm run lint` | 0 | eslint 无错误 |
| `npm run typecheck` | 0 | `tsc -p tsconfig.app.json --noEmit` 无输出 |
| `npm test` | 0 | 89 文件 / 902 用例通过 |
| `npm run build` | 0 | `tsc && vite build` 成功（见 §7 的环境注意） |
| `ruff check nexus_backend/app services/api_gateway scripts` | 0 | `All checks passed!` |
| `ruff format --check nexus_backend/app services/api_gateway scripts` | 0 | `301 files already formatted`（修复前 31 个不合格） |
| `python scripts/check_migration_idempotency.py` | 0 | 无输出（无违规） |
| `pytest .../test_migration_idempotency_ledger.py .../test_ag00_migration_idempotency.py -q` | 0 | `14 passed` |
| `node scripts/run_e2e.mjs --mode both` | 0 | `dev: PASS` / `preview: PASS`，各 106/106，`issues: []` |

核心浏览器门槛的门禁文件：

```text
test-reports/e2e-core/01518cf7-bf03-48a5-b690-4fa8f8b70bab/reports/{dev,preview,summary}.json
source = d3d93298dc0b41dfecd5fe1e7ff95104d8c435512e562d14ccb1d5d54fb3e641
expectedPerMode = 106；passed = true；issues = []
```

### 冷编译定位（复现证据）

dev 模式首轮失败 5 例，逐条复现后归因如下：

| 现象 | 位置 | 归因 |
| --- | --- | --- |
| 3 例 `page.goto: Test timeout of 30000ms exceeded` | `e2e/business.spec.ts` 前 3 条 | **Vite 冷编译**：单独复跑同样 30s 超时，与套件顺序无关 |
| 1 例身份切换轮询超时 | `e2e/ui-navigation.ts:45` `expect.poll(selectedProfile)` | 冷编译挤占 30s 预算（预热后通过） |
| 1 例 mobile 子页面导航高亮 | `e2e/v09-navigation-history.spec.ts` | 单独复跑通过，属冷编译下的偶发（预热后全绿） |

实测：预热加载入口 bundle 耗时 **84,545ms**，远超单用例 30s。预热后同一组用例 `2 passed`，全量 dev `106/106` 通过。业务操作耗时未被掩盖——预热脚本只做一次页面加载，不进入任何业务断言。

## 六、定向回归（账务组合）

| 命令 | 结果 |
| --- | --- |
| `pytest test_posting_service.py test_posting_api.py test_tax_estimate.py test_b04_pilot.py test_accounting.py -q` | 既有基线：`63 passed`（`8b185b9b` 修复停机缺陷后） |
| `pytest test_migration_idempotency_ledger.py test_ag00_migration_idempotency.py -q` | `14 passed` |
| 全量 `pytest nexus_backend/tests -q` | 本机未跑完，见 §7 |

因本批改动只涉及格式与检查器，账务组合的行为等价性由「格式仅版式 + 全量单测通过」共同保证。

## 七、剩余问题与未验证项

1. **PostgreSQL 16/17 门槛未执行（阻断）**
   - 证据：`docker` 不存在；`psql --version` 不存在；WSL 内 `psql (PostgreSQL) 14.24`，`/usr/lib/postgresql` 仅 `14`。
   - `scripts/ak05_postgres_gate.py` 要求 `--major 16|17` 且 `prepare --allow-create-local-databases` 自建一次性集群，本机无法满足，也**没有**用 14 冒充。
   - 未做：AK05 / C01 / 独立复核 / 逻辑版本四组 PG 冻结用例；未减少、未合并用例计数。
2. **全量后端套件未在本机跑完**
   - 实测速率约 7 用例/分钟（15 分钟推进 104 条，0 失败 0 错误、20 跳过），3,985 条用例本机需数小时；已执行定向回归，全量由 CI 承担。
   - 因此「本地 CI 等价命令全部完成」一项**未完全达成**，仅达成除 pytest 全量与 PG 之外的部分。
3. **远端 CI 未执行**：本批不提交、不推送、不改 GitHub/云平台配置，故不能把本地成功写成远端成功。
4. **构建的本地环境注意**：`vite build` 在系统临时目录会报 `[vite:esbuild-transpile] remove ... Access is denied`（esbuild 删临时文件被拒），把 `TMP/TEMP` 指到仓库内 `test-reports/` 后成功；这是本机环境问题，与代码无关，CI 不受影响。
5. 已登记的历史债是**技术债**而非豁免：新迁移必须 `CREATE OR REPLACE FUNCTION` + `DROP TRIGGER IF EXISTS`，否则 CI 直接失败。

## 八、复现命令

```bash
# 1. 迁移静态检查
./.venv/Scripts/python.exe scripts/check_migration_idempotency.py
./.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_migration_idempotency_ledger.py -q

# 2. 格式与静态检查（CI 同款）
./.venv/Scripts/python.exe -m ruff check nexus_backend/app services/api_gateway scripts
./.venv/Scripts/python.exe -m ruff format --check nexus_backend/app services/api_gateway scripts

# 3. 前端
npm run format:check && npm run lint && npm run typecheck && npm test && npm run build

# 4. 核心浏览器门槛（dev + preview，各 106 例）
node scripts/run_e2e.mjs --mode both
```

## 九、边界声明

- 本地验收：完成（SQLite + 虚构企业 + 合成身份）。
- PostgreSQL 验收：**未执行**（环境缺失，见 §7.1）。
- 远端 CI：**未执行**（本批禁止推送）。
- 真实模型：**未触碰**（`ACCOUNTING_AI_*` 未配置，测试全部走确定性桩）。
- 生产验证：**未执行**。
- 结论：本批可作为「本地可重复候选」，**不能**据此宣称远端 CI 或 PostgreSQL 验收通过；Q02 及后续批次应在 PG16/17 门槛补齐后再放行。
