# IMP07 验收记录：评测补强（离线合成）

- 日期：2026-11-06
- 状态：本地离线工程通过；真实模型/真实样本/真人评审 pending。
- 候选：HEAD `143d4aea7154b1905b96ab757ecd14865354b641`；source 1921 / `9e85d77f67b212e444a7919e14b9292db9044f9077003f0a4157fe6964f95b5d`；migration 338 / `2c96acd52de191347a2365962e879340918fad013786faa260b4b13140646116`。
- 运行目录：`tmp_test/imp/imp-20261106-a/imp07/`。

## 目标与边界

补足可追溯的困难票评测：来源组与变体分开、开发/验收按来源隔离、固定分母、缺资料/未知不删分母、关键字段退步否决、候选身份绑定。本轮只使用合成语料与本地确定性解析器，不调用真实 OCR/模型，不把离线分数写成真实识别率。

## 语料

`scripts/imp07_build_corpus.py` 生成 `nexus_backend/tests/fixtures/imp07_eval/corpus.json`：

- 30 个合成来源（S001–S030），每个 4 个变体：`original`、`rotated`、`multi_page`、`low_contrast`，共 120 项。
- 20 个来源（80 项）为 dev，10 个来源（40 项）为 holdout；变体继承来源 split，禁止同源跨 split。
- 每项 10 个金标字段；票号保留前导零；金额由净额与 6% 税额计算。
- `data_nature=synthetic_only`；生成器不读真实客户数据。

## 评分器与红绿测试

`tools/imp07_eval/__init__.py`：

- `load_corpus` 重算输入 hash，校验来源/变体数量、同源 split、金标字段类型、风险/弃答标记。
- `score` 拒绝重复/未知 item、候选版本不符、选择集触碰 holdout；固定分母 120；missing/unknown/failed 单列；费用 `None` 记 unknown 而非 0；risk 样本缺 review、supported 样本弃答均记安全失败；baseline 对比产生字段/样本回归；结果为 `accepted_offline` / `rejected` / `incomplete`。
- 候选行由 `parse_invoice(ocr_text)` 从 OCR 文本确定性解析，不读取 `gold_fields`；金标只进入评分器，不进入候选行生成。
- `render_markdown` 固定声明“真实模型效果未验证；合成；来源组与变体分开”。

命令与结果：

```text
.venv/Scripts/python.exe -m pytest -q nexus_backend/tests/test_imp07_eval.py
7 passed in 0.19s
```

覆盖：同源跨 split/输入 hash 篡改、重复/未知 item/候选版本、缺发票前导零的字段回归、选择集使用 holdout、全 unknown/全弃答的固定分母、报告详情计数、RSI snapshot 约束。

## 离线报告

```text
.venv/Scripts/python.exe scripts/imp07_run_eval.py
{"good": "accepted_offline", "regressed": "rejected", "unknown": "incomplete"}
```

| 报告 | 候选 | 成功/分母 | 缺失 | 未知 | 字段正确 | 回归 | 安全失败 | 结论 |
|---|---|---|---|---|---|---|---|---|
| `good/report.json` | `imp07-good-v1` | 120/120 | 0 | 0 | 1200/1200 | 0 | 0 | `accepted_offline` |
| `regressed/report.json` | `imp07-bad-v1` | 119/120 | 0 | 0 | 1199/1200 | 2（`field_regression` + `sample_regression`） | 0 | `rejected` |
| `unknown/report.json` | `imp07-unknown-v1` | 0/120 | 0 | 120 | 0/1200 | 0 | 16 | `incomplete` |

- `good` 与 `regressed` 均以 `imp07-baseline-v1` 为基线；坏候选只改动一张票的发票号前导零，评分器仍拒绝。
- `unknown` 的 16 个安全失败来自 risk 样本缺少 `review_required=true`；未删除分母。
- 报告含 `real_model_evaluated=false`、`production_ready=false`。

## 证据

| 内容 | 路径 |
|---|---|
| 语料 | `nexus_backend/tests/fixtures/imp07_eval/corpus.json` |
| 生成器 | `scripts/imp07_build_corpus.py` |
| 评分器 | `tools/imp07_eval/__init__.py` |
| 运行脚本 | `scripts/imp07_run_eval.py` |
| 契约测试 | `nexus_backend/tests/test_imp07_eval.py` |
| 报告 | `tmp_test/imp/imp-20261106-a/imp07/{good,regressed,unknown}/report.{json,md}` |
| 汇总 | `tmp_test/imp/imp-20261106-a/imp07/summary.json` |

## 剩余问题

- 合成语料覆盖版式/旋转/低对比度，不等于真实扫描件、手机拍照、发票版式长尾。
- 确定性解析器不是 OCR/模型；真实模型准确率、字段级错误分布、费用/时延未验证。
- 无独立人工复核金标；holdout 未参与本轮选优，样本量 120 在要求区间内但不足以证明生产泛化。
- 真实供应商/真实数据接入仍 pending。
