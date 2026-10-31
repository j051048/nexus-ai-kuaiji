# P01 可重复联合恢复门禁（R2）

只用于本地或 CI 隔离环境。不会连接云端、调用真实模型、发送真实通知、修改产品部署或删除旧库。旧版 `.codex/c01-final-pg` 环境和历史验收文件保持原样；v2 不接管旧集群。

## 环境与版本前提

- PostgreSQL 16、17 官方二进制同时存在，默认根目录 `/usr/lib/postgresql`，可通过 `--pg-bin-root` 指定。
- 运行完整门禁的 Python 使用项目依赖环境（Python 3.11+）；只运行集群/桥生命周期时可用 WSL 自带 Python 3.10。
- 源库必须应用**当前候选所有迁移且校验和匹配**；最低契约版本为 190（173 为税务来源契约；181–187 为权限与复核依据；188–190 为当前版本补核及写入围栏）。旧于该版本先显式升级隔离源库，不能删除恢复清单字段以求通过。
- 四个独立集群固定在 `127.0.0.1:25496–25499`；桥 25510、本地身份服务 25511/25512。占用时拒绝，不停止或接管别人的实例。
- `fsync/synchronous_commit/full_page_writes=on`；登录角色保持 NOINHERIT、非超级用户、无 BYPASSRLS。源库和恢复空库各自独立，目标库仅有必要角色，不预建业务表。
- 本地 GoTrue 固定 v2.197.0，二进制 SHA-256：`f2aa147268461f74784ddc8b2240aa446ee71ceb9633bac41c30e7b852a860aa`。迁移取官方提交 `4eee58f296d9698a1c2c0ae14d7a0b379c7622d3`；目录通过 `--gotrue-root` 指定，内含 `auth` 和 `migrations/`。本地工具不自动下载；CI 从固定官方资产下载并核对归档与二进制哈希。

## 不可变目录和证据

`prepare` 每次创建新的环境 ID。`new-pair` 一次创建 PG16/17 两对新数据库及 `runs/<UUID>/`，不接受指定旧 run 覆盖。四份数据库配置、桥配置、身份迁移清单均为独占创建。重复建库验证并复用原受限登录角色，不改变其密码或权限。

门禁另保存一份私有配置快照，在每阶段前后核对原配置、快照及实际运行桥的哈希。每个 run 只能领取一次；失败重跑也必须 `new-pair`，保留失败证据。`gate-claim.json` 留存领取记录，`gate-active.json` 在正常退出时移除。检测到 active 文件时集群启停拒绝；进程异常退出留下 active 时先核对原进程及私有日志，不提供自动强行清锁或清库。

私有目录存有测试口令、数据库备份、身份和原件，禁止输出或提交。Linux 用 0700/0600；Windows 用当前用户 ACL。可对外保存的只有门禁 `reports/summary.json`；CI 上传路径严格限于 `reports/*.json`，不上传日志、配置、JUnit、dump 或原件。

## Linux 本地或 CI

在仓库根目录，使用项目依赖 Python。下方目录名每轮换新；已存在会拒绝。

```bash
python scripts/p01_local_recovery_env.py prepare --directory tmp_test/p01-env-new \
  --runtime-base "$HOME/.local/share/nexus-local-tests" \
  --gotrue-root "$HOME/.local/share/nexus-local-tests/gotrue-v2.197.0"
python scripts/p01_local_recovery_env.py bootstrap --directory tmp_test/p01-env-new
python scripts/p01_local_recovery_env.py new-pair --directory tmp_test/p01-env-new > tmp_test/p01-run-location.json
P01_RUN_DIRECTORY="$(python -c 'import json; print(json.load(open("tmp_test/p01-run-location.json"))["run_directory"])')"
python scripts/p01_local_recovery_env.py bridge-start --directory "$P01_RUN_DIRECTORY"
python scripts/p01_recovery_gate.py --all --config-dir "$P01_RUN_DIRECTORY" \
  --out tmp_test/p01-acceptance-new --phase-timeout 3600
```

任一命令失败后停止后续验收，保留输出目录。成功或失败均应执行本环境清理停机：

```bash
python scripts/p01_local_recovery_env.py bridge-stop --directory "$P01_RUN_DIRECTORY"
python scripts/p01_local_recovery_env.py stop --directory tmp_test/p01-env-new
python scripts/p01_local_recovery_env.py status --directory tmp_test/p01-env-new
```

`stop` 仅停止核对了环境标记、OS 用户、数据目录、PG 主版本、端口、监听地址及 postmaster PID 命令行的四个实例；不删除数据。`status` 读取当前状态，不用旧报告推断是否停机。恢复使用 `start --directory <原环境>`，再 `new-pair` 和新输出目录；不要再次 bootstrap。

## Windows + WSL

集群及桥在 WSL，建库和完整门禁可使用 Windows `.venv/Scripts/python.exe`。无需修改任何个人目录常量。

1. 在仓库根目录的 PowerShell 取本仓库 Linux 路径：

```powershell
$p01RepoUnix = (wsl -d Ubuntu-22.04 -- wslpath -a (Get-Location).Path).Trim()
wsl -d Ubuntu-22.04 --cd $p01RepoUnix -- python3 scripts/p01_local_recovery_env.py prepare --directory tmp_test/p01-env-new
.venv/Scripts/python.exe -c "from pathlib import Path; from scripts.p01_local_recovery_env import secure; p=Path('tmp_test/p01-env-new'); secure(p); secure(p/'environment-private.json')"
wsl -d Ubuntu-22.04 --cd $p01RepoUnix -- python3 scripts/p01_local_recovery_env.py bootstrap --directory tmp_test/p01-env-new
.venv/Scripts/python.exe scripts/p01_local_recovery_env.py new-pair --directory tmp_test/p01-env-new
```

2. 使用 `new-pair` 输出的 `run_directory`；通过 `wslpath -a` 转成 Linux 路径后，在 WSL 执行 `bridge-start --directory <Linux run路径>`。
3. 在 PowerShell 执行 `.venv/Scripts/python.exe scripts/p01_recovery_gate.py --all --config-dir <Windows run路径> --out tmp_test/p01-acceptance-new --phase-timeout 3600`。
4. 在 WSL 执行 `bridge-stop`、环境 `stop`、`status`，参数与启动时相同。若手动用 Windows Start-Process 启动辅助进程，须加 `-WindowStyle Hidden`。

桥支持同一份配置中的 Windows 路径转换，不要求 Python 或用户家目录名相同。Unix socket 不启用，避免长家目录触发系统 107 字节路径限制；所有数据库连接走受限 loopback TCP。

## 一次命令如何判定通过

`p01_recovery_gate.py --all` 依次执行：

1. 固定的恢复相关单元/回归文件及 R2 负向测试。
2. 两个真实 PG16/17 数据库 + 私有原件协议夹具 + 身份映射 + RLS/列级授权 + hold + 恢复后业务验证。
3. 固定版本真实本地 GoTrue 的密码/TOTP/撤权恢复，以及恢复后的会计 API 业务链。

每一阶段都由 pytest 插件记录**实际收集、setup/call/teardown 结果**。缺失、跳过、xfail、重复、撤选、提前终止、JUnit 计数不符均失败。PG 身份按函数和整数主版本核验，不依赖 `[16]` 参数显示标签。

三阶段都必须成功且绑定同一候选源码/迁移指纹、同一私有配置摘要。`local_regression` 由本次运行自动生成，不能手工补写旧测试结果。原件使用实际文件与哈希及私有对象服务协议夹具，并非已验收任意外部 S3 厂商。

`--phase-timeout` 为每阶段独立超时，30–86400 秒；默认 3600。超时、契约拒绝、环境错误分别返回 `phase_timeout/contract_rejected/environment_failure`，带阶段与下一步。原始敏感日志留在私有运行目录；报告不输出 DSN、密码、令牌或原件内容。

CI：`.github/workflows/p01-recovery.yml` 在 push/PR 执行同一入口并 always 停机。新增 workflow 不等于远端已跑通；未经推送或实际 Actions 证据，不宣称远端 CI 已通过。
