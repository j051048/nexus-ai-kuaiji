# OPT04：租户隔离补强、企业级锁、重复过账约束

先读 `OPT00_MASTER.md`。前置：OPT01（迁移编号与金额列）。

## OPT04-A  新表补 FORCE ROW LEVEL SECURITY

**问题。** 近期新表只 `ENABLE` 未 `FORCE`。2026-11-16 的清单：`ai_route_decisions`、`fee_receivable_items`、`fee_receivable_allocations`、`fee_receivable_refunds`、`fsc_company_profiles`、`lease_invoice_reds`、`repair_rate_cards`、`oa_approval_snapshots`、`oa_material_requests`、`monthly_external_evidence`、`sync_stub_events`。

**核实。** 写一个小脚本或测试，解析 Postgres 迁移，列出"有 `ENABLE ROW LEVEL SECURITY` 但全部迁移中没有对应 `FORCE ROW LEVEL SECURITY`"的表。注意早期迁移用循环动态启用（`001_accounting.sql`），静态解析只能覆盖显式写出的表；能连本地 PG 时再用 `pg_class.relforcerowsecurity` 查一次实际结果，以数据库结果为准。

**做法。** 新增 Postgres 迁移，对清单逐表 `FORCE`。同时检查这些表的策略：

- 是否写了 `TO accounting_app`。
- 读和写是否共用一条只判断"可见"的策略。线索：`247_fee_receivable_items.sql` 用 `company_visible` 同时管读写。写入应另有能力判断，照同类老表的策略写法补齐。

**先失败测试。** 仓库已有全表 FORCE 断言（`nexus_backend/tests/test_demo_cloud_postgres.py` 约 36–41 行）。先跑它确认当前失败。再为 `fee_receivable_*` 和 `fsc_company_profiles` 各加一条跨事务所负向用例：事务所甲的业务身份读不到、写不进事务所乙的行；只读角色写入被拒。

**验收。** 上述用例在 PG16、PG17 通过。

## OPT04-B  查询显式带事务所条件

**问题。** 部分查询只按 `company_id` 过滤，靠 RLS 兜底。SQLite 没有 RLS，会跨事务所。线索：`nexus_backend/app/fsc/profiles.py` 中

```
SELECT COALESCE(MAX(version),0) FROM fsc_company_profiles WHERE company_id=?
UPDATE fsc_company_profiles SET status='superseded' WHERE company_id=? AND status='draft'
```

一个企业可以属于多个事务所（`firm_clients` 以 `(firm_id, company_id)` 为键）。

**做法。**

1. `app/fsc/profiles.py` 所有读写加 `firm_id` 条件。版本号按 `(firm_id, company_id)` 递增。
2. 唯一约束 `UNIQUE(company_id,version)` 改为 `(firm_id,company_id,version)`：新增前向迁移，两种库都做。
3. 用同样思路检查本轮新增的模块：`app/fsc/organization.py`、`app/tax/fees.py`、`app/tax/fee_guard.py` 及 `fee_receivable_*` 相关查询。只改确实缺条件的地方，逐条列在状态文件。
4. 不做全仓排查。老代码中"先按主键取出再鉴权"的模式不在本模块范围。

**先失败测试。** SQLite 下建两个事务所共享同一企业，各自创建档案草稿。事务所甲新建版本后，事务所乙的草稿状态不应被改成 `superseded`，两边版本号互不影响。修改前失败。

**验收。** `python -m pytest nexus_backend/tests/test_fsc01_profiles.py nexus_backend/tests/<新增用例> -q`，PG 侧同样通过。

## OPT04-C  新增写入点改用企业级锁

**问题。** 本轮新代码仍用全局锁。线索：`app/fsc/profiles.py`、`app/tax/basis.py` 中的 `transaction(write=True)` 未声明范围。

**做法。**

1. 先读 `nexus_backend/app/accounting/database.py` 的 `transaction` 和 `nexus_backend/app/accounting/locking.py`，再读一个已用企业级锁的服务作范例（搜索 `write_scope="company"`）。弄清它要求 `actor`、`company_id`，以及数据库侧 `app.write_scope_company` 触发器对"写入表必须属于声明企业"的强制。
2. 仅迁移以下范围：`app/fsc/` 全部写入、`app/tax/basis.py`、以及 `fee_receivable_*` 相关写入。
3. 企业级锁下若写到不属于该企业的表会被数据库触发器拒绝。遇到这种情况说明该事务本就跨范围，保持全局锁并在状态文件说明，不要绕过触发器。
4. 完成后从 OPT02-D 的基线文件里删除对应条目（OPT02-D 未做则跳过这一步并注明）。

**先失败测试。** PG 双会话：会话一在企业甲上开启写事务不提交；会话二对企业乙执行同类写入。修改前会话二阻塞（设短的 `lock_timeout` 断言超时），修改后成功。同企业的两个写入仍然串行。

**验收。** 新增用例 PG16、PG17 通过；相关模块现有测试全过。

## OPT04-D  同一来源不可重复过账的数据库约束

**问题。** `nexus_backend/app/posting/service.py` 过账前查询 `voucher_sources` 判断是否已过账，再插入。`voucher_sources` 上只有非唯一索引（`105_b03_posting_core.sql`）。现在靠全局锁保证不重复，将来缩小锁范围就会出问题。

**做法。**

1. 读 `voucher_sources` 的结构和红冲、更正流程：同一来源在"原凭证加红冲凭证加更正凭证"下合法地出现多行。唯一性的正确粒度需要你从代码中确认，通常是"同一来源、同一关系类型下只有一张有效的原始过账凭证"。把你确认的粒度和依据写进迁移注释。
2. 迁移前先检查存量数据是否违反将要加的约束。有违反则停下，写入状态文件，不删数据、不加约束。
3. 新增唯一索引（需要条件时用部分唯一索引），两种库都做。
4. 应用层保留现有检查用于友好报错，并把唯一冲突映射为 409。

**先失败测试。**

- 绕过应用检查直接插入第二行同来源记录，修改前成功，修改后被拒。
- 正常红冲和更正流程仍然通过。
- PG 双会话并发过账同一来源，只成功一笔，另一笔 409。

**验收。**

```
python -m pytest nexus_backend/tests/test_posting_service.py nexus_backend/tests/<新增用例> -q
```

PG16、PG17 同样通过。
