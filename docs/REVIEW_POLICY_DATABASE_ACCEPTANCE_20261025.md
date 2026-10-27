# 复核责任数据库验收补录

日期：2026-10-25。基线：`3fc1ab6e11761ba36490f1858b85fcff356441cc`。本记录仅覆盖本轮数据库实现及所列本地测试；整体验收见 `REVIEW_POLICY_ACCEPTANCE_20261025.md`。

## 最终本地结果

| 检查 | 结果 | 本地证据 |
| --- | --- | --- |
| SQLite 数据库专项及批量回归 | 71 passed，0 failed，0 skipped，退出码 0；260.21 秒 | `.codex/review-responsibility/candidate2-final-71.log`、同名 XML |
| 历史清偿完整文件 | 18 passed，退出码 0；55.34 秒 | `.codex/review-responsibility/logical-version-legacy.log`、同名 XML |
| 本轮 PostgreSQL 迁移静态纪律 | `scripts/check_migration_idempotency.py --after 161` 通过 | 限定新增 162–165，不代表旧迁移全量静态检查通过 |
| 最终 SQL 字节 | 8 个文件均与冻结清单 SHA256 相同 | `.codex/review-responsibility/candidate2/manifest.json` |

71 项由 `test_review_responsibility_database.py` 的 39 项和 `test_batch_review.py`、`test_stage11_review_packages.py` 合计 32 项组成。批量夹具使用真实不同制单、复核身份，保留原权限、部分成功、幂等和错误断言；未全局启用单人办理。历史清偿的 `applied_version = expected_version + 1` 断言保留。

PostgreSQL 16/17 专项执行代理已报告：最终候选在全新八个隔离库完成 1–165 迁移及校验和重入，新 28 项和旧工作流/逻辑版本 80 项两版业务执行均通过。最终精确身份及源码指纹门禁由该代理、主任务统一复跑记录；中途因测试文件变动而拒绝的门禁结果不计为通过。本补录不替代最终门禁报告。

## 冻结约束复核

- 手工申请由当前编辑者提交完整内容及原命令键；数据库核对账套、期间、政策版本、金额平衡和内容指纹。审批只能由当前复核者完成，独立模式必须与制单人不同，单人模式始终标记非独立。已审内容及申请身份不可改写。
- 草稿责任和审批凭据由受保护触发器生成。凭据绑定最终待审版本、内容修订号和实际内容指纹，应用角色无直接写入权限。审批后分录、辅助项及责任历史不可改写；无新凭据的旧已审记录不会被补造为独立复核。
- 正式记账在命令完成时比对完整表头、每一条分录、辅助值、来源、账户版本及快照。延迟外键要求同一事务内产生内部完成证明，不能提交只有表头的半成凭证。手工请求、已审草稿、期末凭据均有唯一使用约束；冲销还须与原凭证方向完全相反。
- 撤回仅允许仍有编辑权的原制单人撤回当前待审版本，并追加历史；撤回后不能审批。未撤回且未完成正式记账的手工申请，包括已审未记账申请，均阻断关期。七项预检保留原六项并核对新增手工待办的真实状态。

本次限定核查未发现上述链条的新阻断问题。证据不覆盖数据库超级管理员主动篡改、生产身份配置或正式上线切换。

## 逻辑版本与红绿证据

一次逻辑改稿保留业务版本加一；逐次真实内容变化仍增加责任修订并记录当前制单人。新事务首次原生分录写入会使旧版本失效；同事务后续行写入不会让业务版本任意跳号。空操作头更新不占用变更标识，SQLite 无可信事务的写入被拒绝。初始月度生成仍为版本 1，同时校验完整初始责任历史、当前事务、原计划、目标月份和确切分录；修改后恢复原值不能伪装成初始生成。

先前真实失败证据保留于 `.codex/review-responsibility/`：`database-red`、`completion-red`、`periodic-red`、`workflow-red`、`seven-checks-red`、`monthly-red`、`batch-fixtures-red`、`transaction-version-red`。其中逐行抬升业务版本导致成熟清偿契约失败，修复责任事务标识后才取得最终 71 项和历史清偿 18 项通过；未改写历史清偿守卫。

## 候选与迁移边界

本轮新增 PostgreSQL 162–165、SQLite 100–103。最终冻结名为 `review-responsibility-candidate-2-logical-version-final`。本次核对 `git diff HEAD -- nexus_backend/migrations` 未显示既有已提交迁移改动，新增迁移均为独立文件。

候选 1 的 PostgreSQL 162–164、SQLite 100–102 原字节、哈希及废弃说明保留于 `.codex/review-responsibility/candidate1/`。候选 2 规范本轮函数/触发器声明和 breaking 边界，并在尚未应用候选 2 时完善可信事务逻辑；中间字节另存 `candidate2-before-logical-version/`。旧隔离库保持原校验和，不改写 `schema_migrations`，不跳过校验。最终候选只在全新空隔离库应用；冻结后任何 SQL 修复须用后续新迁移。

旧候选或中途通过数不作为最终候选验收数。所有数据库、日志和候选归档均为本地忽略产物，不进入源码提交；未连接生产或真实模型。

## 后续实测补充：PG 166 历史清偿锁入口

追加原 `test_stage13_legacy_settlements_postgres.py` 的 7 项验收后，PostgreSQL 16/17 均实测 4 failed、3 passed：服务已经声明单企业事务，而历史 `legacy_clearance_guard` 仍要求进入旧全局锁域，合法核销在插入命令时被拒绝。此前 80 项工作流通过没有覆盖这 7 项，不能据此前结果宣称完整历史清偿链已通过。

新增 `166_legacy_clearance_company_lock.sql`，显式 breaking，SHA256 为 `a48829eb695b582c1d2f077843773b2af538d21b6093e507168b401a467024a6`。只将完整原守卫的一处 `lock_legacy_write()` 调用替换为 `lock_company_write(NEW.firm_id,NEW.company_id)`：已声明企业事务严格匹配事务所及企业，未声明时仍回退原全局锁；先取语句锁再取行锁的顺序不变。原金额、剩余额、版本加一、当前身份、跨企业、冻结原件、完整分录及独立撤销断言全部保留。未修改 163 的表范围解析、服务层、SQLite 或任何已应用迁移。

函数结构差异及哈希证明：`.codex/review-responsibility/166/guard-structure.diff`、`proof.json`。新增迁移再次通过 `--after 161` 静态检查。

166 应用后两版先得到 6 passed、1 failed，剩余失败是旧正向夹具使用核销制单人自行审批。夹具保留原业务断言，追加自审 403 断言并使用已有管理者独立审批后，两版均为 **7 passed、0 skipped**（18.74 / 18.09 秒）。原全局预览、单企业核销/分配/撤销、错误分录阻断及余额恢复路径均执行成功。报告为 `tmp_test/review-policy-pg/pg16-166-legacy7-green.log`、`pg17-166-legacy7-green.log` 及同名 XML；最初真实锁错误保留在 `pg16-candidate2-legacy7-red`、`pg17-candidate2-legacy7-red`。

包含 166 的全新空库最终门禁由主任务继续汇总；本节不将专项通过冒充整套门禁通过。
