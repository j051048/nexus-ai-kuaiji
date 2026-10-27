# E01 性能与可维护性：基线、已做的一处优化与债务排序

日期：2026-10-27　候选：`tmp_test/qr01-integration`（与 `main` 同起点 `32e3042d`）
边界：本地 SQLite/PG16/PG17 一次性库、虚构数据；未提交、未推送、未改远端 CI。
本批只做**可测量**的部分：给基线、做一处收益确定且行为不变的改动、把剩余债务按收益/风险排序。
没有做任何「看起来更快」的猜测性重构。

---

## 1. 基线（本机实测，脚本见 `tmp_test/e01_baseline.py`、`tmp_test/e01_profile_app.py`、`tmp_test/e01_profile_fixture.py`）

| 指标 | 数值 | 说明 |
| --- | --- | --- |
| `import app.main` | **1.372 s** | 冷进程首次导入 |
| `create_app(seed=True)` | **1.503 s** | 446 条路由，SQLite 临时库 |
| 其中 FastAPI 装配 | **1.410 s（94%）** | 851 次 `add_api_route`、443 个内联装饰器、52 次 `include_router` |
| 其中 pydantic 依赖编译 | 1.050 s | 2,682 次 `TypeAdapter` 构造（`get_dependant`/`analyze_param`） |
| 单测每例 setup | **2.615 s** | 业务夹具基准（7 用户 + 5 成员 + 账套） |
| 其中跑全部迁移 | **1.435 s（55%）** | 106 个 `executescript` |
| 其中夹具写入 | 0.667 s | 769 次单条 `execute`（身份注册、授权） |
| 其中连接关闭 | 0.296 s | WAL 检查点 |
| 授权查询 | **≈35 ms/家** | `access.company()` 单语句 5 join，故意无缓存 |
| PG 批量（200 家，见 P02） | 预检 6.99 s / 执行 28.00 s / 重跑 1.62 s | 冷库 PG16 |

结论一：**启动耗时几乎全是框架装配**（路由与依赖图编译），不是我们的代码。
结论二：**单测耗时的 55% 是每个测试重新回放 106 个迁移**，与测试内容无关。
结论三：单请求的热点授权是「每家一次、不缓存」的显式设计（`nexus_backend/app/accounting/access.py:42-55`），
200 家批量里占约 7 s，与 P02 预检实测对齐；**不是 bug，是安全换性能的取舍**。

## 2. 已做：SQLite 迁移模板（收益 2.05 倍，行为不变）

改动只在测试基础设施：`nexus_backend/tests/conftest.py`。
- 会话首次建一份「已迁移、未播种」的模板库（`_build_template()`），WAL 截断后关闭；
- 之后每个测试新建 SQLite 文件时，若目标不存在或为空则复制模板（`_wants_template()` / `shutil.copyfile`），
  `Database.__init__` 看到 `schema_migrations` 已满即跳过回放；
- 逃生阀 `ACCOUNTING_TEST_SCHEMA_TEMPLATE=0` 恢复旧行为，供必须观察「首次迁移」的用例使用；
- 未改任何产品代码、任何断言、任何跳过标记。

同一组 111 个用例的 A/B（同一台机器、同一命令）：

| 模式 | 结果 | 墙钟 |
| --- | --- | --- |
| `ACCOUNTING_TEST_SCHEMA_TEMPLATE=0` | 111 passed | **143.22 s** |
| 默认（模板） | 111 passed | **69.90 s** |

覆盖率不变、通过集合完全相同，**省下 51.2%（2.05×）**。单套 `test_tax_workpaper.py` 同样从 28.57 s → 13.88 s。
按 3,822 个用例、其中约 3,000 个走 SQLite 估算，全量一次可省 **约 33 分钟**；这直接影响的是「改一行能不能在十分钟内看到结果」。

回归面：`test_accounting.py`、`test_business.py`、`test_business_api.py`、`test_migration_structure.py`、
`test_qs01_migration_integrity.py`、`test_migration_idempotency_scanner.py`（111 例）+
`test_posting_service.py`、`test_posting_api.py`、`test_tax_workpaper.py`、`test_monthly_batch.py`、
`test_batch_preview.py`、`test_batch_operations.py`、`test_delivery_followups.py` 全绿。

## 3. 明确**不做**的两件事及理由

1. **不缓存授权结果**。把 200 家 × 35 ms 变成一条集合查询，需要把 `access.company()` 的谓词复制成第二份
   （`IN (...)` 版本）。两份谓词必然漂移，而这条谓词是「客户企业看不到别人数据」的唯一门闩；
   本项目的审计价值恰恰来自「授权只有一处实现、每次读库」。若将来真要提速，正确做法是把谓词下沉成
   数据库视图/函数（与 RLS 同源），而不是在 Python 里再写一遍——那是独立的一批工作，不在 E01。
2. **不拆 `create_app`**。1.4 s 的装配在生产只发生一次（进程启动），把它拆成十个函数不会让启动变快，
   只会把 870 行的装配体变成十个互相 import 的装配体。真正该拆的理由是可读性，那就该按业务域拆路由，
   并配套契约测试——同样是一批独立工作。

## 4. 债务排序（按「收益 ÷ 风险」，不是按代码行数）

| 优先级 | 债务 | 证据 | 为什么排这里 |
| --- | --- | --- | --- |
| 1 | 单测仍每例重建夹具（≈1.2 s） | 本轮实测 | 模板已拿走 0.66 s，剩下的是 769 条插入；下一步可做「已播种模板 + 测试内覆盖」，收益再翻倍，风险中等 |
| 2 | 写事务仍 56% 全局串行 | `transaction(write=True` 165 处 vs `write_scope=` 73 处 | 多事务所并发时才疼；有 077 触发器兜底，不会错，只会慢 |
| 3 | `is_postgres` 分支 185 处 | `grep -c` 统计 | 每加一个功能要写两遍 SQL，是真维护成本；收敛需要适配层设计与双库回归 |
| 4 | 路由与装配集中在 `main.py`（1,078 行/446 路由） | `app/main.py` | 可读性债；改动风险低但收益也慢，适合与下一次功能批次同行 |
| 5 | 审计写入样板 21 份 `_audit`、29 处裸 `INSERT INTO audit_events` | 全仓统计 | 一致性风险：新增动作可能漏字段；收敛成一个 `AuditLog` 是纯增益 |
| 6 | 前端 `App.tsx` 1,784 行/34 `useState` | 文件统计 | 只在改前端时疼；与 E01 的后端性能无关 |

## 5. 复现

```powershell
# 启动与装配
..\..\.venv\Scripts\python.exe tmp_test/e01_profile_app.py

# 单测夹具构成
..\..\.venv\Scripts\python.exe tmp_test/e01_profile_fixture.py

# A/B（模板开关）
$env:ACCOUNTING_TEST_SCHEMA_TEMPLATE="0"; ..\..\.venv\Scripts\python.exe -m pytest nexus_backend/tests/test_business.py -q
Remove-Item Env:ACCOUNTING_TEST_SCHEMA_TEMPLATE; ..\..\.venv\Scripts\python.exe -m pytest nexus_backend/tests/test_business.py -q
```

## 6. 本轮没有做的

- 未压测生产形态（多 worker + 连接池竞争），只有单进程测量；
- 未做 1,000 家上限与更长时段的 PG 采样（P02 已做 200 家/40 家/60 家争用）；
- 未动 `services/`（OCR 网关）与前端构建体积；
- 未提交、未推送、未改 CI 阈值。
