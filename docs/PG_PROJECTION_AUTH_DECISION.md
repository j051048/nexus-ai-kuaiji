# PG 投影授权：138 实施结果

## 结论

真实 PostgreSQL 14.24 全新库验证通过。

```text
138_projection_identity.sql
migrate: 135 文件全量应用
status --strict: 通过
```

## 架构

- `accounting_projection` NOLOGIN，独立投影身份；
- `accounting_projection_ci` LOGIN，仅 IN ROLE projection；
- `accounting_app` / `accounting_system` 均不是 projection 成员；
- system 对业务源表的 SELECT 被撤销；
- projection 只读源表、只写投影表；
- API 维护进程使用 `ACCOUNTING_PROJECTION_DATABASE_URL`；
- `Database(default_role="accounting_projection")`。

## 测试

4 个代表 PG 套件：

```text
test_postgres_integration.py
test_business_postgres.py
test_admin_workspace_postgres.py
test_intelligence_workflow_postgres.py

80 passed, 0 failed
```

原冲突测试已改为：

- system 读 sources 必须 InsufficientPrivilege；
- system 读 vouchers 必须 InsufficientPrivilege；
- 业务隔离、禁用账号、撤权即时生效继续通过；
- workspace 语句数 2 继续通过。

## 未宣布

- 全部 86 个 PG 文件；
- 生产多 worker；
- 线上迁移/回滚。

临时库和测试角色已清理。
