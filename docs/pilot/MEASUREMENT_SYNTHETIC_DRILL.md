# PLT09 合成计量演练（非基线）

> **全部数字为虚构演练值。** 不得用于客户基线、人效评价、报价、减员或五人定编。
> 真实采集仍以 `measurement_baseline.csv` 的空白单元格为准；空值表示未采集，0 表示已知零。

- 生成脚本：`scripts/plt09_synthetic_drill.py`
- 明细：`docs/pilot/templates/measurement_synthetic_drill.csv`
- 汇总：`docs/pilot/templates/measurement_synthetic_drill.json`

演练范围为出租车公司 1 家 × R01–R15 × 2026-10，共 15 行。汇总：

| 指标 | 值 |
| --- | --- |
| 行数 | 15 |
| 未采集行 | 0 |
| 部分采集行 | 0 |
| 已采集行 | 15 |
| 已知零值单元格 | 113 |

## 2026-11-19 实际命令

```text
python scripts/plt09_synthetic_drill.py
{"rows": 15, "unknown_rows": 0, "partial_rows": 0, "complete_rows": 15,
 "measured_zeroes": 113, "status": "已采集",
 "notice": "合成演练，数字全部虚构，禁止作为客户基线、人效或定编依据"}
```

本演练只证明工具可区分未采集、已知零和已采集，不证明任何客户工时或人效结论。
