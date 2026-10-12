# PostgreSQL 与 Supabase 数据库接入

第一阶段的 SQLite 演示继续使用；第二阶段可将同一导入、审核、导出流程切到 PostgreSQL。会计数据保存在独立 `accounting` schema，浏览器通过后端 API 访问。Supabase Auth 提供登录身份，账套权限保存在本项目的成员及授权表；登录成功不会自动获得客户数据。

## 连接与迁移

安装 `nexus_backend/requirements.txt` 中的依赖。管理员连接和运行连接使用两个独立账号：

| 环境变量 | 用途 |
| --- | --- |
| `ACCOUNTING_ADMIN_DATABASE_URL` | 仅迁移 CLI 读取，使用管理员或 `BYPASSRLS` 账号 |
| `ACCOUNTING_RUNTIME_PASSWORD` | 仅建立运行账号时读取；未设置则隐藏输入 |
| `ACCOUNTING_DATABASE_URL` | 后端运行 DSN，使用受限账号，例如 `accounting_runtime` |

在本地私密环境配置管理员 DSN 后执行：

```powershell
python scripts/migrate_accounting.py status
python scripts/migrate_accounting.py migrate
python scripts/migrate_accounting.py provision-login --username accounting_runtime
```

`provision-login` 从环境或隐藏提示读取密码，不接收密码命令行参数，不覆盖已存在账号。运行账号不拥有 schema、表、迁移函数，不具备超级用户、建库、建角色或绕过 RLS 权限；只可切换到 `accounting_app` 及限定身份资料访问的 `accounting_system` 角色。管理员连接不要进入应用进程的部署环境。

将运行账号的连接字符串保存为 `ACCOUNTING_DATABASE_URL` 后，按 [登录配置](./AUTH_SETUP.md) 启动后端。远端连接启用 TLS，例如 DSN 加 `?sslmode=require`。DSN 中的保留字符须进行 URL 编码；不要把真实 DSN 写进仓库。

应用启动仅核对迁移版本和内容摘要，不会创建 PostgreSQL 表，也不会种入虚构公司。未迁移或使用特权运行账号时启动失败。升级时先备份，再显式执行 `migrate`；已应用的迁移文件不得编辑，新变化追加新版本。

## Supabase 连接方式

从 Supabase 项目的 **Connect** 面板复制实际 host、port、project ref。迁移优先使用直连；IPv4 网络可使用 session pooler。长驻 FastAPI 后端优先直连或 session pooler。共享 pooler 的自定义角色用户名格式为 `accounting_runtime.[PROJECT-REF]`，直连则为 `accounting_runtime`。具体连接模式及 IPv4/IPv6 约束见 [Supabase 官方连接说明](https://supabase.com/docs/guides/database/connecting-to-postgres)。

运行驱动也兼容 transaction pooler：关闭预编译语句（`prepare_threshold=None`），批量写入逐条发送；身份设置、角色及事务级 advisory lock 均在同一事务内生效。共享 transaction pooler 不保留会话状态且不支持查询流水线，见 [官方限制](https://supabase.com/docs/guides/database/connecting-to-postgres#transaction-mode-limitations)。

`accounting` 不应加入 Supabase Data API 的 exposed schemas。迁移撤销 `PUBLIC`、`anon`、`authenticated` 对 schema、表及函数的访问，所有表均启用并强制 RLS；权限函数固定搜索路径并限制执行角色，做法依据 [PostgreSQL SECURITY DEFINER 指南](https://www.postgresql.org/docs/16/sql-createfunction.html#SQL-CREATEFUNCTION-SECURITY)。应用账号及密码只保存在后端。

## 一致性与隔离边界

数据库策略按当前用户、选中的事务所、有效客户关系和账套授权过滤。事务所 owner/admin 可管理事务所账套；其余事务所成员需账套授权；客户企业 owner/admin/finance 还须账套授权。企业 employee 保留未来业务录入身份，不能读取账务来源、凭证或导出。公司部门外键阻止跨企业分配部门。

服务层继续检查编辑、审核、导出等动作，RLS 提供第二道数据隔离。系统身份事务仅能维护 `users`，不能读取流水、凭证、导出。管理员仅用于部署迁移、备份及受控维护。

运行角色只能追加原始来源、补充证据和导出回执，不能更新或删除这些记录。审计记录也只允许追加。客户暂停时账务读取立即停止；事务所管理员仍可清理该客户的人员授权，防止重新启用客户后遗留授权恢复。

金额为整数分，PostgreSQL 用 `BIGINT`，汇总结果仍以整数传回。读事务使用一致快照；MVP 的写事务使用全域事务级 advisory lock，与迁移使用同一锁，保证幂等、审核版本及期间关闭的并发边界。试点后可按账套细化锁和连接池，需继续验证跨表一致性。

SQLite 旧数据自动升级到版本化迁移：每个旧账套建立对应客户企业、保留原 reviewer 与账套授权、原始流水、补证、审核版本、关闭期间、导出回执和审计记录。迁移提交前执行外键检查，失败整体回滚。SQLite 文件与 PostgreSQL 之间的数据搬迁尚未提供自动工具；真实企业验证建议从 PostgreSQL 新建客户、导入来源开始。

## 本地集成测试

仅使用独立测试 PostgreSQL 数据库，配置 `ACCOUNTING_TEST_POSTGRES_URL`（管理员）及 `ACCOUNTING_TEST_DATABASE_URL`（已建立受限运行账号）。执行：

```powershell
python -m pytest nexus_backend/tests/test_postgres_adapter.py nexus_backend/tests/test_postgres_integration.py -q
```

测试迁移当前 schema，并用随机 ID 构造测试资料，不删除数据库或 schema。没有提供测试 DSN 时，真实 PostgreSQL 集成测试明确跳过；SQLite 升级与适配器测试仍可运行。
