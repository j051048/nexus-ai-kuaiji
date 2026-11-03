# NEXT01 验收记录：统一候选、验收入口与测试资源管理

- 状态：**本地验收通过**（工程状态）；外部验收：不适用。
- 有效证据：`tmp_test/next/next01-acceptance-20261103-final/manifest.json`（最终候选重跑）
- 候选：HEAD `df3fface95d1f69c28e859e89015465955c517b4`；`source_files=1816`；`source_sha256=dddef1a88eee0bfdc2c2db37d09560dc0290b434fe800993c05b52b7ba2a963e`；`migration_files=325`；`migration_sha256=48fc7b47abd864e39ec6701939dd42f0bb65c1bc16732bcff2c6686350a0c8e1`。
- 运行前后候选一致（`candidate.stable=true`），`verify` 返回 `valid=true`。
- 命令：`.venv/Scripts/python.exe scripts/next_harness.py run --task next01 --run-dir tmp_test/next/next01-acceptance-20261103-final`，退出码 0。
- 阶段结果：`harness-self-check` 14 passed／33.16s；`skip-guard` 4 passed／2.07s；`recovery-environment` 3 passed；全部 0 failure／0 skip，清理 `passed`。

## 红测与修复

| 步骤 | 证据 | 结论 |
| --- | --- | --- |
| 先写测试 | `tmp_test/next/next01-red/import-error.txt`：`ImportError: cannot import name 'next_harness' from 'scripts'`（exit 4，收集阶段） | 产品代码缺失，非夹具错误；计入红测 |
| 实现 harness | `scripts/next_harness.py` | 红转绿 |
| 追加 xfail／收集错误覆盖 | 14 项全部通过 | 防止假绿 |

第一次验收 `next01-acceptance-20261102`（候选 `2c4dbe8c…`）因随后新增两项测试而使候选变化，按规则作废，仅保留历史；当前证据只认 r2。

## 八项必验对照

| # | 必验 | 测试／机制 | 结果 |
| --- | --- | --- | --- |
| 1 | 正常完成释放连接、线程和临时资源 | `test_run_binds_candidate_and_keeps_evidence`；阶段子进程全部 `wait`，登记临时目录在清理中移除；manifest `cleanup.removed` | 通过 |
| 2 | 失败仍执行收尾并保留规定证据 | `test_zero_cases_is_failure_not_green`、`test_skip_is_failure`、`test_xfail_is_failure`、`test_collection_error_is_failure`；失败时仍执行 `_cleanup`，`evidence/*.log` 与 `*.xml` 保留 | 通过 |
| 3 | 初始化中途失败不遗留不可识别资源 | `test_initialisation_failure_leaves_no_directory`：标记写入失败时删除空 run 目录 | 通过 |
| 4 | 清理被拒绝／占用时结果明确可见 | `test_cleanup_refusal_is_visible`：manifest `status=failed_cleanup`，`cleanup.failures[0].code=cleanup_denied`，原因含 “in use”，目录保留 | 通过 |
| 5 | 强杀后下次识别并安全处置本任务遗留 | `test_force_killed_run_is_detected_and_recovered`：真实子进程 `hold` 被 kill 后 `status=stale`，`recover` 仅删 `scratch`，写 `recovery.json` | 通过 |
| 6 | 无权限操作其他任务目录、数据库或进程 | `test_recover_refuses_alive_or_foreign`：活动 run 拒绝（`run_active`），无标记目录拒绝（`not_owned`）且文件保留；数据库／端口／进程只登记 `untouched`，不自动处置；`test_paths_outside_harness_root_are_refused` 覆盖越界与 `..` | 通过 |
| 7 | 候选／配置变化使旧证据失效 | `test_candidate_change_during_run_invalidates_result`（`candidate_changed`）；`test_config_change_invalidates_verification`（`config_changed`）；`verify` 同时校验证据哈希与归属 token | 通过 |
| 8 | skip、xfail、收集阶段跳过、零用例不能假绿 | 上述四项测试 + `scripts.qa_no_skip` 插件 + harness 自身 JUnit 计数校验（0 用例／skipped／failures／errors 均失败） | 通过 |

## 资源与边界

- 运行目录均在 `tmp_test/next/` 下；`recover` 只接受带合法 `ownership.json` 且为 `tmp_test/next` 直接子目录的目标。
- 未连接任何数据库、端口或外部服务；未触碰 `p02pg16/p02pg17` 或其他任务证据目录。
- `test_force_killed_run_is_detected_and_recovered` 使用真实进程强杀，不是模拟。

## 剩余问题

- NEXT01 只治理“测试资源”，不替代后续 PG 集群生命周期；真实 PG 资源在 NEXT02 起单独登记。
- 清理被占用时的自动重试未实现，按设计转人工（manifest 可见、`recover` 可重跑），符合“结果明确可见”要求。
