# P01-R2：统一恢复门禁、不可变配置、环境生命周期

日期：2026-10-30。结论：**同候选本地验收通过；CI 工作流已接入，远端尚未执行。**

## 范围和归属

- 基线 HEAD：`91d3bc77c94e91a43cdda78d25df747657270923`。
- 开始时已有 `docs/P02_R1_ACCEPTANCE_20261030.md` 修改及对应未跟踪 JSON；本轮均未修改。
- 本轮只改恢复工具、测试基础设施、workflow、回归测试和文档。C01 两处夹具仅增加私有配置目录注入并移除个人目录常量，原有业务断言保留；未改核算、复核、导出业务、SQL 迁移或产品前端。
- 未提交、未推送、未部署；未访问云端数据库、真实模型或通知渠道。

## 实现

1. `scripts/p01_recovery_gate.py --all` 一次运行固定回归、PG16/17 联合恢复和真实本地 GoTrue 身份恢复。pytest 插件记录实际 collection 与 setup/call/teardown；零跳过、零 xfail、禁止缺项/替换/重复/计数不一致。PG 参数按语义整数校验，不依赖显示为 `[16]`。
2. 每阶段前后校验完整候选、原配置、私有配置快照和已运行桥配置哈希。`local_regression` 直接来自当前运行，不能拼接历史通过结果。运行领取为一次性；重复恢复须新建源/空目标库。
3. 环境 `prepare/bootstrap/start/status/stop/new-pair/bridge-start/bridge-stop`；路径可配置、按当前用户推导。仅操作校验归属、版本、地址及 PID 的独立集群，停止不删数据。运行中的 gate 阻止集群启停。
4. 每轮新建 `runs/<UUID>`，禁止覆盖旧配置。登录角色仅在不存在时创建，已有完整角色集先核对权限并实际登录，绝不重置旧口令/权限；不完整角色集拒绝。
5. 最低迁移契约 173，并要求当前候选全部迁移已应用且 checksum 匹配；阶段超时可配置，错误按阶段分类并脱敏。
6. `.github/workflows/p01-recovery.yml` 使用全新 runner、两个 PG 主版本及固定官方身份资产，执行相同 `--all`。always 停止本环境；只上传 `reports/*.json`，配置、dump、身份、原件、私有日志不上传。

## 红—绿证据

新增 `test_p01_r2_gate.py`，最终 **23 passed**。红测先后保存于本地忽略目录：

- `tmp_test/p01-r2-red.xml`：8 项失败，缺统一配置/证据/lifecycle 契约。
- `tmp_test/p01-r2-red-extended.xml`：3 项失败，伪造模块、JUnit 错误计数及重复领取未拒绝。
- `tmp_test/p01-r2-red-socket.xml`：长家目录下 Unix socket 路径限制回归。
- `tmp_test/p01-r2-red-orchestration.xml`：完整入口引用了不存在的迁移状态函数。
- `tmp_test/p01-r2-red-repeat-pair.xml`：重复建库不能安全复用角色；同时真实第二次 new-pair 复现了“账号已存在”的失败。

实际补正后：长路径集群成功启动；start→stop→start 正常；重复 new-pair 成功且旧六份配置摘要不变；门禁运行中实调 stop 返回 `gate_active`，未停库。

## 同一候选完整验收

执行命令（完整门禁一次成功，不手工合并阶段）：

```powershell
.venv/Scripts/python.exe scripts/p01_recovery_gate.py --all `
  --config-dir tmp_test/p01-r2-env-20261030-b/runs/1e8cec4f731c4de493189bd88db54fe1 `
  --out tmp_test/p01-r2-acceptance-20261030-a --phase-timeout 3600
```

| 阶段 | 实际通过 | 跳过 | 耗时 |
|---|---:|---:|---:|
| 固定本地回归 | 143 | 0 | 128.979 秒 |
| PG16/17 联合恢复 | 2 | 0 | 67.266 秒 |
| 真实本地 GoTrue 身份与恢复后的会计 API | 4 | 0 | 54.222 秒 |
| 合计 | **149** | **0** | 250.467 秒（测试阶段） |

候选源码 SHA-256：`2ebddfcd3abc596c69ebd216a75cf1b33acced825a6e4647ef4946b9e06554b2`。
迁移集合 SHA-256：`26e9be85529136507e36a2fe5b25e7a740274d8679cc635302601d596457275a`。

PG16.15 和 PG17.11 各恢复 168 条迁移登记（数量不是最高版本号）、3 份原件、6 个源身份、886 项能力授权、49 项列授权。八项业务检查均为 true：数据库、原件、身份、权限、hold、复核导出、税务与交付、禁止自动重放。身份阶段实际验证密码、TOTP、旧会话失效、撤权及恢复后的业务请求。

机器原始报告按字节复制到 [P01_R2_ACCEPTANCE_20261030.json](P01_R2_ACCEPTANCE_20261030.json)，没有补写人工回归数字。旧配置不变及最终停机状态单独保存于 [P01_R2_ENVIRONMENT_20261030.json](P01_R2_ENVIRONMENT_20261030.json)，不冒充门禁测试阶段。

Ruff check/format 和 diff whitespace 检查通过；workflow YAML 及六段 shell 经解析检查。完成后受控关闭桥和四个集群，数据库、旧配置、失败及成功证据保留。

## 限制与下一步

- 本轮完整运行主机为 Windows Python + WSL PG；CI Linux 原生入口已配置且静态检查通过。遵守“不推送”，**没有远端 GitHub Actions 成功记录**，也未修改 required-check 分支保护。
- 私有原件使用实际文件/hash 与本地私有对象服务协议夹具；不等于验证任意外部 S3 厂商或生产灾备。
- 旧版 `.codex/c01-final-pg` 与旧报告未覆盖；不能用本轮结果追认其他候选。后续代码变更后需新 run 重新验收。
- 本轮不表示 P03–P05、真实企业试点、真实模型或生产发布已完成。操作说明见 [P01_LOCAL_RECOVERY_ENV.md](P01_LOCAL_RECOVERY_ENV.md)。
