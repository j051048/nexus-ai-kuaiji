# 五条历史兼容性标签前向修复（本地验收）

结论：**本地及 PostgreSQL 16/17 验收通过，尚未推送或执行线上迁移。**

基线 HEAD：`2e811d9ebd8020980c0d6475782790fc10440ad6`，开始时工作区干净。最终源码指纹：`6015b69238e1eedab6c05f976262f2929ef3614f75e92e891d02047101f13f06`。机器可读证据：[验收 JSON](HISTORICAL_COMPATIBILITY_REPAIR_20261031.json)。

## 修复范围

新增 `nexus_backend/migrations/postgres/191_historical_compatibility_labels.sql`，只将 PostgreSQL 历史版本 **87、138、148、149、150** 的 `accounting.schema_migrations.compatibility` 从 `incremental` 收紧为 `breaking`。这些历史 SQL 没有兼容增量声明，按现有规则应保守分类为 `breaking`。

迁移先按版本升序锁定并验证全部五条历史记录。每条的 checksum 必须匹配固定的原始摘要，记录必须存在；任一不符便整体拒绝，不补写丢失历史，不重跑旧 SQL，不修改 checksum、版本或原应用时间。全部核验后才更新错误标签，标签本来正确则不更新任何记录；重复运行无副作用。

191 自身保守标记为 `breaking`，让旧镜像不能忽略新的兼容性边界。未改任何权限、RLS、业务表、复核、记账、费用或导出逻辑，也未新增 SQLite 迁移：SQLite 版本号属于另一套历史，不能按相同数字修改。

同时完善 `scripts/migrate_accounting.py status`：状态核验现在包含兼容性标签，标签错误不再显示“已应用”，`status --strict` 会返回失败。摘要错误和迁移缺失仍优先报告，原有仅比较摘要的纯函数调用方式保持兼容。

## 失败测试与验证

先运行失败测试，再增加迁移和修复代码：

- 本地红测：4 failed / 5 passed，固定缺少前向修复以及标签错误被漏检的问题。
- 真实隔离 PG16 红测：两会话调用原迁移器后，五个标签仍为 `incremental`，断言失败。
- 新增专项逐一覆盖：全部/部分错误、已正确状态、原记录缺失、摘要损坏（标签错误和已正确两种状态）、重复执行、并发迁移、运行账号拒绝、严格状态检测，以及旧镜像遇到 breaking 191 时拒绝启动。

故障注入只在本轮新建的本地数据库进行。缺行/坏摘要场景始终回滚；实际修复 SQL 没有改写历史 checksum 的语句。模拟 190→191 升级时仅重建新迁移的本地登记状态，不回退业务 schema。

| 检查 | 结果 |
| --- | --- |
| 本地迁移、兼容性、历史摘要、复核、权限和旧稿回归 | 94 passed，0 skipped |
| PG16 原有企业隔离、复核/导出及并发回归门禁 | 90 passed，0 skipped |
| PG17 同组门禁 | 90 passed，0 skipped |
| PG16 新修复专项 | 24 passed，0 skipped |
| PG17 新修复专项 | 24 passed，0 skipped |
| 修复后本地状态 | 两个库均为 191，179 条 PostgreSQL 登记，全部状态正确 |
| 历史文件完整性 | 178 个既有 PG + 120 个 SQLite 迁移，共 298 个摘要全部未变 |
| Ruff、格式、Git whitespace 检查 | 通过 |

以上集合存在重叠，不相加宣称独立业务覆盖总量。最终 PG 回归和专项报告绑定同一完整候选指纹。CI 的 PG16/17 矩阵已加入这 24 个专项，校验精确用例名称、模块、数量及跳过/失败状态；相同校验逻辑已在两份本地 JUnit 上执行。**未推送，因此没有远端 Actions 通过的声明。**

静态迁移扫描通过；191 的 DO 块被列为需专项检查项，其实际执行、拒绝及重复执行行为由上述两个 PostgreSQL 主版本验证，不能仅凭静态扫描声称幂等。

## 证据及边界

本机忽略目录中的证据：

- `tmp_test/historical-compatibility-red-unit.xml`
- `tmp_test/historical-compatibility-red-pg16.xml`
- `tmp_test/historical-compatibility-local-regression.xml`
- `tmp_test/historical-compatibility-final-pg-access/summary.json`
- `tmp_test/historical-compatibility-final-native/summary.json`

管理员/应用/worker 的本地连接配置保存在私有忽略目录，不能提交或输出。全程未连接生产库、真实模型或通知渠道，未提交、未推送、未重启线上服务。未覆盖其他智能体修改。

**线上数据库本轮没有改变，仍是此前核验的 190。** 后续上线 191 时，需要核验备份并协调包含 191 的 API/worker 候选及迁移窗口；旧镜像看到 breaking 191 会拒绝启动，不能单独迁移后仍假设旧镜像可以任意重启或回退。
