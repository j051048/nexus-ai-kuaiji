# PLT09-A 计量口径审查

状态：**部分审核完成，尚不能作为人效结论**。本文件只审查现有系统指标，不填真实数据、不估算节省比例。

审查对象：`nexus_backend/app/observability/value.py`、`GET /api/firm/observability/value`、`nexus_backend/tests/test_value_baseline.py`。

## 逐指标结论

| 指标 | 分子 | 分母 | 时间归属 | 结论 |
| --- | --- | --- | --- | --- |
| `submitted_events` | `business_events` 中非 draft 对象数 | 无，展示为业务量 | `e.created_at` 月份 = 查询期间 | 可用；只代表当期创建，不代表当期办结 |
| `mapped_drafts` | 不同 `drafts.id` | `submitted_events`（映射率） | `event.created_at` 与 `mapping.created_at` 均 = 期间 | 已闭合本期事项 cohort；不是“本期全部映射动作” |
| `approved_drafts` | 不同 `drafts.id` | `mapped_drafts`（复核转化率） | `drafts.approved_at` 月份 + book period | 已导出对象不会从分子移出（有测试）；仍要求映射与批准同期间才能严格比较 |
| `returned_events` | 不同 `business_events.id` | `submitted_events` | `event.created_at` 与 `action.created_at` 均 = 期间 | 已闭合本期事项 cohort；本期不退回的上期事项不会进入 |
| `return_actions` | `business_actions` 行数 | 无 | 动作创建月份、事项创建月份 | 可表达“多次退回”，与 `returned_events` 分开 |
| `first_pass_rate` | `submitted_events - returned_events` | `submitted_events` | 两者均按事项创建月份与动作月份 | 分母/分子已同 cohort；仍为期间描述，不是个人评价 |
| `exception_rate` | `returned_events` | `submitted_events` | 同上 | 同上 |
| `review_conversion_rate` | `approved_drafts` | `mapped_drafts` | 两个不同对象/月份口径 | 可作趋势参考，不是个人工时 |
| `system_elapsed_hours_median` | 映射创建到批准的秒差 | 样本数 | 系统时间 | 只代表系统停留；接口明确 `labour_time_verified=false`，未冒充人工工时 |
| `ai_model_cost_micro` | `confirmed_micro` 优先，否则 `estimated_micro`；未知项不进入已确认总额 | 无 | `model_costs.period` | 明示为已确认/已知费用小计；`ai_model_cost_complete=false` 时不得当完整成本 |
| `ai_cost_per_approved_micro` | 同上 | 已批准对象数 | 费用期间 | 仅当 `cost_unknown_count=0` 才返回值，未知费用不会摊成 0 单价 |

## 已确认并修复的问题

1. **mapped_drafts 的 cohort 已闭合**：分子查询同时按 `e.created_at` 月份过滤，映射上期事项不再进入本期分子。
2. **returned_events 的 cohort 已闭合**：本期退回动作必须属于本期创建的事项；`return_actions` 同样按事项月份过滤。
3. **AI 费用总额语义**：新增 `ai_model_cost_complete`；`cost_unknown_count>0` 时为 false，`ai_model_cost_micro` 只能读作“已知费用小计”，不能当完整总成本。
4. 人工工时：系统没有人工计时，`labour_time_verified=false` 与空值是正确的，不应推算。
5. 不同月份创建/办理/完成对象：已按“事项创建月份”统一 cohort；不能跨期拼接。
6. 失败测试/回归：`test_value_baseline.py` **2 passed/11.11s**（含新增 `ai_model_cost_complete` 断言）。跨期夹具尚未单独写成回归用例，仍需补。

## 验收状态

- `test_value_baseline.py` 覆盖：空数据返回 null 比率、未知费用不计算单均、导出后仍计入 approved、零业务与未采集区分、费用完整性字段。
- 上述修复已通过现有测试，但 PLT09-A 仍需补一条“上期事项本期映射/退回不进分子”的显式回归用例；在补测前仍只算部分审核。
- 人工工时与真实基线属于 `PLT09-C`，状态为“工具就绪待客户输入”。

## 不能从本接口得出的结论

- 不能得出“节省 75%”“单笔复核 5–8 秒”“可减至 5 人”。
- 不能把 `system_elapsed_hours_median` 当作人工处理时间。
- 不能把 `cost_unknown_count>0` 的总额当作完整成本。
- 不能按个人排名；本接口只按企业/期间聚合。
