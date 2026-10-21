# R5 AG04 最终本地执行报告

## 结论

本包的离线调用、预算、快照与受控恢复契约已完成本地修复与专项验收：**100 passed，ruff 通过**。
本次不是生产队列/模型接线；不宣称生产多 worker、PG 或真实模型已验证。

只修改：

- `tools/ai_inference_lab/investigation.py`
- `nexus_backend/tests/test_ag04_investigation.py`
- 本报告

未修改 C01、AG01、UX、AG05、数据库迁移和 PG 投影；未提交、推送、部署；未访问真实模型、数据库或生产服务。

## 红—绿证据

1. 在原实现补行为测试，`-k r5`：**9 failed**。
   - 缺省 usage 被解释为零并 resolved。
   - token 预算 4、known 5 + unknown 的合法停止快照不能加载。
   - calls=0、三步、无证据的 forged resolved 被接受。
   - 人工接手覆盖原 unknown 原因。
   - bool/负数/小数/NaN/Infinity 预算被接受。
2. v2 修复后首轮：**85 passed**。
3. 再补两个审计红测：**2 failed**。
   - 重新计算 hash 后改变预算仍可恢复。
   - unknown 调用事实可从停止原因中隐藏。
   修复后：**87 passed**。
4. 再补类型/时钟红测：**2 failed**。
   - 超大整数时间预算抛 OverflowError 而非领域错误。
   - 发送后时钟失败丢失已知用量返回路径。
   修复后：**89 passed**。
5. 父任务交叉审计后补红测：证据处理期间超时/撤权、1100 层嵌套递归、超大文本，**4 failed**。
6. 补快照调用准入重放红测：零 token 预算仍有调用、前一次调用已耗尽 token/time 后仍有第二次调用，**3 failed**；零时间预算原有检查已拒绝。
7. 完成以上修复：**97 passed**。
8. 补深层 JSON 与超大快照红测：**2 failed**，非法 UTF-8 原已拒绝。读写统一 16 MiB 上限、快照结构深度 40 / 节点 1,000,000 上限、受控递归/编码异常；最终 **100 passed**。

最终命令：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.\.venv\Scripts\python.exe -B -m pytest nexus_backend/tests/test_ag04_investigation.py --noconftest -p no:cacheprovider -q --basetemp tmp_test/r5-ag04-final3
# 100 passed in 0.90s
.\.venv\Scripts\python.exe -B -m ruff check tools/ai_inference_lab/investigation.py nexus_backend/tests/test_ag04_investigation.py
# All checks passed!
```

## 关键契约

### A02/A03：调用用量独立于证据

- `CallOutcome` 每次 resolver 调用仅有一个 `CallUsage`，可返回零到多条 `Result`。
- `CallUsage` 默认 unknown；显式 `CallUsage(0, "known")` 才表示已知零。
- known_tokens 可保留 unknown/invalid 状态下的已知部分。
- 非法类型、布尔、小数、负数、非有限值、超上限统一分类，不截断、不强制转型。
- 一次调用多条证据只累计一次；无证据失败调用也保留费用。
- 发生 unknown、invalid、异常、撤权、迟到或预算超限后不追加调用。
- 旧裸 Result 列表不能提供可信调用用量，保持 usage_unknown 并停止，不接纳证据。

### A01/A05：可信状态与语义

- resolver 只接收冻结 `RunView`，steps/evidence 递归复制。
- 已接纳 Result.value 复制为有限 JSON，外部保留对象无法回写原事实。
- 每次调用都记录唯一 attempt_id、known_tokens、usage_state、elapsed_seconds、error、接纳证据 ID。
- calls、steps、用量累计、证据接纳与终态互相校验。
- resolved 要求真实调用、已知用量、无错误且有 resolved 证据；不接受空证据 forged resolved。
- 混合企业、旧版本、同 ID 异内容整批拒绝。
- 证据校验、深拷贝处理也计入总时限；提交证据前重检时间、授权及事实版本。
- JSON 证据限制深度 32、节点数 10,000、单值估计大小 256 KiB、运行证据数 256；深嵌套/超大值明确转人工并保留已知用量。
- 快照逐次重放调用前累计 token/time，禁止预算耗尽后的额外调用；合法最后一次调用超预算停止仍允许往返。

### A04/A06/A07：可恢复与授权

- 快照升级为 `investigation-v2`，不隐式升级 v1。
- `Binding` 包含 run、企业、账套、事实版本，以及 input/config/prompt/policy SHA-256。
- `load` 和 `resume_as_handoff` 必须提供可信 expected Binding、expected_budget 和当前 authorization_check。
- 加载前后重检授权；检查当前事实/输入/策略指纹及预算；失权不返回证据。
- 多项停止事实独立保留，不使用单一原因白名单解释预算超额。
- known 5 / 预算 4 + unknown/invalid/error/迟到/撤权组合能 save→load。
- 原停止原因、计数、已知用量、预算、已消耗时间不因恢复重置。
- 恢复只产生受控人工接手投影，不调用 resolver，不自动继续。
- 已解决结果也需重新提供可信范围及当前授权才能投影。
- SHA-256 仅检查文件完整性；不是身份认证或带密钥的防篡改签名。

### 写入与多进程证据

- 临时文件、flush/fsync、原子 replace；失败保留最后完整快照并清理本次临时文件。
- save/load 共用 16 MiB 文件上限，读取最多上限加 1 字节；超大写入在替换前拒绝并保留旧文件。
- 摘要与文件序列化都检查深度 40、节点 1,000,000 上限；深层/编码/递归错误为受控 InvestigationError。
- 同一目标并发保存采用最后一次成功原子替换，不能产生半文件。
- 独立子进程完成加载与人工接手，保持调用计数、原 unknown 原因与已知用量；无 resolver 路径。
- 未承诺生产多 worker 领取、租约或自动恢复；本模块无生产队列依赖。

## v1 → v2 离线调用迁移

仓库检索确认调用代码仅在本模块专用测试；生产 assistant/OCR/worker 未导入该模块。

新调用形式：

```python
run = investigate(
    binding=trusted_binding,
    budget=budget,
    resolver=lambda view: CallOutcome(
        results=(evidence,),
        usage=CallUsage(known_tokens=actual_known_usage, state="known"),
    ),
)
restored = load(
    path,
    expected=current_trusted_binding,
    expected_budget=original_trusted_budget,
    authorization_check=current_authorization_check,
)
```

- 不能从快照或模型返回值生成 expected Binding；应来自可信当前请求与冻结实验配置。
- 指纹需由调用方对其可信输入和版本化配置生成。
- 旧 Result 的 tokens/usage_known 字段不再是调用费用来源；迁移到 CallOutcome.usage。
- 旧 v1 快照若需要保留，可作为历史原件留存；不得当作 v2 可恢复运行直接加载。

## 最终候选 SHA-256

```text
investigation.py
2956ECD225DD2FBDE3EE7D5D31D4A492E53FBBB915B219550F33A660E2720156

test_ag04_investigation.py
EB312228C654101BB17A5D83AC4C1FE935A10D96459F3B560E10AA98BD26F4DC
```

AG04 本包未留未实现项；组合回归由主任务在 UX/AG05 合并候选上执行。上述通过范围严格限于本地离线契约。
