# AK01 亲自收口与本地验收

日期：2026-10-20。范围：本地虚构资料、离线传输替身和独立本机 PostgreSQL。

## 验收状态

**AK01 本轮明确列出的本地缺口已收口，可以进入 AK02 的本地集成。**

按测试 ID 合并冻结生产候选的组合回归与定向复验：
**690 passed，0 个未解决失败/错误，1 个 SQLite 不适用项**。
该不适用项是 PG 私有冻结函数的 SQLite 参数；对应真实 PG 参数已通过。
这是多套实际执行结果的合并，不声称曾有一条“690 项单次全绿”的命令。

L：本地服务、HTTP、SQLite 进程矩阵通过。
D：本机受限 PG14 的已执行矩阵通过，PG16/17 和 CI 未验证。
P：真实模型、供应商账单、生产接入未验证，**不批准上线**。

本轮不执行提交、推送、部署、生产迁移或真实模型调用，不替代 C01 验收。

## 1. 修改归属和基线

接手 HEAD：`baf3f414187a198dbde1c41f8a90e42dee2629ca`。

接手时已有其他任务修改，先保存 1,425 个文件的 SHA-256、Git 状态和 diff：

`tmp_test/ak01-codex-20261020/baseline-hashes.json`、`baseline-status.txt`、`baseline.diff`。

本轮生产修改仅 7 个文件：

- `nexus_backend/app/intelligence/provider.py`
- `nexus_backend/app/intelligence/service.py`
- `nexus_backend/app/intelligence/ocr.py`
- `nexus_backend/app/intake/service.py`
- `nexus_backend/app/assistant/worker.py`
- `nexus_backend/app/jobs/worker.py`
- `nexus_backend/app/operations/meter.py`

另修改 `test_queue_workflow.py` 两个退避夹具，明确其模拟的是“已证实未发送”，
不能让只有 `busy` 错误码的替身代表未发送证据。
最终回归又发现 `test_intelligence_boundaries.py` 的两个旧包装函数只接受 `pages`，
因此新增 `limits` 无法透传，导致 3 项测试没有进入预期撤权/竞争路径。
仅将包装函数改为接收并转发 `**kwargs`；保留所有原安全断言。

新增 4 个专项测试文件、一个进程测试 helper、离线测试启动器以及本轮验收文档。
原 `test_model_cost_workflow.py` 的 500 万 + 500 万上限用例修正是接手前的修改，
本轮保留并复验，没有把它算成本轮实现。

不修改 C01、会计核心、数据库迁移、投影、前端或旧执行报告；
不替其他任务签收其未提交代码。归属核对见 `ownership-check.json`。

## 2. 实际修复

### 调用限制贯穿服务入口

原 OCR/journal 服务路径漏传有效 `limits`。现在由服务端企业政策生成严格类型的
`ExecutionLimits`，传入真正发送 HTTP 的 provider：

- 输入估算超过端点上限：HTTP 发送数为 0；
- 输出取端点、调用方和应用上限的交集；
- 禁止 bool、字符串、非有限数等被强制转换成合法限制；
- 固定任务截止时间，准备资料、前置校验和容量领取不能刷新原有时间预算；
- PaddleOCR 后的文字字段模型同样使用限额；
- journal 在发送及接纳结果时核对本次冻结的外发政策，政策变化拒绝继续；
- 不改变持久化的旧 fact digest，也不改写历史凭证、任务和回执。

`context_limit` 沿用现有“输入估算上限”契约；未将它包装成精确 tokenizer 结果，
也未擅自改为供应商总上下文窗口。

### 员工 AI 预填补齐同一政策

调用盘点额外发现 `IntakeService.recognize` 漏掉端点外发政策和限制：
未批准 OCR 场景仍能发送，调用中政策变化后仍能发布。

现在在预检、发送前、Paddle 阶段复检、结果接纳时检查当前企业政策，
向字段模型传递固定限额。过期结果不发布，已发生用量仍结算。
本地结构化电子票据解析保留原有不调用模型的路径。

### 发送证据、429 和费用分离

- provider 只在真实 HTTP 边界前失败时标记 `llm_attempted=False`；
- Paddle 和助手的异常包装保留该证明，不再把未发送误记为费用未知；
- 合法正用量和显式零用量优先于错误码，不因“busy/未发送”提示丢失；
- 明确 `llm_attempted=True` 时，不能仅凭本地错误码或上层 `attempted=False` 清零费用；
- 已发送 429 不因 `retryable=True` 自动创建下一次 attempt；
- usage 缺失/非法维持未知费用；合法零用量是已知零，二者不可互换；
- 自动退避仅允许未发送证据成立且没有 usage 的情况，保留原有限次数与间隔。

没有增加供应商自动重试，没有把发送后超时、断连或硬崩溃解释为免费。

### Windows 模板分支测试阻断

原命令稳定复现两例：
`finish_rule → UPDATE proposal_jobs → sqlite3.OperationalError: unable to open database file`。

诊断时保持同一源码、同一 SQL，只把 Python 子进程的临时目录切到独立可写目录，
两条模板路径即可通过。证据支持 SQLite 语句/保存点临时文件环境依赖；
没有证据支持“账务库路径不存在”，也没有声称已经定位 Windows 临时目录的具体 ACL 原因。

新增 `scripts/test_accounting_offline.py`，在导入 SQLite/pytest 之前设置独立临时目录，
清除继承的生产/模型环境变量和代理，只允许本机 socket/DNS，
并为 pytest 使用新建的专用 `--basetemp`。

没有改动数据库业务 SQL、关闭约束、使用内存数据库替换持久库或跳过模板测试。
最终组合中 `test_stage7_proposal_queue.py` 的 34 项全部通过。

## 3. 先失败后修复的证据

以下日志位于 `tmp_test/ak01-codex-20261020/`。失败断言均先于对应修复。

| 证据 | 原失败 | 修复目标 |
|---|---:|---|
| `limits-red.log` | 4 | OCR/journal 输入超限仍发送、输出超限 |
| `limits-paddle-red.log` | 3 | Paddle 限额和 OCR 准备时间 |
| `chat-deadline-red.log` | 1 | 助手前置校验刷新截止时间 |
| `limits-policy-red.log` | 1 | journal 政策切换仍发送 |
| `intake-red.log` | 5 | 预填政策/限额/接纳失效及 Paddle 未发送证明 |
| `retry-red.xml` | 6 | OCR/journal 已发送 429 自动再次调用 |
| `usage-precedence-red.xml` | 2 | 合法用量被本地错误提示清零 |
| `sent-evidence-red.xml` | 2 | 明确已发送被本地错误码清零 |
| `chat-unsent-red.xml` | 1 | 助手包装异常丢失未发送证明 |
| `stage7-red.xml` | 2 | Windows SQLite 临时目录导致模板失败 |
| `pg/pg-429-red.xml` | 6 | 受限 PG 路径的同类 429 退避缺陷 |

`limits-intersection-red.log` 中直接修改不可变端点导致的夹具错误不计业务红测。
初次 PG 测试的夹具初始化错误也不计业务红测。
已经正确的崩溃恢复、取消、竞争领取等属于补充验收，不虚构其原实现失败。

## 4. 场景矩阵

| 原缺口 | 本轮执行证据 |
|---|---|
| A01 服务发送次数 | 三入口实际 service → provider → 传输替身 → meter；请求数与费用行数断言 |
| A02 暂停、额度不足、限额 | 三入口请求数为 0，不能误作未知已发送费用 |
| A03 领取后、发送前退出 | 独立进程恢复，安全执行一次 |
| A04 持久发送标记后退出 | 收件数 0 也保留未知状态，新进程不盲目重发 |
| A05 已收件后断连/硬退出 | 独立接收计数为 1，独立恢复进程不重发 |
| A06 用量落库后、业务发布前退出 | 费用留存，不重复发布或再次发送 |
| A07 原始命令回放 | OCR 批次、journal 业务移交、chat 提问的原始 body/key 复用同一任务/回执 |
| A08 发送前/后取消 | 前者 0 调用，后者保留费用且不发布内容 |
| A09 撤权 | 发送前拒绝、结果接纳时拒绝；实际费用与业务读取权限分离 |
| A10 在途事实变化 | 旧结果不覆盖当前资料或人工修改 |
| A11 真双进程竞争 | 两个独立解释器/连接、启动屏障、生产领取逻辑，单次发送 |
| A12 429 分类 | 三入口 × 正用量/显式零/缺失；无自动新 attempt |
| A13 坏响应结构 | 缺失/空/多 choices 等失败保留合法外层 usage |
| A14 用量边界 | bool/负数/小数/缺字段/超界拒绝；零及最高合法显式总量通过 |
| 模板路径 | 命中发布模板时零模型调用、零 proposal 费用；税额仍取已核验字段 |

用量边界的准确范围：现有 `ProviderUsage` 的 prompt/completion/total **各字段**
均有 1,000 万上界。本轮不声称新增了供应商 total 与分项求和的一致性校验，
也不声称原生 PG 结算函数新增了总量关系约束。缺失/非法 usage 按未知处理，
没有实现保留部分已知 token 子字段的独立账本。

## 5. 最终测试

| 测试集 | 结果 | 证据 |
|---|---|---|
| 限额服务矩阵 + 员工预填 + Paddle | 128 passed | `limits-intake-final.log` |
| 助手包装修复后的受影响限额子集 | 18 passed，58 deselected | `chat-final.xml` |
| 三路径独立进程/HTTP/SQLite 矩阵 | 42 passed，0 skipped | `attempts/final-matrix.xml` |
| 独立受限 PG14 组合 | 122 passed，0 skipped | `pg/pg-final.xml` |
| 费用/队列/助手/账务组合首次终验 | 362 passed，3 个旧夹具失败，13 skipped | `final-regression.xml` |
| 旧夹具修正后 HTTP 完整复验 | 28 passed；含上述 3 个原失败 | `http-final.xml` |
| 会计审核/导出 PG 参数补验 | 12 passed，0 skipped；覆盖上述 12 个 PG skip | `pg/pg-ledger-final.xml` |

18 项是 128 项中受最后一处助手异常包装修复影响的复验，不能重复相加成独立用例数。
更早的 95、97、173 等中间结果不计入最终总数。
最终以 `(classname, name)` 合并，相同用例以最近一次明确复验为准：
690 项通过，只保留 1 个真正不适用于 SQLite 的 PG 专用用例。
原失败日志没有覆盖或删除，替换关系逐项记录在 `final-acceptance.json`。

只看到既有 Starlette TestClient 弃用警告；不静音失败、跳过或超时。

静态检查：本轮生产文件、专项测试、离线启动器 Ruff 通过，相关 diff whitespace 检查通过。

### 本地离线回归方式

```powershell
.\.venv\Scripts\python.exe scripts/test_accounting_offline.py `
  nexus_backend/tests/test_ak01_service_limits.py `
  nexus_backend/tests/test_ak01_retry_cost.py `
  nexus_backend/tests/test_ak01_attempt_matrix.py `
  nexus_backend/tests/test_stage7_proposal_queue.py `
  nexus_backend/tests/test_queue_workflow.py -q
```

其他最终回归覆盖 provider、费用、助手会话/动作、票据事实版本、会计凭证及政策。
完整命令在 `final-commands.json`，原始 JUnit 及合并后的逐项状态在本轮证据目录。

PG 专项只允许显式选择独立本机临时目标。使用 HEAD 干净副本叠加本轮白名单，
避免把现有 C01/投影脏修改混入数据库验收。环境为 PG14.24，独立端口 15438，
受限 runtime/worker，无超级用户或 BYPASSRLS 业务执行。

详情见 [AK01_PG_LOCAL_VALIDATION.md](AK01_PG_LOCAL_VALIDATION.md)。
临时 PG 已停止，测试密码文件已清理，原 5432 集群没有被改动。

## 6. 冻结候选 SHA-256

路径前缀：`nexus_backend/app/`。

| 文件 | SHA-256 |
|---|---|
| intelligence/provider.py | `b296e0005983824abff3d3bc101ddb63af9c780fe764c231bfb1a009d64023b0` |
| intelligence/service.py | `f2d3f1adbd4cb63acc16fef6c6a0ddbd7b9ec83bddee4205fc921926ea1b349f` |
| intelligence/ocr.py | `64dd1d2bff61de6bef8eff87966e354d5bf54921205043fa711fc1a69d77bf45` |
| intake/service.py | `5d891cea5a15d1c182345736c42f4f8e4dcd63432a2ca5271d472752eb64615e` |
| assistant/worker.py | `6a7078ece36e773b90dbb9c72d7e16f8ca435f304db773226d89f1c000000d24` |
| jobs/worker.py | `0407a5c2cf6aeabb56d87955e3bd66aefbd6cd4f7d5ae05e887217faf84a8043` |
| operations/meter.py | `ac093678fbd8fd2e07ceb859838b13363e82dc6b6054d5657d3710f66b7760c5` |

进程矩阵、最后助手子集和 PG 组合均核对执行前后指纹。
最终合并前再次核验 7 个生产文件未变；
1,425 个基线文件中只有本轮 7 个生产文件及 2 个测试夹具文件发生变化，
没有范围外文件变化或缺失。

## 7. 结论范围

本轮已解决 AK01 调用、费用、attempt 的明确缺口及其本地回归。
以下不在通过结论内：

- 真实模型效果、供应商实际账单/幂等行为；
- PG16/17、CI、生产或混合版本部署；
- C01、完整备份恢复、正式账务切换；
- 外部 HTTP 与数据库撤权的跨系统原子化。现有保证为发送前领域检查、
  实际传输企业检查、结果接纳检查；不声称撤权与发送之间不存在任何并发窗口。

后续 AK02 应在本轮候选上推进本地 ContextPack 集成，继续保留企业隔离、
异常转人工和会计复核后导出。AK01 的局部通过不能跳过其余工作包自己的验收门槛。
