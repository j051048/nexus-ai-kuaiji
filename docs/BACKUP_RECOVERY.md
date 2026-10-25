# 数据库与票据原件的备份、恢复演练

`scripts/backup_accounting.py` 生成数据库快照、快照所引用的业务、补件、未提交录入、银行导入、旧账迁移、月度资料、发票导入及两段式上传原件、SHA-256 清单和迁移版本清单。原件表由 `ORIGINAL_TABLES` 单一登记；新增附件表未登记时，备份测试会失败。恢复只允许明确指定的新目录或空目录；已有数据库、原件及失败演练产物不会被删除或覆写。校验失败时不会写出完成清单。

## 包边界（不要混称“完整系统恢复”）

| 包 | 包含 | 不包含 | 用途 |
|---|---|---|---|
| **accounting 数据包** | `accounting` schema、被引用的私有原件、迁移版本、清单 | 平台身份、共享用户、角色登录、云配置、密钥 | 本产品数据恢复与演练 |
| **月度交付包** | 已复核账期的凭证、账簿、正式报表、对账及原件索引 | 原件字节、完整历史、平台身份 | 已实现月度交付与独立签收；不是完整解约交接包 |
| **企业完整交接包** | 全历史与原件等解约交接范围（待独立实现） | 其他企业与平台身份 | 不能用月度 ZIP 冒充完整交接 |
| **完整系统灾难恢复** | 数据库、原件、身份系统、角色映射、配置、密钥、DNS/证书 | — | 真实生产灾难恢复（需平台配合，B07 演练） |

备份脚本负责 accounting 数据包；完整系统恢复仍需身份、平台配置和密钥的独立恢复方案。`restore-verification.json` 会写明 `serving_enabled=false`、`outbound_disabled=true`、`worker_autostart=false`、`in_flight_jobs_hold=true`，同时写入数据库 `restore_state` hold。API／worker／助手／维护进程启动会检查文件及数据库；权限错误、连接失败或任意未释放 hold 都会拒绝启动。文件不存在不等于数据库已解除隔离，不要靠删除标记绕过核对。

## SQLite 本地试点

PowerShell 示例，环境变量中的数据库路径须明确指定；不要将连接字符串写进命令参数或保存到文档。

```powershell
$env:ACCOUNTING_DATABASE_URL = 'C:\试点测试目录\accounting.db'
python scripts/backup_accounting.py backup --storage-root 'C:\试点测试目录\private' --destination 'C:\试点备份\20261012-001'
python scripts/backup_accounting.py verify 'C:\试点备份\20261012-001'
python scripts/backup_accounting.py restore 'C:\试点备份\20261012-001' --destination 'C:\恢复演练\20261012-001'
```

SQLite 使用官方 backup API，避免复制运行中的 `.db` 文件而漏掉 WAL。附件列表取自备份后的数据库，原件哈希必须与该快照一致；运行期间附件被移除或损坏会使备份明确失败。恢复后执行数据库完整性、外键、迁移清单和全部引用原件的检查。

## PostgreSQL / Supabase 接入底座

需要 Python `psycopg`、与服务器兼容的 `pg_dump` 和 `pg_restore` 客户端。备份账号须具有读取全部需要备份记录的授权；启用 FORCE RLS 的数据库应使用专用备份管理员连接，不能使用普通 API 或 worker 账号。连接通过环境变量提供，脚本不会打印连接、密码或供应商错误输出。

备份在一个 REPEATABLE READ READ ONLY 事务里导出快照，`pg_dump --snapshot` 与附件元数据读取共享该快照。仅备份本产品的 `accounting` schema，不导出集群角色、认证服务、云存储、供应商密钥及其他 schema。

恢复目标需要人工事先建立**独立空数据库**，并设置 `ACCOUNTING_RECOVERY_DATABASE_URL`。脚本检查所有用户 schema 的表和函数，拒绝恢复到已有应用数据库；`pg_restore` 在单一事务里执行，出错回滚。目标须预先具备迁移中引用的 `accounting_app`、`accounting_system`、`accounting_worker` 等 NOLOGIN 角色；脚本不会复制角色登录密码。

`pg_restore` 使用 `--no-acl`。新 PG 备份另外记录四个固定 NOLOGIN 能力角色的 schema、表、序列、函数和列授权，以及 RLS、触发器、约束、索引和函数结构指纹，不含密码。默认仍不恢复授权；管理员明确使用 `--restore-permissions` 时，先比对结构和角色属性，再在事务中按白名单重建精确 ACL 并逐项反查。不复制登录、PUBLIC / anon、转授权能力或 owner；列 SELECT 不得升级整表 SELECT。旧备份没有安全清单时不能使用此选项。恢复后仍保持 hold，管理员另外创建新运行登录和明确角色关系；不能用备份管理员运行 API。

```powershell
python scripts/backup_accounting.py backup --database-env ACCOUNTING_BACKUP_DATABASE_URL --storage-root 'C:\试点原件' --destination 'C:\试点备份\pg-001'
python scripts/backup_accounting.py restore 'C:\试点备份\pg-001' --destination 'C:\恢复演练\pg-001' --postgres-target-env ACCOUNTING_RECOVERY_DATABASE_URL
# 已审查带安全清单的新备份，可在隔离目标显式追加 --restore-permissions
```

数据库创建、角色授权和部署切换必须另行完成。恢复使用 `--no-owner --no-privileges`，因此 **恢复数据库不直接作为生产服务使用**：先由管理员重新核验对象所有者、SECURITY DEFINER、FORCE RLS、运行角色授权及新运行登录账号。备份管理员不能成为 API 的运行账号。Supabase Auth、Storage 和项目配置另按云服务自己的恢复方式备份，本脚本不操作真实云项目。

## Supabase 私有原件的离线备份

Zeabur 多服务 Demo 使用同一个私有 bucket，不再传入本地原件目录。在独立维护终端配置专用备份数据库连接及与服务相同的 `ACCOUNTING_STORAGE_*`；不要把备份管理员连接注入运行服务。执行：

```powershell
python scripts/backup_accounting.py backup --database-env ACCOUNTING_BACKUP_DATABASE_URL --configured-storage --destination 'C:\试点备份\supabase-001'
python scripts/backup_accounting.py verify 'C:\试点备份\supabase-001'
```

此模式只读取 private bucket metadata 和数据库快照引用的对象，逐件校验 SHA-256 / 长度，保存到受控备份目录；不创建 bucket、不签发公共链接、不上传或删除对象。bucket 公开、原件丢失、响应超时或哈希不符时，不写出完成清单。清单仍采用相同格式，不记录项目地址、bucket 密钥或数据库连接。

`restore` 继续把原件还原至新目录 `originals/`，不自动上传到云存储。恢复到新的 Supabase 环境仍需重建 Auth / 数据库角色及 ACL，并将核验过的原件按原存储键放入**新的私有 bucket**，验证后才切换服务。不能把还原到本地目录视为云端原件已恢复。新 Demo 可直接新建客户导入资料；现有 SQLite 业务库迁至 PostgreSQL 的自动搬迁不在此工具范围内。

## 演练验收

1. 在隔离目录/空库恢复，保持真实模型、财务软件发送及自动 worker 未启动。
2. 核验 `restore-verification.json` 的迁移数和文件数；抽查业务事项、提交快照、会计审批、导出清单、导入回执和原件下载。
3. 检查跨企业拒绝访问、关闭期间拒绝写入、已审核凭证不能改动。
4. 恢复后的 `running` 任务不能视为成功；按原租约恢复规则处理。外发结果未知的模型或财务软件调用先核验回执，不自动重发。
5. 记录开始/完成时间、快照时间、失败项及修复结果，再决定是否达到实际 RPO/RTO。当前脚本不宣称既定恢复时限。

逐行导入或自定义云迁移还必须核对自增序列。显式恢复 `id` 不会自动推进 PostgreSQL 序列，读接口正常也不能证明恢复后的写入可用。启用服务前，以管理员只读检查 `accounting` 中每个表拥有的 serial/identity 序列，确认下一值大于表中最大 ID；空表保留原初始状态。标准 `pg_dump` / `pg_restore` 应保留序列状态，自定义行导入需要单独处理。确需修复时，使用专用管理员，在与应用相同的全局写锁保护下重新读取最大 ID 与序列值，只向前校正落后的序列，不回退更高值，不使用 `nextval` 试探，不把修复权限授予运行账号。恢复验收必须包含隔离虚构企业的实际业务写入、审计与审核后导出。

清单哈希用于检测误改和文件损坏，不代替签名或加密。备份包含真实业务资料，应放在受控且加密的存储中，并纳入保留期限管理。

## 本机隔离释放工具

`scripts/release_recovery_hold.py` 仅接受显式的回环地址、非 5432、`ak01_local_ak05_` 前缀的测试 PG 数据库。它不是生产切换工具。管理员必须先完成数据库、原件、身份、权限、业务和未知外发六项独立核对，并提供当时 `pending_inventory` 的指纹；核对后在途任务、费用或外部回执发生变化，须重新检查。

释放时再次比对恢复 ID、精确授权／结构、全部 hold 和在途清单。在同一管理员事务中写入不可改删的 `recovery_release_audit` 后释放数据库 hold，再把文件标记改名为 `restore-released.json`。文件改名失败仍阻断正常启动，保留审计供排查。工具不修改任务、费用、未知回执，不启动 worker，也不启用模型或财务软件外发。生产恢复需要独立运行手册与授权，不能复用本机释放开关。

恢复会以只读方式核验所有 accounting 自增／identity 序列的下一值高于当前数据，禁止用 `nextval` 试探或自动回退序列。角色重建与原件／身份恢复的本轮本机实证见 [C01 收口记录](C01_LOCAL_CLOSURE_20261025.md)，供应商协议替身结果不能冒充 Supabase 云平台灾难恢复。

2026-10-12 已演练本机虚构 SQLite 数据与真实附件，并在独立 PostgreSQL 14 集群新建两个仅含合成资料的数据库，实际执行共享快照 `pg_dump` 与新空库 `pg_restore`。Windows Python 调用 WSL PostgreSQL 客户端，使用本脚本的真实快照、清单和恢复校验路径；迁移清单、企业账套、FORCE RLS 标记及原件字节哈希一致，重复恢复到已有数据库被拒绝。恢复结果保持 `serving_enabled=false`。这次演练未启用恢复库对外服务，也未覆盖生产规模、云服务恢复、运行角色重新授权和真实 RPO/RTO；这些仍须按试点部署环境演练。

2026-10-13 第十三阶段演练还原 65 个迁移版本、8 条历史记录及两份旧账原件。先验证恢复后运行账号无法启动，再审查并重建三个内部 NOLOGIN 角色的对象与列 ACL，撤销 PUBLIC 默认权限，不复制旧登录、owner 或转授权能力。使用实际受限账号验证原件 SHA、接管只读及无历史授权不可读取，任务列权限未提升为整表读取，仍保持 `serving_enabled=false`。具体结果见 [第十三阶段记录](VALIDATION_STAGE13_20261013.md)。
