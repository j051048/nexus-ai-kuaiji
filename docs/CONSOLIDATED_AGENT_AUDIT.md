# Nexus AI 垂直会计 Agent 综合审计报告

> 版本：合并版  
> 基线：`baf3f414` + 当前工作区未提交修改  
> 方法：静态架构审计 + 关键路径代码阅读 + 已有测试复跑结果  
> 边界：未连接真实模型、真实客户、线上数据库或对象存储；不代表生产认证。

---

## 0. 执行摘要

Nexus 的会计安全底座明显强于普通 AI 记账 Demo：

- PostgreSQL RLS + 受限角色；
- 正式凭证 Append-Only；
- 锁期、期初冻结、借贷平衡、叶子科目校验；
- AI 只生成未审草稿，人工复核后导出；
- 持久队列、租约、发送后未知不自动重试；
- 金额使用整数分/Decimal。

但 Agent 运行内核仍处于收口期：

- `UnifiedWorker` 实际串行，慢 OCR 会阻塞助手；
- 心跳 `inflight=0`，监控失真；
- 会计助手只校验引用 ID，不校验回答金额；
- 模板确定性路径当前有 2 项真实失败；
- 大文件、双数据库方言和重复授权 SQL 带来长期维护风险；
- 税务仍只有税额估算，没有税负/进销项/申报能力。

### 综合评分

| 维度 | 权重 | 评分 |
|---|---:|---:|
| 会计与安全底座 | 30% | 8.5 |
| 代码架构 | 20% | 7.0 |
| 调度与管线 | 20% | 6.0 |
| 代码质量 | 15% | 6.0 |
| 风险控制 | 15% | 7.0 |
| **加权综合** | 100% | **7.1 / 10** |

**结论：可信任其会计隔离与人工复核边界；暂不能信任其高并发调度、AI 金额事实和模板确定性路径。**

---

## 1. 代码架构：7.0 / 10

### 优点

1. 领域包划分清晰：`accounting`、`business`、`posting`、`subledger`、`closing`、`intelligence`、`tax`、`legacy_migration`、`jobs`、`workspace` 等 40 余个模块。
2. PostgreSQL 物理隔离：`database.py` 在事务内 `SET LOCAL ROLE`，并设置 `app.user_id`/`app.firm_id`；迁移中存在 `FORCE ROW LEVEL SECURITY` 和企业写锁触发器。
3. 路由已按领域拆分：`main.py` 使用大量 `include_router`，不是“没有 APIRouter”。
4. Agent 能力边界克制：模型只建议动作，不选对象 ID；动作需要预览、人工确认、幂等回执。
5. 138 迁移将 projection 读取拆为独立登录，system 不再直读业务源表。

### 问题

1. **巨型文件**：`accounting/service.py` 约 82KB；`src/LegacyMigrationWorkspace.tsx` 约 132KB；`src/App.tsx` 约 62KB；`main.py` 约 48KB。
2. **双数据库方言分叉**：业务层大量 `if db.is_postgres`，同一规则存在两套 SQL/锁/函数路径。
3. **授权 SQL 重复**：assistant、workspace、closing、accounting 各自维护相似谓词，容易一处修复、多处遗漏。
4. **税务能力薄**：`tax/estimate.py` 仅约 67 行，只做增值税/附加税估算，没有税种配置、申报表、进销项勾稽、税负预警。
5. **Agent 能力未统一**：Assistant、OCR、Proposal、Batch、Migration 各自有 worker/重试/监控语义，没有统一能力契约。

### 业务影响

- 多客户扩张时，新税种/新准则接入成本高；
- SQLite 通过、PG 行为不同会成为常态；
- 大文件让代码审查看不到完整影响面。

### 建议

**高**

1. 拆 `accounting/service.py` 为凭证命令、账簿查询、导出、复核。
2. 抽 dialect/repository 层，业务层不直接写 `is_postgres`。
3. 建统一授权谓词模块。

**中**

4. 拆 `App.tsx` 为路由、范围状态、报告入口、权限展示。
5. 税务升级为“税种配置 + 底稿 + 申报表 + 风险规则”。

---

## 2. 代码质量：6.0 / 10

### 优点

1. 金额使用整数分与 Decimal，避免浮点尾差。
2. Pydantic 模型严格，`extra="forbid"`、字段长度和枚举约束较多。
3. 测试体量大：约 270 个测试文件、4.5 万行测试。
4. 注释多解释“为什么”，尤其是发送后未知、历史失效、投影只读。
5. 错误分类意识强：timeout/network/http/context/stale/revoked/unknown。

### 问题

1. **当前仍有真实失败**：

```text
test_stage7_proposal_queue.py
2 failed, 32 passed

test_matching_published_template_avoids_journal_model_and_fee
test_tax_template_consumes_only_verified_net_tax_after_ocr
```

异常：

```text
app/jobs/worker.py:355 finish_rule
sqlite3.OperationalError: unable to open database file
```

模板命中、无模型路径本应最稳定，却在 `finish_rule` 更新 `proposal_jobs` 时失败。

2. **宽泛异常掩盖根因**：`jobs/worker.py` 与 `batch_ops/worker.py` 的 `except Exception` 将真实异常统一成 `worker_failure`。
3. **超大组件圈复杂度高**：`LegacyMigrationWorkspace.tsx`、`App.tsx`、`accounting/service.py`。
4. **注释与代码漂移**：例如“template/no-model branch unchanged”描述的是设计意图，实际模板路径在 `jobs/worker.py`。
5. **Python 侧主要靠 ruff，缺 mypy/pyright 级别类型检查。**

### 业务影响

- 模板失败让会计回退人工，直接降低代账效率；
- 宽泛异常导致线上只能“重试/重启”，无法快速定位客户影响；
- 大文件让新规则上线周期变长。

### 建议

**高**

1. 修复 `finish_rule` 的 SQLite 资源生命周期。
2. Worker 异常分级：resource/validation/provider/authorization，保留安全错误码。
3. 统一授权谓词。

**中**

4. 新代码行数/复杂度门槛。
5. 关键模块加类型检查。

---

## 3. 调度与管线控制：6.0 / 10

### 优点

1. 持久队列、数据库租约、attempt 表、发送标记齐全。
2. 发送后未知不自动重试，`retry_is_safe` 只接受未发送证据。
3. 企业调度序号、provider 跨进程容量控制已有基础。
4. 正式凭证 Append-Only；草稿审核、内部过账、外部交付是三套状态轴。
5. 红字冲销通过 `reverse_voucher`，不原地改凭证。

### 问题

1. **UnifiedWorker 实际串行**：

```python
for worker, budget in self._plan:
    for _ in range(budget):
        result = worker.run_once()
```

慢 OCR 会阻塞同进程助手任务，即使企业不同、容量允许。

2. **心跳失真**：

```python
monitor.tick(inflight=0, state=state)
```

慢任务执行时监控仍显示空闲，无法据此做容量/背压。

3. **公平调度只部分实现**：assistant 有企业调度序号，OCR/proposal/迁移的租户公平仍不完整。
4. **重试与费用状态仍有矩阵缺口**：429 有/无 usage、双进程竞争、接纳时撤权、发送后崩溃尚未完整收口。
5. **空轮询带来数据库压力**：固定 `idle_seconds` 轮询，无任务时持续查询。

### 业务影响

- 申报期大客户批量导入会拖慢其他客户实时助手；
- 监控显示空闲但实际有慢调用，运维无法判断容量；
- 未知费用错误重试会重复调用/重复计费。

### 建议

**高**

1. UnifiedWorker 真并发：assistant 与 OCR/proposal 分执行器，保留同企业互斥。
2. 心跳由执行任务独立上报，记录真实 inflight/类型。
3. 补 AK01 三入口发送/费用/撤权/双进程矩阵。

**中**

4. 统一 JobLease 抽象。
5. 低优先级任务 aging，避免迁移饥饿。
6. 可选 PG LISTEN/NOTIFY，不作为当前前置。

---

## 4. 风险控制：7.0 / 10

### 优点

1. 未审核草稿不能导出；月结需要 preflight、快照复核、明确理由。
2. 未知税额不填零；未知费用不自动归零。
3. 授权每次服务端重查，模型/请求正文不能授予权限。
4. 撤权、事实版本变化、迟到结果有 stale/revoked 语义。
5. 交付回执逐项核验，批量部分失败可定位/重试。

### 问题

1. **AI 回答只校验引用 ID，不校验金额事实**：

```python
allowed = {item["id"] for item in snapshot["evidence"]}
if set(reply.citation_ids) - allowed:
    raise ProviderError(...)
```

`AssistantReply.answer` 是自由文本，可能：

- 金额说错；
- 把价税合计说成营业收入；
- 声称已审核/已记账/已导出。

2. **Maker-Checker 缺失**：大额冲销、反结账、跨期调整只有权限校验，没有“制单→主管复核”双人工作流。
3. **税务风险模型缺失**：没有税负率、进销比、留抵、连续亏损、无票支出预警。
4. **模板路径当前失败**，确定性风控路径不稳定。

### 业务影响

- 金额错误回答可能被会计直接采信，造成错账；
- 单人冲销/反结账在规模化代账中有舞弊与误操作风险；
- 缺少税务预警会放大稽查风险。

### 建议

**高**

1. `AssistantReply` 增加结构化 claims：金额、币种、期间、来源、版本；关键数字只能来自校验后 claim 或确定性模板。
2. 禁止自由文本输出动作完成语义，动作结果只来自 receipt。
3. 修复模板路径。
4. 完成 AK01 费用/attempt 矩阵。

**中**

5. 大额冲销/反结账 Maker-Checker。
6. 税务风险预警 MVP。

---

## 5. 安全与多租户隔离：8.5 / 10

### 优点

- PostgreSQL RLS + FORCE RLS；
- API/worker/system/projection 角色分离；
- 事务级角色与 firm/user 上下文；
- 138 迁移撤销 system 直读；
- 4 个代表 PG 套件 80/80 通过；
- 迁移校验和与 strict status。

### 问题

- SQLite 与 PG 授权语义不同；
- 授权 SQL 重复；
- 前端范围状态仍可能造成 URL/请求/标题不一致；
- 诊断/日志需持续确认不泄漏跨企业信息。

### 建议

- 每个新 API 强制“scope + RLS + 测试”三件套；
- 诊断接口默认聚合，不返回客户明细；
- 统一授权谓词。

---

## 6. 税务与会计扩展性：6.0 / 10

### 优点

- 复式记账、锁期、期初冻结、Append-Only 扎实；
- 税额估算严格 Decimal、有限值、两位小数；
- 红字冲销合规方向正确。

### 问题

- `tax/estimate.py` 仅增值税/附加税估算；
- 无企业所得税、印花税、留抵台账、申报表；
- 无税负率/进销比/行业对比；
- 新税种需改函数签名，不是策略引擎。

### 建议

**高**

1. 建税种策略接口：VAT、附加税、企业所得税、印花税。
2. 建税务底稿：销项、进项、留抵、预缴。

**中**

3. 税负率/进销比预警。
4. 关账前税务体检。

---

## 7. 前端产品与交互：6.5 / 10

### 优点

- 角色工作台、客户门户、内部工作台分离；
- 复核抽屉、批量进度、月结、交付回执闭环；
- 已有 React.lazy 对路由/管理模块懒加载；
- 有真实浏览器验收和手机宽度测试。

### 问题

- `App.tsx` 仍承载全局范围、报告入口、弹窗协调；
- `LegacyMigrationWorkspace.tsx` 132KB；
- 保存视图恢复没有单一 range store；
- 报告入口存在 A/B 范围污染风险；
- 部分验收依赖组件断言，不能替代后端鉴权。

### 建议

**高**

1. 单一 `WorkspaceScope` store：company/book/period/view。
2. 报告入口显式 target，禁止猜。
3. 企业切换清理旧 book/period。

**中**

4. 拆 App 与迁移工作区。
5. 键盘/焦点/错误关联测试。

---

## 8. 可观测性与离线评测：6.5 / 10

### 优点

- 费用、未知用量、失败、积压、队列等待已有监控；
- AG04 有预算、证据、快照、人工接手；
- AG05 有 manifest、固定分母、风险 FP/FN、H01 薄适配；
- JSON/Markdown 报告。

### 问题

- `inflight=0` 导致监控失真；
- 未统一 `run → attempt → context → provider → validation → action` trace；
- 费用事实与展示事件边界未完全统一；
- 部分测试用通过数量代替矩阵。

### 建议

**高**

1. 统一 ExecutionTrace。
2. 关键发送事实与展示日志分离。
3. 监控区分排队/容量/模型/校验耗时。

---

## 9. 测试与验收可信度：7.0 / 10

### 优点

- 270 个测试文件、约 4.5 万行；
- AG02–AG05/H01 组合 111 项通过；
- 4 个代表 PG 套件 80/80；
- 大量合成数据、MockTransport、隔离数据库。

### 问题

- `test_stage7_proposal_queue` 2 项失败；
- AK01 三入口矩阵未完成；
- 部分测试是补充验收，不是先红后绿；
- SQLite 不能替代 PG 并发；
- 尚未跑完整 86 个 PG 文件。

### 建议

- 每个缺陷记录：失败命令、失败断言、修复、通过命令、候选 hash；
- 模板/撤权/双进程/未知费用优先补红测。

---

## 10. 对两份原始审计的校准

| 原始判断 | 校准 |
|---|---|
| “main.py 缺乏 APIRouter 拆分” | 不准确。已有大量 `include_router`；问题是 lifespan/装配仍集中 |
| “前端缺乏懒加载” | 不准确。路由/管理模块已有 React.lazy；问题是 App 全局状态耦合 |
| “UnifiedWorker 高并发 9 分” | 高估。当前顺序执行，且 `inflight=0` |
| “CQRS 读写分离” | 偏强。是投影读模型 + 专用角色，不是完整 CQRS |
| “AI 成本计量完整” | 方向正确，但 429/unknown/双进程/撤权矩阵未完整收口 |
| “AI 风险控制 8.5” | 高估。只校验引用 ID，不校验回答金额 |
| “RLS/Append-Only/锁期强” | 准确，应保留高分 |
| “缺税务预警/Maker-Checker” | 准确，是真实业务缺口 |

---

## 11. 关键问题优先级

### P0：不做会直接影响会计信任

1. AI 回答结构化 claim 校验；
2. UnifiedWorker 真并发 + 真实心跳；
3. 修复模板 `finish_rule` 失败；
4. 完成 AK01 发送/费用/撤权/双进程矩阵。

### P1：影响规模化代账效率

5. Maker-Checker；
6. 税负/进销项预警；
7. 统一授权谓词；
8. 拆大文件与 dialect 层；
9. ExecutionTrace；
10. WorkspaceScope 单一入口。

### P2：长期演进

11. 税务策略引擎；
12. 完整 PG 86 文件矩阵；
13. 多进程压测；
14. 真实模型小流量评测；
15. PG LISTEN/NOTIFY；
16. OpenAPI 生成前端类型。

---

## 12. 最终结论

这是一套**会计底座强、Agent 内核仍在收口**的系统。

可以信任：

- 多租户 RLS；
- 正式凭证 Append-Only；
- 锁期与期初冻结；
- 人工复核后导出；
- 未知税额不填零；
- 发送后未知不自动重试。

暂不能信任：

- 高并发下助手不被慢 OCR 拖住；
- AI 回答中的金额一定正确；
- 模板确定性路径稳定；
- 税务风险已覆盖；
- 大额冲销有双人复核。

最终判断：

> **7.1 / 10：具备成为代账 SaaS 核心核算系统的底座，但尚未达到可规模化无人监督运行的程度。**

下一步不应继续加页面或加 Agent 花活，而应优先补：

1. AI 事实校验；
2. 调度并发与心跳；
3. 模板路径与费用矩阵；
4. 税务预警与 Maker-Checker。
