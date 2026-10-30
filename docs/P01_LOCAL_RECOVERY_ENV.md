# P01 本机恢复环境

该工具只准备本机隔离环境，不代表恢复验收通过，也不访问 Supabase、Zeabur、真实模型或通知服务。

## 已复用的机制

- 使用系统已有 PostgreSQL 16/17 官方二进制，不下载安装服务。
- 四个独立集群固定使用 `127.0.0.1:25496–25499`；占用时拒绝，不接管或停止其他任务。
- `scram-sha-256` 认证；`fsync`、`synchronous_commit`、`full_page_writes` 全开。
- 复用 `migrate_accounting` 的迁移和受限角色创建，以及 AK05 的角色/持久化校验。
- 源库执行当前迁移；目标库只建数据库和角色，不执行业务迁移，保持真正空库以接收恢复。
- `ak05_runtime`、`ak05_worker`、`ak05_projection` 均为 `NOINHERIT`、无超级用户及 BYPASSRLS 权限。
- 凭据仅保存在 `.codex/c01-final-pg` 的当前 Windows 用户专属 ACL 文件中，禁止提交和输出文件内容。
- `new-pair` 为失败后重跑创建一对新数据库；旧库和失败证据保留，不执行 DROP 或清空。

## 首次准备

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe scripts/p01_local_recovery_env.py prepare
```

按输出执行 PowerShell 中的 `wsl ... bootstrap-private ...` 命令，然后：

```powershell
.\.venv\Scripts\python.exe scripts/p01_local_recovery_env.py new-pair 16
.\.venv\Scripts\python.exe scripts/p01_local_recovery_env.py new-pair 17
```

Windows 某些环境中 Python 子进程启动 WSL 会返回 `Wsl/E_ACCESSDENIED`，因此工具有意使用分阶段入口，不把这个主机限制解释成缺少 PostgreSQL。已有私有目录时，`prepare` 拒绝覆盖；重跑用 `new-pair`。

准备后启动已有 `scripts/c01_local_wsl_bridge.py`，只绑定 `127.0.0.1:25510`。配置使用 `.codex/c01-final-pg/wsl-bridge-private.json`，`artifact_roots` 仅允许本仓库 `tmp_test`。若用 Windows `Start-Process`，必须加 `-WindowStyle Hidden`，stdout/stderr 写入该私有目录。

如需 GoTrue 协议验证，工具仅使用本机已存在的固定版本 `gotrue-v2.197.0`；既有 bridge 会检查二进制固定 SHA-256 和 migrations manifest。不存在时应明确报告未验证，不联网下载、不替换成登录成功 stub。

## 本轮环境证据

2026-10-30 已实际准备 PG16.15 与 PG17.11 的两对独立集群及四角色配置。源库全部迁移完成；目标库空表、空自定义 schema、空自定义函数校验通过。bridge 返回 `ready=true`。这仅证明基础设施已可用，联合恢复和恢复后业务结论以 P01 恢复验收报告为准。

先写环境失败测试，初跑因缺少工具模块失败；补工具后：

```text
test_p01_local_recovery_env.py + test_c01_wsl_bridge.py: 8 passed
ruff check / format: passed
```

覆盖四集群缺失、版本/端口错配、端口占用拒绝、非空目标禁止清理、既有桥的路径和命令边界。此处没有把这些单测代替 PostgreSQL 联合恢复验证。

## 停机后安全重启已保留环境

本轮验收后已停止四个集群及恢复桥，数据库和证据仍保留。`prepare` 和
`bootstrap-private` 会拒绝已有目录；不要重新初始化，也不要删除已有目录。
`new-pair` 要求集群已运行，因此应先按下列步骤启动已保留集群，再创建本轮新的
源/目标数据库。下方脚本只读取私有配置的 `root` 字段，不输出连接、密码或令牌。

先在仓库根目录的 PowerShell 执行：

```powershell
@'
from pathlib import Path
import json, os, re, socket, subprocess
private = Path('/mnt/c/Users/Fei/Desktop/AI应用/nexus-ai-kuaiji/.codex/c01-final-pg/environment-private.json')
root = Path(json.loads(private.read_text())['root']).resolve(strict=True)
assert root.parent == Path('/home/flydao/.local/share/nexus-local-tests')
assert root.name.startswith('p01-recovery-') and root.stat().st_uid == os.getuid()
plans = []
for name, major, port in (
    ('source16', 16, 25496), ('target16', 16, 25497),
    ('source17', 17, 25498), ('target17', 17, 25499),
):
    data = (root / name / 'data').resolve(strict=True)
    assert data.is_relative_to(root) and data == root / name / 'data'
    assert data.stat().st_uid == os.getuid()
    assert int((data / 'PG_VERSION').read_text().strip()) == major
    conf = (data / 'postgresql.conf').read_text()
    ports = re.findall(r'^\s*port\s*=\s*(\d+)\s*(?:#.*)?$', conf, re.M)
    assert ports and int(ports[-1]) == port
    addresses = re.findall(r"^\s*listen_addresses\s*=\s*'([^']*)'", conf, re.M)
    assert addresses and addresses[-1] == '127.0.0.1'
    plans.append((major, data))
# 任一端口已被占用就中止，不停止或接管现有进程。
reserved = []
try:
    for port in (25496, 25497, 25498, 25499, 25510, 25511, 25512):
        sock = socket.socket()
        sock.bind(('127.0.0.1', port))
        reserved.append(sock)
finally:
    for sock in reserved:
        sock.close()
for major, data in plans:
    result = subprocess.run(
        [f'/usr/lib/postgresql/{major}/bin/pg_ctl', '-D', str(data),
         '-l', str(data.parent / 'postgres.log'), '-w', 'start'],
        capture_output=True, timeout=60,
    )
    assert result.returncode == 0, '本机集群启动失败；保留私有日志，不接管其他实例'
print('四个已保留 P01 隔离集群已启动')
'@ | wsl -d Ubuntu-22.04 -- python3 -
if ($LASTEXITCODE -ne 0) { throw 'P01 集群启动失败，请检查私有日志，勿继续' }

.\.venv\Scripts\python.exe scripts/p01_local_recovery_env.py new-pair 16
if ($LASTEXITCODE -ne 0) { throw 'PG16 新库准备失败，勿继续' }
.\.venv\Scripts\python.exe scripts/p01_local_recovery_env.py new-pair 17
if ($LASTEXITCODE -ne 0) { throw 'PG17 新库准备失败，勿继续' }
```

确认上面命令全部成功，再启动已配置的恢复桥；为本次启动使用新的日志名：

```powershell
$p01LogTag = [Guid]::NewGuid().ToString('N')
Start-Process wsl.exe -WindowStyle Hidden -ArgumentList @(
  '-d', 'Ubuntu-22.04', '--', 'python3',
  '/mnt/c/Users/Fei/Desktop/AI应用/nexus-ai-kuaiji/scripts/c01_local_wsl_bridge.py',
  '--config',
  '/mnt/c/Users/Fei/Desktop/AI应用/nexus-ai-kuaiji/.codex/c01-final-pg/wsl-bridge-private.json'
) -RedirectStandardOutput ".codex/c01-final-pg/bridge.$p01LogTag.stdout.log" `
  -RedirectStandardError ".codex/c01-final-pg/bridge.$p01LogTag.stderr.log"
```

以上只恢复本机测试基础设施，不启动产品 API/worker，也不解除任何恢复 hold。
联合验收应使用新的 `tmp_test` 输出目录；原有已通过报告不能因重启环境而重新标记日期。
