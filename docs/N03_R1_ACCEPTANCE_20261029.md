# N03 通知一致性与恢复验收（2026-10-29）

## 范围与候选

本轮只修改通知权限、收件人和冻结包绑定、通知发送状态机、恢复核验清单，以及对应的本地测试和状态文案。只使用虚构数据与进程内模拟通道；未调用邮件、短信、微信、真实模型或线上数据库，未提交、推送、部署。

初始 HEAD：`bc354080e9b0db41e3d86b4f92ed9c8c11d4a7a5`。期间另一任务提交既有 N02 内容，HEAD 变为 `582cd87671b256dd747da7f5a49831bcfc3948f8`。按初始 1817 个文件 SHA-256 逐项核对，无文件丢失，只有本轮 N03 的 7 个既有文件变化；N02、C01 和其他任务文件未被覆盖。新增文件另记录于最终候选清单。

前向迁移：PostgreSQL 174–178、SQLite 110–114。后续修复使用新编号；没有改写已登记的历史迁移摘要。

## 最终契约

### 权限与收件人

- 入队要求当前账套复核权限；原生 PostgreSQL 写入同样检查当前 actor、企业、账套、期间及复核权。应用不能伪造 worker 的发送状态、attempt 或租约证据。
- 收件人须为有效企业成员，拥有当前账套读取权，是最新服务单联系人，也是冻结包联系人；不能是该包创建人。配置通知授权不会替代这些条件。
- 通知绑定企业、账套、期间、包 ID/版本/manifest、收件人、授权 ID/版本、恢复代次与自身 ID。身份和内容不可修改为另一个收件人或包。
- 入队、领取、开始发送分别重检。撤权、更换联系人/授权、包版本变化、已签收都会取消尚未开始的发送。
- 缺联系人是内部待办，不能携带可外发的包/收件人/授权，也不会调用通道。未知发送结果交给事务所人工核对，不转嫁给客户。
- `status()`、待办列表与发送资格保持一致。无复核权限的员工不能从通知待办读取其他企业信息。

### 事务外发送与结算

1. 短事务领取：`pending/failed → claimed`，生成 worker、租约及唯一令牌，不增加 attempt。
2. 短事务重检并提交：`claimed → sending`，attempt 加一，记录开始时间。
3. 事务外调用模拟通道。
4. 短事务结算：必须同时匹配 sending 状态、令牌、有效租约和恢复代次。

| 结果 | 状态和处理 |
|---|---|
| 有合法回执 | `sink_written`；只代表本地通道记录成功，不等于客户签收或财务软件过账 |
| 适配器明确证明未发送 | `failed`；延迟重试，累计最多 5 次，所有权限/版本仍需重检 |
| 超时、断连、异常、回执缺失/非法 | `unknown`；不自动重发 |
| claimed 到期且未开始发送 | 回到待领取，不增加 attempt |
| sending 到期或进程退出 | `unknown`，不能重新领取 |
| 发送前权限或来源变化 | `cancelled`，不能恢复成 pending |

同一包和收件人存在 claimed/sending/unknown 时，不能靠切换提醒类型、授权或幂等键重发。已排队的另一个提醒也会在领取/开始前被阻断。发送开始后的撤权不能撤回已经进入通道的数据；本轮保证保留该次发送的真实结果或未知记录，不伪装成未发送后重试。

### 恢复与证据

- 文件标记或任一数据库恢复 hold 均阻止新排队/派发。仅删除文件标记不能绕过数据库 hold。
- 恢复代次变化后，旧 pending/failed/claimed 隔离取消，旧 sending 转 unknown。释放 hold 不会自动补发。
- 原进程令牌不能结算恢复副本，即使其原租约还未到期。
- `release_recovery_hold.py` 的在途清单增加通知的身份、版本、授权、attempt、租约和恢复代次；内容/令牌/回执/错误只输出摘要。任何这些证据变化都会使旧释放摘要失效。
- 未将业务表误加到原件注册表；业务表仍由整库备份覆盖。本轮不改 C01 恢复核心。
- 旧 schema 下未确认的待发任务迁移后进入隔离；有不确定发送历史的任务进入 unknown，不自动把历史任务补发出去。

## 红测证据

所有修复先取得本地失败证据，再修改代码；未通过放宽业务断言让测试通过。

- 发送超时后重试、恢复后自动发送、旧代次令牌结算、unknown 换类型重排等已分别复现。
- 当前联系人/账套读取权变更：应用层 queue/claim/begin 六个用例全部先失败；状态投影两个用例先失败。
- SQLite 原生 INSERT：2 failed / 2 passed；原生领取/开始：6 failed / 2 passed，保留正常路径防止全拒。
- PostgreSQL 原生角色、参数边界、取消时篡改 worker 证据、收件人重检及进程故障的红绿记录见 PG 专项报告。
- 旧测试把“换一个 grant 后收件人即有效”和“发送超时可自动重试”视作正确，本轮按冻结包和未知结果的新契约修正了这两项过时断言。另外，已有有效服务单联系人的 g0 通知路径不需要重复配置 grant；对应待办应等待客户签收，不能显示缺联系人。

## 验收结果

**N03 本地工程验收通过。** 测试执行期间的生产源文件保持稳定；最后只修正一条过时的测试预期，并重新跑完对应的两个测试文件。没有将中途失败写成单次全绿。

| 验证 | 真实结果 |
|---|---|
| 本地综合：通知、恢复、C01 安全、月度包、迁移门禁 | 192 passed / 1 failed；唯一失败是已有联系人仍被断言为缺联系人 |
| 修正该旧断言后的完整 service + dispatch 两文件 | **30 passed，0 skipped**，包括上述失败项；没有再改生产代码 |
| SQLite 原生通知专项（包含于综合） | **48 passed** |
| N03 恢复 + C01 安全（包含于综合） | **29 + 22** 项通过 |
| PG16.15：N03 + N02 邻近 | **55 + 45 passed，0 skipped** |
| PG17.11：N03 + N02 邻近 | **55 + 45 passed，0 skipped** |
| PG 两版本从空库迁移到 178，随后再次迁移 | 166 个文件全部摘要一致；重复迁移 `new_applied=[]` |
| 前端状态文案 | **6 passed**；TypeScript、ESLint 通过 |
| 本轮 Python 文件 | Ruff check、format check 通过；`git diff --check` 通过 |

本地核心命令：

```text
.venv/Scripts/python.exe scripts/test_accounting_offline.py
  nexus_backend/tests/test_delivery_followups.py
  nexus_backend/tests/test_n03_dispatch_contract.py
  nexus_backend/tests/test_n03_notifications.py
  nexus_backend/tests/test_n03_recovery.py
  nexus_backend/tests/test_c01_recovery_security.py
  nexus_backend/tests/test_monthly_delivery_package.py
  nexus_backend/tests/test_migration_structure.py
  nexus_backend/tests/test_qs01_migration_integrity.py
  nexus_backend/tests/test_migration_idempotency_scanner.py
  nexus_backend/tests/test_migration_idempotency_ledger.py -q

# 旧断言纠正后，两个受影响文件完整复跑
.venv/Scripts/python.exe scripts/test_accounting_offline.py
  nexus_backend/tests/test_delivery_followups.py
  nexus_backend/tests/test_n03_dispatch_contract.py -q
```

对应证据：`tmp_test/n03-20261029/local-final-v3.xml`、`service-final.xml`、`candidate-final.json`。最后候选清单记录 21 个代码/迁移/测试文件的 SHA-256。PG 源稳定性、运行角色、持久化设置和双进程证据见 [PG 专项报告](N03_PG_ACCEPTANCE_20261029.md)；恢复细节见 [恢复增量报告](N03_RECOVERY_20261029.md)。测试后删除了本任务六个 PG 测试库和测试密码文件，只停止本任务的两个隔离集群。

本地迁移草稿 114 修改期间曾触发测试模板库 checksum 校验，导致一轮 103 个 setup error；该轮作废，没有绕过校验或修改模板中的 checksum，改为冻结文件后新建隔离测试环境重跑。

## 发布边界

本报告只支持本地工程验收。真实通道仍关闭；没有完成真实供应商送达率、反垃圾、外部回执查询或生产容量验证，也没有新增人工解除 unknown 后强制重发的入口。完整 PG + 外部身份服务 + 私有对象存储恢复不以本轮增量测试冒充重新验收。正式上线仍需独立授权、迁移依赖核验及既有 C01 发布门槛。
