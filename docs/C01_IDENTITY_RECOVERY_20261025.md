# C01 本地身份、私有原件与数据库联合恢复

本文件只记录本地虚构数据验证。没有访问 Supabase、Zeabur、Vercel 或真实模型。没有提交凭据、原件、身份私钥或备份产物。

## 身份协议契约测试

`nexus_backend/tests/c01_identity_fixture.py` 是测试专用协议服务，不是 GoTrue 实现，也不能证明 Supabase 云端身份恢复。它在独立私有目录持久化 UUID、随机盐和 PBKDF2 密码哈希、邮件确认状态、ES256 私钥、TOTP、会话撤销记录。登录必须实际校验密码；MFA 必须实际校验 TOTP；后端使用现有 `SupabaseAuthenticator` 验签、校验 issuer/audience/UUID，并在每次认证调用服务的 `/user`。没有替换为固定 `Identity` 或伪造可信 claims。

十项独立测试已执行：10 passed。首次缺少模块/联合验证函数的红测、私有存储 apikey 协议错误红测后修复，均保留在本轮执行记录。测试覆盖错误密码、未确认用户、伪造角色/AAL、失效会话、JWT 过期/错误 issuer、私钥及身份数据库独立目录恢复、身份包篡改、已有目标保护、原件授权与重传、联合清单缺身份或账务清单篡改。

身份包单独包含敏感材料，须使用 NTFS 仅当前用户 ACL 或 POSIX 0600 文件/0700 目录。业务 manifest 的 `contains_credentials=false` 不适用于身份包。合成测试密码仅用于本地 fixture；备份没有明文密码。

## 联合恢复验收边界

`test_c01_full_recovery_postgres.py` 要求显式 `C01_FULL_RECOVERY_ACCEPT=1`。只接受 `.codex/c01-final-pg` 中明确配置的 127.0.0.1、非默认端口、独立前缀源/目标；凭据不写入 argv、日志或报告。真正调用官方 PostgreSQL 工具，而非模拟 dump/restore。失败后的数据库保留，不通过删历史或覆盖目标让下一次验收通过。

联合清单绑定身份包 SHA-256、账务 manifest SHA-256、活跃用户 UUID 集合和原件 hash。缺少任意活跃账务身份时，必须在恢复写入前阻断。本轮使用已停止业务写入的 fixture，`quiesced=true`；不声称数据库与身份服务跨系统在线原子快照。

恢复后先保持数据库与文件 hold，验证原件、身份、权限、在途清单及受限业务服务，再通过受控工具写审计并释放本地核验环境。正常 API 在 hold 时必须拒绝启动。释放后重新密码登录，完成原件下载、跨企业负向、新业务写入、独立复核、拒绝未复核导出、复核后导出、撤权及员工开户后登录。未知费用与发送后未知 attempt 保留，不能自动重发。

### 完整 PG16/17 联合恢复结果

两条图均真实执行并通过；身份/私有对象仍明确为上述协议服务，PostgreSQL、dump/restore、受限连接、授权、RLS、业务服务及 API 均为真实实现。源与目标属于不同 `initdb` 集群，目标使用新管理员与不同密码的全新受限登录，不共享源集群角色目录。

| 项目 | PostgreSQL17 | PostgreSQL16 |
| --- | --- | --- |
| 实际版本 | 17.11 | 16.15 |
| 命令结果 | 1 passed, 1 deselected | 1 passed, 1 deselected |
| pytest 用时 | 34.86秒 | 35.04秒 |
| 迁移数量/最大版本 | 149 / 161 | 149 / 161 |
| accounting表 | 239 | 239 |
| 精确能力授权/其中列授权 | 848 / 49 | 848 / 49 |
| 快照身份/原件数 | 5 / 2 | 5 / 2 |
| 只读序列核验 | 2，不调用nextval | 2，不调用nextval |
| 本地恢复开始至业务核验 | 12.907秒 | 14.901秒 |

时长含本地 fixture 及 HTTP 验证开销，不代表生产 RTO/SLA。每条实际完成：五个密码身份、owner真实TOTP、两企业/账套、员工原件、独立会计复核导出、一次发送后断连及未知费用、另一个待执行任务、双manifest绑定、全新目标恢复、正常API在hold期间拒绝启动、hold内重新登录/原件及受限业务服务核验、全部角色/RLS/列授权恢复、细粒度列查询成功而敏感整表查询失败、原生跨企业读取拒绝、审计UPDATE拒绝、本地审计释放hold、目标API重新登录/原件授权下载/新业务写入/拒绝未审导出/独立复核后导出/撤权/企业员工密码开户及登录。

复验后 OCR任务、attempt、费用、外部发送和交付回执清单保持原值；未自动重发或冲销未知费用。身份包与原件及私有连接只保存在仓库忽略的本机验收目录。安全汇总证据：

- PG17：父仓库 `tmp_test/c01-frozen-full-recovery-pg17/test_fresh_cluster_database_ob0/pg17-full-recovery-evidence.json`。
- PG16：父仓库 `tmp_test/c01-frozen-full-recovery-pg16/test_fresh_cluster_database_ob0/pg16-full-recovery-evidence.json`。

以上为最终冻结候选重新使用全新源/新空目标完成的两条图，不复用前轮已恢复数据库冒充新结果。525个运行时Python、全部SQL迁移及backup/security/release/migrate脚本前后逐文件哈希完全一致，聚合SHA-256为 `68dd96c92d34049269f66b420ad356b9c6877a71ab7192c8e5f4cd88612ec909`；冻结测试文件SHA-256为 `689c97004f56105243caef795aed7a2308af71ef2593b3108b6e783609c8369b`。忽略目录中保留 `fullgraph-frozen-candidate-before.json` 与 `fullgraph-frozen-final-acceptance.json` 的安全摘要。最后复验仅更新私有连接指向与报告，未修改源码、重启任何服务、覆盖旧库或旧角色密码。

独立真实 GoTrue 进程及 auth schema 恢复由本轮另一个验收文件记录，不将本文件的协议替身结论外推为真实供应商恢复。尚不能据此宣布生产发布门槛全部满足。

### 首轮实际发现与红测

PG17 首轮已通过真实认证、员工原件上传、两企业/两账套授权、流水导入、独立会计复核后导出；随后构造发送后断连的一次 attempt/未知费用和另一待处理任务，停止写入并运行真实快照 dump。全新独立集群已实际 `pg_restore`。

验收真实发现：`Database.assert_restore_hold` 原先裸 `with self.connect()` 对 PostgreSQL 包装连接并不支持，并且缺少事务角色与 search_path 设置，导致正常受限 API 启动失败。主任务负责人已按失败测试修复。之后恢复遇到 CHECK 约束的 PostgreSQL 解析规范化差异（嵌套 AND 在 dump/restore 后平坦化）而正确阻断授权重建；不能忽略约束或放宽指纹，须修正安全规范化再用全新源/目标重跑。目前不将该中途运行计作完整验收通过。

## F12–F14 最新证据映射

| 项目 | 当前可引用证据 | 仍缺的证据 |
| --- | --- | --- |
| F12 intake stale | 本轮新增真实本地API+持久化stale状态的桌面/手机故障契约通过 | 浏览器没有推进真实供应商或系统时钟；不能称真实worker崩溃演练 |
| F13 intake running 恢复 | 本轮新增真实本地API+持久化running状态刷新/重开通过；AK05真实PG进程恢复另有证据 | 浏览器没有重启worker；两组证据不能合称同一真实供应商端到端故障演练 |
| F14 原件 5xx | `e2e/v03-entry-review.spec.ts` 的“原件 503 明确提示失败，不自动重发审核或导出，也不闪回登录”；包括桌面/手机截图 | 不等于真实对象存储供应商故障演练 |

最近一次已留存的核心浏览器验收包含 F14：`test-reports/e2e-core/c8d843cc-2454-4feb-9fe1-ad32e3325bfc/reports/summary.json`，dev/preview 各102项在其同一候选通过；不将这次既有结果冒充本轮C01后端新增迁移的重新浏览器验收。这仍是本地 SQLite/虚构身份边界，不代表 PostgreSQL、云端、真实企业验收。旧 C01 文档把 F12–F14 全部标“未执行”已不准确；应分别记录。

### 本轮 F12/F13 浏览器补验

新增 `e2e/c01-intake-recovery.spec.ts` 与 `nexus_backend/tests/c01_prepare_intake_browser.py`，未修改原有E2E、产品组件、权限或状态机。沿用V10默认dev/preview隔离配置，每轮新SQLite和原件目录；不使用route mock。仅测试prepare脚本可在带随机nonce标记、当前员工、唯一V10隔离草稿中准备合成故障状态；不接受数据库URL或文件路径，不能访问PostgreSQL，也不会删除约束、触发器或历史。

先写浏览器失败测试（准备脚本不存在，1 failed）；新增辅助后发现夹具错误（将最新故障记录倒退到旧成功建议之前，2 failed/2 passed）。改为明确的最新持久化合成stale/running记录，没有修改生产代码。真实API先创建草稿/上传虚构XML/本地解析成功，保留旧0.30元建议；浏览器加载最新stale/running时不展示采用按钮，不覆盖人工金额。

最终相同测试文件：dev **4 passed**（约1.1分钟），preview **4 passed**（51.6秒），每种模式各1440桌面、375手机两种故障。F12验证原件保留、latest run/result为空、不自动识别或提交，人工填64.20并明确勾选后仅提交一次，业务仍处于待审批；F13验证运行态手工改22.20保存、刷新后重新登录同一员工并重开草稿，run ID和进度不变、没有追加recognize/complete，重新核对前提交保持禁用。

验收截图在 `test-reports/c01-intake-final-dev/` 与 `test-reports/c01-intake-final-preview/`，均含 `f12-1440-manual-recovery.png`、`f12-375-manual-recovery.png`、`f13-1440-running-restored.png`、`f13-375-running-restored.png`。已实际查看桌面/手机截图；状态、下一步、人工金额可读，无整页横向溢出。显式e2e TypeScript检查、应用TypeScript、辅助脚本Ruff/语法检查通过。新增四项未由本子任务改入原102项核心清单，是否纳入最终统一清单由主任务维护。
