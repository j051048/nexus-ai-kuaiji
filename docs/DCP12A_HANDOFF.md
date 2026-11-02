# DCP12-A 交接（2026-11-02 09:35）

## 一、一句话现状

DCP01–DCP11 已在本地完成并提交，DCP12-A（候选冻结与验收）**未通过**：PG17 门禁、恢复演练、DCP11-C 三项为空。生产健康、五服务 RUNNING、数据库台账到 **195**。

## 二、已推送的提交（本轮相关）

```
ed3c633a  DCP12-A: pinpoint migrate failure branch swallowing driver exceptions
fc55e9ab  DCP12-A: record PG17 login enabled but migrate still failing with redacted error
b05b645a  DCP12-A: document PG17 run blocked on non-superuser login provisioning
765df051  DCP12-A: local acceptance report (candidate freeze, evidence, gaps, release plan draft)
```

## 三、PG17 卡点（已逐层定位，未解决）

| 层 | 事实 |
| --- | --- |
| 集群 | WSL `p02pg17` :25532 online，PG 17.11 |
| 网络 | Windows 侧 `127.0.0.1` 不通，必须走 WSL IP `172.24.221.111` |
| 库 | `dcp17_test` 业务表 0 张（从未迁移） |
| 守卫 | `Database._validate_postgres` 拒绝超级用户（`database.py:519`，RuntimeError） |
| 角色 | `accounting_app` 原 `rolcanlogin=f`；已 `ALTER ROLE … LOGIN PASSWORD` 成功 |
| 迁移 | 仍失败，且只回显「数据库操作失败…已隐藏」，退出码 1 |
| 根因位置 | `scripts/migrate_accounting.py:301-309`：`ValueError/RuntimeError` 打原文，其余 `except Exception` 全吞 |

**下一步（二选一）**：

1. 最小修复：该分支保留 `type(error).__name__` 与 `getattr(error, "sqlstate", None)`，其余继续隐藏；然后重跑
   ```
   ACCOUNTING_DATABASE_URL=postgresql://accounting_app:<pw>@172.24.221.111:25532/dcp17_test
   ACCOUNTING_ADMIN_DATABASE_URL=postgresql://postgres:<pw>@172.24.221.111:25532/dcp17_test
   ACCOUNTING_WORKER_PASSWORD=<pw>
   ./.venv/Scripts/python.exe scripts/migrate_accounting.py migrate --username accounting_app
   ```
   注意：**不要用 `| tail` 判断退出码**（会拿到 tail 的 0）。
2. 或跳过 PG17，先做恢复演练（不依赖它）。

## 四、DCP12-A 通过所需的四件事

1. **PG17 跑通**：迁移到代码 head + `n02_n03_postgres_gate.py --major 17` 绿；
2. **恢复演练**：源库 → 备份 → 全新目标库 → 原件 sha256 校验 → 重新登录 → 继续业务，四步都要有记录；
3. **双企业/双期/跨年浏览器链**：至少一条从开账到月度包的完整浏览器证据；
4. **DCP11-C**：任务级回放/H01/AG05/holdout 评测，或明确降级为「未做」并在文档里保留。

## 五、不要做的事

- 不要把 `196_settlement_company_lock.sql` 提交进候选（并行智能体在途，未验证）；若提交，**必须先应用到生产再推送**，否则重演 11-02 08:21 崩溃；
- 不要在候选指纹含未提交字节时宣布 `release` 模式通过（`check_release_candidate.py --mode release` 会直接拒绝）；
- 不要把通知/模型的 unknown 结果自动重发。

## 六、环境速查

```
生产          https://kuaiji.zeabur.app/api/health
Supabase ref  qgzketopnkzquotddwha
PG16          WSL p02pg16 :25531（库 p02_chain、n01_gate）
PG17          WSL p02pg17 :25532（库 dcp17_test，未迁移）
Python        ./.venv/Scripts/python.exe（系统 3.14 与 TestClient 不兼容）
证据目录      artifacts/、test-results/、docs/DCPxx_ACCEPTANCE.md
```

## 七、本会话的已知限制

上下文已接近上限，本会话不宜再承担「需要几十次工具调用」的包（恢复演练、PG17 全量门禁）。建议在新会话继续，交接以本文件为准。
