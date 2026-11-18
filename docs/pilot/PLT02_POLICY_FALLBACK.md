# PLT02-A 默认准则回落盘点

日期：2026-11-18
范围：报表生成入口、核算政策加载、政策创建表单。
原则：准则只能来自企业已批准、带版本和生效期的 `accounting_policies`；没有 active 政策时阻断，不按行业、账套名或 UI 初值推定。

## 盘点结果

| 位置 | 当前行为 | 分类 | 处理 |
| --- | --- | --- | --- |
| `nexus_backend/app/reports/service.py:84-90` `ReportService._standard_kind` | 无 active 政策时返回 `STANDARD_UNASSESSED`（`unassessed`），不再返回 `small_enterprise` | 政策加载回落 | **已删除**，改为显式未核定哨兵 |
| `nexus_backend/app/reports/service.py:118-125` `_template_for_book` | 收到未核定哨兵时抛 `AccountingError(409, "核算政策未核定，不能生成报表；请先批准当前期间适用的核算政策")` | 生成入口阻断 | **已新增**；不再查默认模板 |
| `src/OpeningPreparationPanel.tsx:50,162,219-232,241-250` 政策创建表单 | 准则/纳税人身份原先预选“小企业/小规模”，可能被直接提交 | UI 默认值 | **已删除预选**：空值 + “请选择准则/请选择纳税人身份”，未选择时按钮禁用 |
| `nexus_backend/app/opening/service.py:83-92` | 写入调用方提交的 `standard_kind`，服务端不补默认值 | 写入入口 | 无需改动；模型仍只允许两种准则 |
| `nexus_backend/app/reports/profit.py:76,103,129,134,145,292` | 利润映射固定 `ReportService.TEMPLATE_ID`（`small_enterprise_basic`） | 非报表生成路径的硬编码 | **未改**；利润映射属于 OPT07 后续范围。若企业准则为 `enterprise`，利润映射默认项仍按小企业模板；已记录为顺带发现，不冒充 PLT02-A 完成。 |
| `nexus_backend/app/assistant/delivery_details.py:84` | 结账依赖指纹固定传 `small_enterprise_basic` | 非报表生成路径的硬编码 | **未改**；只影响依赖版本 scope，不影响报表数值。记录为后续结账依赖 scope 修复项。 |
| `nexus_backend/tests/**`、`tests/fixtures/**` 中 `standard_kind="small_enterprise"` | 测试显式构造政策 | 测试夹具 | 不是产品回落；保留，便于覆盖小企业分支 |
| `src/` 其他 `small_enterprise` | 只用于展示已加载政策或显式表单选项 | 展示/输入 | 不是服务端默认值 |

## 结论

报表生成入口的静默回落已删除：无 active 政策时报表创建 409，不再生成小企业报表。政策创建表单也不再预选准则或纳税人身份。利润映射和结账依赖 scope 的固定模板仍存在，但不在报表生成数值路径；已列为后续项，未计入本模块通过。

## 验收命令

```bash
cd nexus_backend
PYTHONPATH=.. python -m pytest tests/test_plt02_policy_gate.py -q --tb=short
```

本模块测试覆盖：

- 无 active 政策 → `AccountingError`，文案包含“核算政策未核定”，状态 409。
- 有 active `small_enterprise` 政策 → 报表使用 `small_enterprise_basic`，不被门禁误伤。
