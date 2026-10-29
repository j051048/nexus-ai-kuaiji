# N02 PostgreSQL 权限、来源与并发验收（2026-10-28）

## 结论与边界

本轮在本机独立 PostgreSQL 16.15、17.11 数据库完成 **每版 45 passed、0 failed、0 skipped**：32 项原生数据库守卫 + 13 项真实服务/双会话验收。两版使用同一源码候选，运行前后逐文件 SHA-256 未变化。另有 SQLite 结构守卫 **11 passed**。

这里的“通过”仅指所列用例的本地技术验收，不代表真实税务口径已签收、已申报、已上线，或 C01 全量恢复门槛已重新完成。未接模型、未访问生产、未提交推送。

## 修改归属

本子任务新增：

- PostgreSQL 171：管理/复核能力、可信 actor、企业/账套/期间/生效档案绑定；新增来源快照列并冻结。
- PostgreSQL 172：复核时必须有 `n02-tax-v2` 快照，绑定事务所、企业、长期账套、月度账套、期间和档案；旧空快照不能复核。
- PostgreSQL 173：快照 8 项整数汇总与存储字段逐项相等、发票行 JSON 语义相等、差异等于税额减正式账余额；金额列扩为 BIGINT，受通用金额上限约束。
- SQLite 108、109：相应结构/快照/金额守卫。SQLite 没有 PG 的 RLS 和数据库角色，不能把这些结构约束称为 PG 权限等价实现。
- `test_tax_workpaper_postgres.py`：改掉管理员执行攻击 SQL、任意数据库异常算通过、同值更新和过短自审理由等假阳性夹具。
- `test_n02_tax_concurrency_postgres.py`、`test_tax_workpaper_scope_sqlite.py`。

原 PG169/170、SQLite106/107、C01 迁移和测试未由本子任务修改。171/172 在各自应用后发现的新要求分别用 172/173 前向追加，未回写已应用校验和。主代理另行修改税务服务/router、来源重算和导出边界，本报告的候选指纹包含它们。

## 环境和权限

| 环境 | 版本号 | 受限运行账号 | 验收 |
|---|---:|---|---|
| PG16 | 160015 | n01_app_pg16 | 45 passed，14.27 秒 |
| PG17 | 170011 | n01_app_pg17 | 45 passed，14.32 秒 |

两版均实查：`fsync=on`、`synchronous_commit=on`、`full_page_writes=on`；业务登录账号的 superuser、bypassrls、createdb、createrole 全部为 false。连接仅允许本机 loopback 和既有本地测试端口；口令从忽略的本机配置读取，只存在子进程环境中，未写进报告。

新库由本轮创建，与既有 n01_gate、n02_fresh、C01 库无共享数据。管理员仅用于建库、迁移和虚构身份；攻击 SQL 使用受限登录，`SET LOCAL ROLE accounting_app` 后只安装可信 actor 上下文，**不经过 Python AccessControl 或 write_lock**，必须由 RLS/触发器兜底。服务测试则调用真实 `TaxWorkpaperService`，不使用 HTTP mock。

原生 guard 夹具刻意提供合成底稿和最小快照，仅检验数据库结构与授权；它不冒充经真实来源重算的税务证据。服务验收使用虚构发票导入行，正式账查询实际执行但其中没有正式凭证；“正式凭证来源变更”的非空数据回归由主代理的相关本地用例另行列证，不能把本报告的空正式账结果替代它。

## 先红后绿

| 红测阶段 | 实际失败 | 证据 |
|---|---|---|
| 原170权限/归属 | 两版各 5 failed / 9 passed | editor 可 reviewed、可建税务档案，错误企业/档案/期间可落库 |
| 快照复核 | PG16 10 failed / 15 passed | 空/坏/旧 schema、错 firm/company/book/period/ledger/profile 可复核 |
| 金额绑定 | PG16 7 failed / 25 passed | 改税额/发票行/差额、bool/小数/字符串 totals 未拒绝 |
| BIGINT边界 | PG16 1 failed / 11 passed | 4,000,000,000 分正常业务触发 integer out of range |
| SQLite范围/快照 | 5 failed | 范围与旧空快照未阻断，快照列缺失 |
| SQLite金额 | 6 failed / 5 passed | 金额/行/差额与严格整数未校验 |

失败日志、JUnit XML 在本机忽略目录 `tmp_test/n02-pg-hardening/`。这些红测是真实错误/未抛错，不依赖注释或静态搜索。后续把原生 SQL 夹具进一步收紧为绕过 Python 写锁后，两版 32 项再次全部通过。

## 最终验收覆盖

- editor 只有编辑权限不能原生复核；RLS 返回更新 0 行，原稿仍 draft。
- 档案创建要求管理权限；创建人、复核人不能伪造；制单与复核必须不同。
- 企业/账套/生效档案/月份必须匹配；快照 scope 必须匹配长期账套关联。
- 新建 reviewed、改写身份/快照/已复核数字、删除、再次复核均拒绝。
- 完整合法复核及版本/摘要绑定导出成功；read/edit 不能导出；错版本/摘要拒绝且不落导出审计。
- 有复核权限的 reviewer 自己生成底稿后自审，明确 403 且包含“制单人不能复核”，不把“没有复核能力”当作独立性验证。
- 两个真实 PG 会话：来源写入或撤权先提交，已经取得旧 scope 的 review/export 在事务内重新检查并拒绝；断言两会话 backend PID 不同。
- 相反顺序：export 持企业锁，在来源校验前暂停；第二受限写会话在 `pg_stat_activity` 中真实进入 Lock 等待。先发布完整旧快照 CSV，再允许来源写入提交；下一次导出因来源变化 409，审计不重复增长。
- 两次同时 review 只能一次成功、一次 409，只有一条复核审计。
- 4,000,000,000 分可生成、独立复核并导出；快照 totals 是严格整数且逐项对应金额列，发票行语义一致。
- 导出审计绑定实际版本、来源摘要、复核身份、CSV 字节 SHA-256。

## 复跑与证据

在已配置隔离测试 DSN 的环境运行：

```text
python -m pytest -q -p no:cacheprovider \
  nexus_backend/tests/test_tax_workpaper_postgres.py \
  nexus_backend/tests/test_n02_tax_concurrency_postgres.py
```

最终 JUnit/日志与环境指纹：

- `tmp_test/n02-pg-hardening/final-pg16.xml`、`final-pg16.log`、`final-pg16-metadata.json`
- `tmp_test/n02-pg-hardening/final-pg17.xml`、`final-pg17.log`、`final-pg17-metadata.json`
- `tmp_test/n02-pg-hardening/sqlite-amount-green.xml`：11 passed。

这份证据经过新空库 0→170，再按实际开发顺序前向迁入171→172→173。主代理另做最终 0→173 全新重放和重复 migrate，不能从本报告推断那项已通过。

三个测试文件 `ruff check` 通过；`scripts/check_migration_idempotency.py` 退出0。静态检查仍明确有需真实 PG 核验的条目，不将其解释为全库动态幂等证明。

## 候选源码 SHA-256

下列为原始文件字节 SHA-256；数据库 schema_migrations 使用项目 `migration_digest()` 的换行归一化 SHA-256，因此不应拿原始字节值直接替换数据库校验和。

| 文件 | SHA-256 |
|---|---|
| `nexus_backend/app/tax/workpaper.py` | `e999c508c54c0b1a1f81e435d597f3851d647cc9336e42c48399ae955c315957` |
| `nexus_backend/app/tax/router.py` | `13c0da5079c6c670fc647ed19fdb16dfe2358c917508ed52f65ecd6157826ea3` |
| `nexus_backend/tests/test_tax_workpaper_postgres.py` | `ab44761d5cbca1cbf28787cc88e74cc36555157af72c4d818fb7a487f7ec3353` |
| `nexus_backend/tests/test_n02_tax_concurrency_postgres.py` | `f3a2f64287b11aa528a4c7d4a2611683e2942ecc90a5778baa3ba1b0f3ce7dd8` |
| `nexus_backend/tests/test_tax_workpaper_scope_sqlite.py` | `9e77639b9770b24e71142f4594ff3555b166bd673ca09c5997fa62ac36970b5c` |
| `nexus_backend/migrations/postgres/171_n02_tax_authorization.sql` | `ceed72515d13ff23dc316870ad6b6c27ae45789d51357142af99812f086774a9` |
| `nexus_backend/migrations/postgres/172_n02_tax_source_snapshot.sql` | `6dacd746128254859372db6dafa6ec675647ff82e35ad8482689da92f2142bff` |
| `nexus_backend/migrations/postgres/173_n02_tax_snapshot_values.sql` | `12c93e027a90a0513d7c1b8ce57ea702bc02cf1eeb4058b9db44f021e395a5d0` |
| `nexus_backend/migrations/sqlite/108_n02_tax_scope.sql` | `616577478d2354b2b56268afe1a9fab81303cac446023a82366dac27fd0ec738` |
| `nexus_backend/migrations/sqlite/109_n02_tax_snapshot_values.sql` | `6efb37057eba171f69deb2ffde9e5b36047664147458937f52caff36c8186528` |

## 尚未证明的范围

- SQL守卫验证权限、身份、范围和快照/行一致；税率合法性、真实来源完整性与计算口径仍需服务确定性重算和会计确认，不能只信任手写 snapshot。
- 未接一般纳税人抵扣资格、季度申报、期初留抵、税局直连或真实缴款。
- 未进行生产压力、真实客户资料或真实税务申报验收。
- 本轮未改 C01；没有把这些45项当作整个产品或C01完整恢复的替代验收。
