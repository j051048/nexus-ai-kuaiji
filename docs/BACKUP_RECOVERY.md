# 数据库与票据原件的备份、恢复演练

`scripts/backup_accounting.py` 生成数据库快照、快照所引用的业务/补件原件、SHA-256 清单和迁移版本清单。恢复只允许明确指定的新目录或空目录；已有数据库、原件及失败演练产物不会被删除或覆写。校验失败时不会写出完成清单。

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

```powershell
python scripts/backup_accounting.py backup --database-env ACCOUNTING_BACKUP_DATABASE_URL --storage-root 'C:\试点原件' --destination 'C:\试点备份\pg-001'
python scripts/backup_accounting.py restore 'C:\试点备份\pg-001' --destination 'C:\恢复演练\pg-001' --postgres-target-env ACCOUNTING_RECOVERY_DATABASE_URL
```

数据库创建、角色授权和部署切换必须另行完成。恢复使用 `--no-owner --no-privileges`，因此 **恢复数据库不直接作为生产服务使用**：先由管理员重新核验对象所有者、SECURITY DEFINER、FORCE RLS、运行角色授权及新运行登录账号。备份管理员不能成为 API 的运行账号。Supabase Auth、Storage 和项目配置另按云服务自己的恢复方式备份，本脚本不操作真实云项目。

## 演练验收

1. 在隔离目录/空库恢复，保持真实模型、财务软件发送及自动 worker 未启动。
2. 核验 `restore-verification.json` 的迁移数和文件数；抽查业务事项、提交快照、会计审批、导出清单、导入回执和原件下载。
3. 检查跨企业拒绝访问、关闭期间拒绝写入、已审核凭证不能改动。
4. 恢复后的 `running` 任务不能视为成功；按原租约恢复规则处理。外发结果未知的模型或财务软件调用先核验回执，不自动重发。
5. 记录开始/完成时间、快照时间、失败项及修复结果，再决定是否达到实际 RPO/RTO。当前脚本不宣称既定恢复时限。

清单哈希用于检测误改和文件损坏，不代替签名或加密。备份包含真实业务资料，应放在受控且加密的存储中，并纳入保留期限管理。

2026-10-12 已演练本机虚构 SQLite 数据与真实附件，并在独立 PostgreSQL 14 集群新建两个仅含合成资料的数据库，实际执行共享快照 `pg_dump` 与新空库 `pg_restore`。Windows Python 调用 WSL PostgreSQL 客户端，使用本脚本的真实快照、清单和恢复校验路径；迁移清单、企业账套、FORCE RLS 标记及原件字节哈希一致，重复恢复到已有数据库被拒绝。恢复结果保持 `serving_enabled=false`。这次演练未启用恢复库对外服务，也未覆盖生产规模、云服务恢复、运行角色重新授权和真实 RPO/RTO；这些仍须按试点部署环境演练。
