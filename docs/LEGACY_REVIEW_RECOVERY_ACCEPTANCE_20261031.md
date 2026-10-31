# 旧稿当前补核与新增权限、复核证据恢复验收

日期：2026-10-31。结论：**本地验收通过**。未连接真实模型、通知供应商或生产数据库；本任务未推送、部署或执行线上迁移。

## 同候选证据

- HEAD：`c127f309e19e80e1ae01769c3bd98565a988114a`。
- 完整源码指纹：`5e7fe05c73634bba3854e32cae1ef2db2b6b4c6bf43a48e23aa735a7650fb8a5`。
- 迁移指纹：`c3928631f53e104215bb7da6e9539e78c7935a6967b348e2f0f779ffa3dbb27d`。
- PG 新增前向迁移 188–190；SQLite 120。未改已应用迁移或登记摘要。完整恢复最低版本提升为 190，仍要求当前候选全部迁移对齐。
- 汇总：[JSON 验收记录](LEGACY_REVIEW_RECOVERY_ACCEPTANCE_20261031.json)。汇总程序校验 PG、恢复和前端三份报告的候选完全相同；不拼接不同候选的通过结论。

## 业务处理

在「正式账簿 → 旧稿补核」读取本账套旧稿，分两步办理：

1. 有编辑权限的人核对原内容，明确承担**当前版本**的制单责任，提交理由；冻结来源 ID、版本、内容指纹及当前复核政策。
2. 当前有正式复核资格的事务所会计重新核对并确认。默认要求与当前制单人、已知原制单人不同，业务申请人不能复核本人事项。明确启用的单人政策标为「单人办理，非独立复核」，仍保存实际身份、理由与时间。

支持来源草稿、手工凭证、期末草稿。补核写入独立的 `legacy_review_preparations` 和 `legacy_review_confirmations`，不改原制单/审批字段，不插入历史 `draft_review_receipts`，不把 `cutover_current_membership` 升格为历史审核证据。数据库生成本次真实时间，不能回填过去日期。

有效当前证据接入既有导出和原子记账边界。原冻结导出不变；重复确认、响应丢失重试与重复记账不重复生成结果。已正式记账的对象不能重新补核，须通过现有冲销或调整办理；关期或锁期不允许补核写入。政策变化时旧申请失效，可按新政策重新认领，旧记录保留。

SQL 直写仍检查企业、账套、来源/内容版本、当前权限、制单与复核责任、期间和公司写锁。补核表不可更新或删除，新写入使月结预检依赖失效。等待锁期间撤权的旧请求被拒绝；已实际完成的复核证据不会因复核人后续失权而被抹去。

主要接口：

- `GET /api/books/{book_id}/legacy-reviews`
- `GET /api/books/{book_id}/legacy-reviews/{kind}/{identifier}`
- `POST /api/books/{book_id}/legacy-reviews/{kind}/{identifier}/prepare`
- `POST /api/legacy-reviews/{request_id}/confirm`

列表只读取当前有权访问账套、尚未记账且缺少有效依据的对象，一次最多 100 项，完成后刷新继续；正常审核、原始历史和已记账记录沿用原入口。

## 完整恢复

恢复释放清单增加全部权限及复核字段，而不是仅记录行数：

- `staff_access_assignments`、`delivery_contact_access`；
- `ledger_review_policies`、`draft_preparations`、`draft_preparation_history`、`draft_review_receipts`；
- `review_authority_snapshots`、`legacy_review_preparations`、`legacy_review_confirmations`。

PG16/17 均以有实际数据的源库进行 `pg_dump/pg_restore`，恢复到不同集群的全新空库，核对数据库清单、原件 hash、身份映射、角色、RLS、列授权和恢复 hold。恢复后重新登录，验证只读范围、过期/撤销授权、只签收不读账、当前补核、独立复核后导出、税务底稿、原件下载与准确版本签收。真实本地 GoTrue 的恢复后 API 测试还读取当前补核证据，并拒绝已撤权会计及仅签收客户读取账簿。

在途通知保留未知结果、旧租约不能结算，恢复后两次调度的通知通道调用次数均为零。源/目标清单缺表、缺列或关键表为空不能当作恢复成功。

权限和补核相关数据由服务实际创建。少量“未知历史依据”夹具仅在明确的本地源库特权恢复夹具中构建，恢复全部保护后才进入运行账号验证；没有提供运行时删除、改写或补造历史依据的产品接口。

## 验收结果

| 验收 | 结果 |
| --- | --- |
| 受限 PG16 动态、原生 SQL、并发及既有回归门禁 | 90 passed，0 skipped |
| 受限 PG17 同组门禁 | 90 passed，0 skipped |
| 完整恢复入口：本地回归 | 151 passed，0 skipped |
| 完整恢复入口：双版本数据库、原件、身份、权限、业务联合恢复 | 2 passed，0 skipped |
| 完整恢复入口：真实本地 GoTrue 密码、TOTP、撤权及恢复后 API | 4 passed，0 skipped |
| 额外复核政策、权限、月结和迁移结构本地回归 | 88 passed，0 skipped |
| 前端 | 108 files / 1124 passed；lint、typecheck 通过 |
| 构建预览下桌面 1440 与手机 390 浏览器 | 8 passed；新补核与既有权限路径均验证 |
| 本轮 Python / TSX / CSS 格式与静态检查 | 通过 |

以上测试集合有重叠，不将它们相加宣称独立覆盖数量。PG 新增专项包括原生 SQL 伪造身份/账套/日期、自审、无旧审核凭据、并发确认/导出、等待锁时撤权、完成后撤权，以及手工/期末凭证的真实记账。旧稿已导出后的补核还验证原冻结导出与正式凭证幂等。

主要本机证据（均为忽略目录，私有配置、数据库备份、身份材料及原件不得提交）：

- `tmp_test/legacy-review-release-pg/summary.json`
- `tmp_test/legacy-review-release-recovery/reports/summary.json`
- `tmp_test/legacy-review-release-frontend-native/summary.json`
- `tmp_test/legacy-review-release-local.xml`
- `tmp_test/legacy-review-pending-1440.png`、`tmp_test/legacy-review-pending-390.png`

## 红—绿与协作记录

最初 3 条失败分别固定了补核服务不存在、旧稿无当前复核出口及恢复清单漏权限表；随后补充的独立命令行失败测试复现共享模块导入错误，已修复并纳入 151 项门禁。未放宽历史审核、角色、期间、版本或费用/attempt 防重发规则。

原有权限修改在工作开始时未提交；并行任务随后形成 `0c06a38c`，本轮相关文件又由并行任务提交为 `c64fd052`、`c127f309`。未回退、覆盖或改写这些提交。本任务没有执行提交、推送、远端配置变更或部署。候选变化期间的旧运行保留，但不算最终候选通过。

Python 子进程编排前端时曾出现 Windows SWC 原生绑定加载失败；未修改依赖或跳过失败用例，最终使用原生 PowerShell 同候选完整执行全部前端测试及浏览器流程。该失败日志也保留。

验收结束已停止本轮六个本地 PG 测试实例及桥，保留私有配置和恢复证据，没有删库。远端 CI、云对象存储厂商恢复和正式上线均未在本轮验证。
