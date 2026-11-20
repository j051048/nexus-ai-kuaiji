# RWK04：报表回落与历史报表模式

先读 `RWK00_MASTER.md` 和 `docs/pilot/PLT02_POLICY_GATE.md`。前置：RWK02。

## RWK04-A  `create` 中的小企业模板回落

**现状。** `nexus_backend/app/reports/service.py` 的 `create`：

```
standard = policy["standard_kind"] if policy else "small_enterprise"
try:
    template = self._template_for_standard(conn, standard)
except AccountingError as exc:
    reasons.append(str(exc))
    template = self._template_for_standard(conn, "small_enterprise")
```

两处回落：无政策时按小企业准则；政策指定的准则找不到模板时改用小企业模板。结果被标为试算且不能发布，但试算数字是在未告知的口径下算出来的。PLT02-A 的要求是"没有任何路径把未核定当成小企业准则用"。

**做法。**

1. **无生效政策**：不生成报表内容。返回明确的"核算政策未核定，无法出表"，并指向建立政策的入口。不落一条带小企业模板的记录。
2. **政策指定的准则没有对应模板**：同样不生成，返回"某准则的报表模板不存在"。
3. 如果确有"在政策批准前先看一眼数字"的需要，那必须是用户**显式选择**一个准则来试算，且结果上写明"按用户选择的某准则试算，非核定口径"。先搜索前端是否有这种入口；没有就不新增，把这一需求记入 `docs/rework/DECISIONS_NEEDED.md`。
4. 搜索全仓其余 `"small_enterprise"` 作为默认值的位置（月度简报、交付包、助手取数、期初、开账），同类回落一并处理并列表。种子数据和枚举定义不算。
5. 前端：报表与关账页面在无政策时显示上述提示与入口，不显示空报表或小企业报表。

**先失败测试。**

- 无生效政策的账套调用报表生成：修改前生成了小企业模板的试算记录，修改后返回"未核定"且 `report_runs` 无新行。
- 政策为 `enterprise` 但模板缺失：同上。
- 有合成政策：可试算，记录的 `standard_kind` 与政策一致。

**验收。**

```
<python> -m pytest nexus_backend/tests -q -k "report or policy or close or delivery"
npm run typecheck && npm test
```

因夹具没建政策而失败的测试：给夹具补明确的合成政策，逐个列出。

## RWK04-B  历史报表的模式

**现状。** `nexus_backend/migrations/postgres/292_*.sql`（及 SQLite 对应迁移）：

```
ALTER TABLE accounting.report_runs ADD COLUMN mode TEXT NOT NULL DEFAULT 'formal'
```

迁移前已存在的报表全部自动成为"正式"，而它们生成时没有经过任何正式门槛。新插入的行若漏写 `mode` 也会默认成正式。

**做法。** 新增前向迁移（两种库），不改 292：

1. 增加一个取值，用于"门槛建立之前生成、未经核定"的历史报表（例如 `legacy`）。把 `CHECK` 约束扩展为包含它。
2. 回填规则：292 应用之前生成的、且没有 `policy_version` 与 `policy_approver` 的行，改为该历史取值。回填前先统计受影响行数并写入迁移注释说明的口径；本地库的实际数字写入状态文件。
3. 默认值改为不是 `formal` 的安全值，或去掉默认值使漏写直接报错。选一种并写明理由；确认所有插入点都显式写 `mode`。
4. 发布守卫触发器已限制只有 `formal` 可发布：历史取值同样不可**新**发布。**已经发布的历史报表保持已发布状态不变**，不追溯撤回；界面上标注"门槛建立前发布"。
5. 接口与前端的模式标签增加该取值的显示。
6. 交付包、导出、助手、集团汇总中把它与 `formal` 区分显示，不混同。

**先失败测试。**

- 迁移后，模拟的历史行（无政策字段）模式为历史取值，不是 `formal`。
- 插入不带 `mode` 的行：按你选的方案，报错或落入安全值，不成为 `formal`。
- 历史未发布报表尝试发布：被拒。
- 已发布的历史报表仍可读取与下载。
- 迁移重复应用幂等。

**验收。** 新增用例 SQLite 通过；PG16、PG17 有环境则跑，没有则写"PG 未验证"。`<python> -m pytest nexus_backend/tests -q -k "report or delivery"` 无新增失败。
