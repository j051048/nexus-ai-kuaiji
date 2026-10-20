# P0.1 迁移与发布基线

## 审计纠正

审计称“缺少 migrate status / 依赖未锁 / PG 未进 CI”，与当前仓库不符：

- `scripts/migrate_accounting.py` 已有 `migrate | status`；
- `nexus_backend/requirements.lock` 与 `requirements.runtime.lock` 已存在；
- `.github/workflows/accounting.yml` 已启动 PostgreSQL service、执行迁移、创建受限账号并运行全量后端 pytest；
- 因此不重复建设迁移/CI 平台。

真实缺口是：

1. `status` 把“缺失”和“校验不符”混为一类；
2. CI 迁移后没有严格检查所有版本已应用；
3. 迁移状态没有纯函数测试；
4. 批量部分结果的浏览器证据不足。

## 本轮改动

- 新增 `classify_migrations` 纯函数：
  - 未记录 → 缺失；
  - 记录但 checksum 不同 → 校验不符；
  - 一致 → 已应用。
- `status --strict`：任一缺失/不符返回非零。
- CI 在 `migrate` 后执行 `status --strict`。
- 新增 `test_ag00_migrate_status.py`。
- 新增 `scripts/check_migration_idempotency.py`：
  - 新迁移的 `CREATE FUNCTION` 必须 `OR REPLACE`；
  - `CREATE TRIGGER` 前必须有 `DROP TRIGGER IF EXISTS`；
  - 禁止 `pg_get_functiondef + replace`。
- 新增 `test_ag00_migration_idempotency.py`。
- CI 增加迁移幂等 lint。

## 验证

```text
test_ag00_migrate_status.py: 1 passed
test_ag00_migration_idempotency.py: 3 passed
check_migration_idempotency.py: exit 0（当前无 136+ 迁移）
ruff check scripts/migrate_accounting.py: pass
```

本地未连接 PG，因此未实际执行 `migrate status`；CI 会执行。

## 未完成

- 迁移函数/触发器/策略的幂等重建规范尚未落地；
- 未做真实 PG 迁移演练（本机无 PostgreSQL 服务/psql/测试 DSN）；
- 已新增 CI `check_pg_test_target.py`：缺 DSN、迁移表为空、受限角色缺失、运行账号是超级用户时直接失败，避免 PG 套件全 skip 仍绿；
- 未证明线上部署自动迁移；
- 批量部分成功尚未有真实失败项证据（月初批量开期当前 scope 会排除已存在账套）；
- 请求计数已用独立最小 E2E 验证：`p0-request-count.spec.ts` 1 passed；
  - bootstrap 1 次；
  - 首页不请求 workbench/book；
  - 客户账套按需 workbench 1 次；
- 原 `stage20-home-loading` 多测试仍会在启动后无输出，未作为通过证据；
- 401/403/MFA 已形成跨层证据：
  - 后端 401/403：`test_http_auth_origin_and_loopback_boundaries` 1 passed
    （未认证 401、恶意 Origin 403、非 loopback 403）；
  - 浏览器 MFA/403：`stage18-admin` 8 passed；
  - 撤权 403/404：`stage12-inline-review`。

## 批量部分失败

新增真实状态机测试：

```text
test_batch_operations.py: 2 passed
```

覆盖：
- 一户成功、一户失败；
- 逐项 `succeeded` / `failed`；
- completed=2，不再整批显示成功；
- 重试只把失败项重新置为 queued。

同时修复真实缺陷：`BatchWorker` 直接读取 `AccountingError.code`，但该异常没有 `code`，导致失败项落入 `worker_failure`；已改为安全的 `getattr`。

浏览器展示证据（前端 route mock，状态机由后端单测覆盖）：

```text
p0-batch-partial-ui.spec.ts: 1 passed
```

显示：

- 已开期 1 家 · 待人工处理 1 家 · 进度 2/2；
- 两户逐项状态；
- 失败原因可见。

结果页已增加“只重试失败项”按钮：

- 仅在 `failed > 0` 时显示；
- POST `/{batch}/retry`；
- 带独立 idempotency_key；
- E2E 验证 `reason` 与 key 发送，重试后 2 成功、0 失败。

## 已修正的旧测试

- `e2e/ui-navigation.ts` 的 `openAccounting` 优先点击当前一级入口“客户账套”，兼容旧“客户企业”；
- `e2e/monthly-batch.spec.ts` 从旧“客户月度进度”改为当前“交付”，并提高冷启动超时。
