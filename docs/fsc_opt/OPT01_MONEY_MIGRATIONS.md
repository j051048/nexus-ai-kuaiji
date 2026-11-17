# OPT01：金额列 64 位、迁移完整性、税额取整统一

先读 `OPT00_MASTER.md`。无前置。

## OPT01-A  Postgres 金额列扩为 BIGINT

**问题。** 部分 `*_cents` 列是 `INTEGER`，上限约 2147 万元。应用层上限是 `MAX_CENTS = 99_999_999_999_999`（`nexus_backend/app/accounting/invariants.py`）。SQLite 整数是 64 位，SQLite 测试发现不了溢出。

**核实。**

```
grep -n -E "_cents +INTEGER" nexus_backend/migrations/postgres/*.sql
```

2026-11-16 的结果是 14 处：`169_tax_workpapers.sql` 9 处、`225_tax_source_adapters.sql` 1 处、`249_fee_receivable_allocations.sql` 2 处、`251_fee_receivable_refunds.sql` 2 处。以你实际跑出的清单为准，后续新增迁移里若又出现也一并处理。

**做法。**

1. 新增一个 Postgres 前向迁移，对清单中每一列执行 `ALTER TABLE ... ALTER COLUMN ... TYPE BIGINT`。不改任何现有迁移文件。
2. 检查这些列上是否有依赖列类型的触发器、函数参数、视图或生成列，有则在同一迁移中同步处理。
3. SQLite 侧新增一个对应编号的空操作迁移并注释原因，保持两边编号节奏与仓库现有做法一致（先看现有迁移如何处理"仅一侧需要"的情况，照做）。

**先失败测试。**

- 新增 PG 用例：对清单中每张表，经服务层或直接以业务角色写入 `3_000_000_000` 分（3000 万元），修改前应报整数溢出。
- 新增一条静态扫描测试（不需要数据库）：解析 `nexus_backend/migrations/postgres/*.sql`，按迁移顺序计算每个 `*_cents` 列的最终类型，断言全部为 `BIGINT`。这条测试将长期防止回归。

**验收。**

```
python -m pytest nexus_backend/tests/<新增扫描测试> -q
python -m pytest nexus_backend/tests/<新增PG用例> -q     # PG16 与 PG17 各一次
```

迁移首次应用和重复应用都通过（按仓库现有迁移幂等检查方式）。

## OPT01-B  还原被削弱的已提交迁移

**问题。** 工作区里 `nexus_backend/migrations/sqlite/162_fsc_company_profiles.sql` 被改动，删掉了三处外键：`created_by REFERENCES users(id)`、`confirmed_by REFERENCES users(id)`、`FOREIGN KEY(company_id,firm_id) REFERENCES firm_clients(firm_id,company_id)`。该文件已在提交 `767f7cdf` 中入库。

**核实。**

```
git diff nexus_backend/migrations/sqlite/162_fsc_company_profiles.sql
```

**做法。**

1. 弄清为什么删外键：通常是某个测试夹具插入了不存在的用户或企业关系。查 `nexus_backend/tests/test_fsc01_profiles.py` 及相关夹具。
2. 正确修法是修夹具，让它建立真实的用户和 `firm_clients` 行，而不是去掉约束。
3. 把该迁移文件内容恢复到与 `git show HEAD:nexus_backend/migrations/sqlite/162_fsc_company_profiles.sql` 一致。用编辑方式手工还原，不用 `git checkout`。
4. 如果确有业务理由需要改表结构，新增前向迁移并在状态文件说明理由。

**验收。**

```
git diff --stat nexus_backend/migrations/sqlite/162_fsc_company_profiles.sql    # 应无输出
python -m pytest nexus_backend/tests/test_fsc01_profiles.py -q
```

同时检查 Postgres 侧对应迁移是否有这三处外键；没有则在状态文件"顺带发现"中记录，不擅自添加。

## OPT01-C  税额取整统一

**问题。** 三处规则不一致：

- `nexus_backend/app/tax/workpaper.py` 的 `_ratio`：`(cents*rate_bp+5000)//10000`，四舍五入。
- `nexus_backend/app/tax/basis.py`：`base * rate_bp // 10000`，向下截断。
- `nexus_backend/app/tax/estimate.py`：`quantize(Decimal("0.01"))` 未指定舍入模式，Python 默认是银行家舍入。

**做法。**

1. 在 `nexus_backend/app/accounting/invariants.py` 增加一个整数分乘基点的函数，默认四舍五入，负数按绝对值四舍五入后还原符号。写清文档字符串。
2. 三处改为调用它。`estimate.py` 的 `quantize` 全部显式写 `rounding=ROUND_HALF_UP`。
3. 搜索 `nexus_backend/app` 下其余 `// 10000` 和 `quantize(` 的金额用法，同类问题一并收敛；非金额用途不动。
4. 截断若是某项政策的明确要求（例如某些税种按规定舍去），不要硬改：保留并在代码注释和状态文件中写明出处。没有出处就统一为四舍五入。

**先失败测试。** 构造让三种规则产生不同结果的输入（例如 `cents=150, rate_bp=300` 得 4.5 分；`cents=250, rate_bp=300` 得 7.5 分），断言三个模块对同一输入结果一致。修改前至少一条失败。

**验收。**

```
python -m pytest nexus_backend/tests/test_tax_estimate.py nexus_backend/tests/<新增一致性测试> -q
python -m pytest nexus_backend/tests -q -k "tax"
```

已有税务测试若因取整变化而失败，逐条判断：是原期望值本身依赖了错误规则，还是你改错了。前者更新期望值并在状态文件逐条列出旧值、新值和理由；不允许批量改期望值。
