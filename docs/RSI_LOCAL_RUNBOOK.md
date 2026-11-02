# RSI 本地使用与复跑

这是离线实验工具，不是自动学习发布服务。RSI01/02 不加载业务数据库；RSI03 仅允许显式的本地隔离库服务测试。没有新增生产路由、worker、数据库迁移或前端入口。

## 快照和比较

在仓库根目录用 PowerShell 执行。每次用新的运行目录；验收数据在同一候选 store 中使用后，不能再次用于调参选优。

```powershell
$python = '.venv/Scripts/python.exe'
$store = 'tmp_test/rsi/my-experiment/candidates'
$fixtures = 'nexus_backend/tests/fixtures/rsi'
$base = (& $python -m tools.ai_inference_lab.rsi --store $store snapshot --resources "$fixtures/resources/baseline" --reason '合成基线' | ConvertFrom-Json).id
$bad = (& $python -m tools.ai_inference_lab.rsi --store $store snapshot --resources "$fixtures/resources/bad" --parent $base --reason '含关键退步的候选' | ConvertFrom-Json).id
$safe = (& $python -m tools.ai_inference_lab.rsi --store $store snapshot --resources "$fixtures/resources/safe" --parent $base --reason '保留票号的候选' | ConvertFrom-Json).id
& $python -m tools.ai_inference_lab.rsi --store $store diff $base $safe
& $python -m tools.ai_inference_lab.rsi --store $store freeze --manifest "$fixtures/manifest.json" --baseline $base --candidate $bad --candidate $safe --out tmp_test/rsi/my-experiment/run
& $python -m tools.ai_inference_lab.rsi --store $store run --out tmp_test/rsi/my-experiment/run
```

每条命令检查退出码，失败后不要继续流水执行。`freeze` 不覆盖旧运行；`run` 可以恢复同一未漂移候选。结果在 `report.json`、`report.md`，选优凭据在 `selection.json`，逐次预留/结果在 `attempts/`。

执行器只收到 H01 ExtractionInput。开发集初筛和全量比较完成，选优凭据写入后，评分器才读取验收集；不能用验收结果重新选择。报告使用 AG05 的固定分母及关键退步否决。

示例是已知合成数据，不是未见真实模型评测。只改 prompt 而 mock 行为不变不会产生改善结论。费用未知保留 null；`accepted_offline` 不等于允许发布。

归档模式给 `freeze` 加 `--archives <本地目录> --recorded-model <归档模型标识>`。文件名为 `<screen|dev|validation>-<candidate_id>.json`，外层字段只能是 mode、model、results。每条结果必须已有完整候选、请求、样本、manifest 和企业身份绑定；不允许运行器重写来源后冒充匹配归档。具体契约和可运行例子见 `test_bound_synthetic_archives_do_not_call_extractor`。

## 已审核修正形成规则候选

默认 CLI 故意没有规则写库命令。专用隔离测试使用 `LocalRules`：

1. 通过正常业务服务走提交、部门审批、财务核验、制单和另一位会计复核，取得现有 LearningService 自动捕获的样例和规则。
2. `prepare(actor, book_id, source_rule_id, positive_ids, negative_ids)` 返回候选证据对象，不新增规则。可用 RSI01 `atomic_create(path, canonical(artifact))` 保存本地不可覆盖证据。
3. 本地明确选择后调用 `stage(actor, book_id, artifact)`，只保存或返回精确匹配的草稿。
4. `evaluate(actor, book_id, artifact, rule_id)` 复用既有评测，仍为 draft。
5. `shadow(..., current_amount='120.00')` 检查历史 100 元处理方式不会覆盖当前 120 元。这是合成影子试算，不是向生产业务写分录。

每次 stage/evaluate 都在当前授权事务中重取审核凭据、来源、主数据、科目和政策。离线文件不构成权限或历史审核证据。仅支持普通办公采购、双分录 gross 模板；核销、银行清偿、复杂税务和迁移样例拒绝。没有 publish/retire/apply 入口。

## 持续验收命令

纯离线与 SQLite：

```powershell
.venv/Scripts/python.exe scripts/test_accounting_offline.py nexus_backend/tests/test_rsi_snapshots.py nexus_backend/tests/test_rsi_experiment.py nexus_backend/tests/test_rsi_rules.py -q
```

完整组合验收，需要已准备好的专用本地 PG16/17 配置，沿用 `test_accounting_postgres_isolated.py` 的 admin/app/worker 配置格式；不得填写生产连接：

```powershell
.venv/Scripts/python.exe -m tools.ai_inference_lab.rsi.acceptance --pg16-config <私有PG16配置文件> --pg17-config <私有PG17配置文件> --out tmp_test/rsi/new-acceptance-run
```

门禁不建库、不改角色、不发布。它检查本机地址、专用数据库名称、实际主版本、运行身份无 super/BYPASSRLS、持久化设置开启；串行运行离线和两个 PG 版本，记录 JUnit、日志、完整依赖摘要及 HEAD。任何失败、错误、跳过或相关依赖漂移都会使整体退出码非零。

`rsi_contracts_passed` 只是新增 RSI 契约子集；整体结果仍看顶层 `passed`，不能用子集绿色覆盖旧领域回归的真实失败。配置内容和数据库密码不写入公开报告。环境建立/停止仍使用本机已有隔离环境工具，本命令不擅自管理其他任务的 PostgreSQL 进程。

## 恢复边界

取消终态不重新执行，发送后 unknown 不重发。崩溃留下 reservation 但没有结果时，耗时未知，恢复不继续消耗新预算；保留 unknown/missing 并输出 incomplete。JSON 已完成但 Markdown 写入中断时，可以不执行提取而修复 Markdown。配置、输入、共享依赖或结果漂移则拒绝恢复。

文件 hash 不是签名，白名单不是操作系统沙箱。对有本地任意文件写权限的操作者没有恶意隔离保证；可信运行器及输入目录是前提。实验目录是验收证据，不替代生产备份与完整恢复演练。
