# IMP05 验收记录：真实使用与协同闭环

- 日期：2026-11-06
- 状态：**A 阶段完成；B 阶段待 IMP02–04/06 后执行**
- `real_user=pending`、`real_device=pending`、真实通知/客户签收=未接
- 候选：HEAD `143d4aea…`，source 1909 / `7886e47a…`，migration 338 / `2c96acd5…`

## A 阶段交付

- `docs/IMP05_TRIAL_PACKAGE.md`：两家以上虚构企业、员工/部门/财务/制单/复核/负责人/签收角色、T1–T6 任务卡、主持说明、记录字段、外部 pending。
- `tmp_test/imp/imp-20261106-a/trial/`：试用包副本、空 `records.csv`、`summary.json`。
- `scripts/imp05_trial_summary.py`：本地 CSV 汇总，不生成“0 错误”。
- `nexus_backend/tests/test_imp05_trial_summary.py`：6 项契约测试。

## 红测与修复

首次 `pytest test_imp05_trial_summary.py`：**3 failed / 3 passed**。

- 空记录 `summarise([])` 缺 `rates` 键：修复为 `rates: {}`。
- 非放弃记录被强制要求 `failure_step`，导致 completed/assisted 全判 incomplete：从 `REQUIRED` 移除 `failure_step`，仅 `abandoned` 必须提供。
- CLI round-trip 因同一必填问题失败：修复后通过。

修复后：

```text
.venv/Scripts/python.exe -m pytest -q nexus_backend/tests/test_imp05_trial_summary.py
6 passed in 0.27s
```

JUnit：`tmp_test/imp/imp-20261106-a/imp05-summary.xml`

空记录 CLI：

```text
{"status":"incomplete","reason":"no_records","denominator":0,"rates":{},"problems":["no records: do not report 0 errors"]}
```

## 契约覆盖

| 用例 | 结果 |
|---|---|
| 05-T1 无记录/缺设备或候选/重复记录不得完整通过 | 6 passed 覆盖空记录、必填缺失 |
| 05-T2 失败/放弃保留在分母，自动化不算真人 | 覆盖 abandoned 仍在 denominator、automation 拒绝 |
| 05-T3 不同企业隔离、复核人≠制单人 | 由试用包角色表 + 现有隔离/复核测试承担；本脚本不涉及授权 |
| 05-T4 打开消息不审批/不签收、旧包不静默升级 | 由现有通知/签收测试承担；本包未改 |
| 05-T5 正向链 + 退回/恢复链 | B 阶段浏览器回归；本轮只准备材料 |

## 待办（B 阶段）

- IMP02–04/06 完成后，用最终页面跑协同浏览器回归：员工提交→部门审批→财务核验→会计复核→交付/签收；退回补件；上传中断恢复。
- 本地测试身份仍是本地登录替身，生产必须用真实测试账号；不得把身份切换器当授权能力。
- 真人试用 3–5 人、真机（iOS/Android/微信）、真实通知均 pending；不得用自动化数据填 `records.csv`。
