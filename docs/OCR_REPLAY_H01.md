# H01 票据字段离线回放与基线对比

日期：2026-10-19  
性质：本地离线实验工具；未提交、未推送、未部署、未连接生产

---

## 1. 范围

本轮只评测：

```text
冻结 OCR 文本 → 结构化字段
```

不重新运行 PaddleOCR，不处理图片，不生成业务草稿，不访问数据库或业务 API，不调用真实模型。

因此：

- **离线工具工程验收：通过**（本地 mock、无网络、无数据库）。
- **真实模型效果验证：未验证**（model=none）。
- **生产接入与发布：未实施**。

这三项必须分开报告。

---

## 2. 代码与数据

| 类型 | 路径 |
|---|---|
| 工具 | `tools/ocr_replay/replay.py` |
| 包入口 | `tools/ocr_replay/__init__.py`、`__main__.py` |
| 合成夹具 | `nexus_backend/tests/fixtures/ocr_replay/` |
| 测试 | `nexus_backend/tests/test_ocr_replay_engine.py` |
| 示例报告 | `docs/ocr_replay_h01_example_report.json/.md` |

工具只依赖标准库和现有纯评分函数：

```python
from scripts.evaluate_ocr_fields import FIELDS, evaluate
```

没有导入 FastAPI、Database、worker、provider 或任何业务服务。

---

## 3. 命令

### 验证数据包

```powershell
python -m tools.ocr_replay validate `
  --manifest nexus_backend/tests/fixtures/ocr_replay/manifest.json
```

### 执行基线

```powershell
python -m tools.ocr_replay run `
  --manifest nexus_backend/tests/fixtures/ocr_replay/manifest.json `
  --policy baseline `
  --out tmp_test/h01/baseline
```

### 执行候选

```powershell
python -m tools.ocr_replay run `
  --manifest nexus_backend/tests/fixtures/ocr_replay/manifest.json `
  --policy candidate `
  --out tmp_test/h01/candidate
```

### 恢复

```powershell
python -m tools.ocr_replay run ... --resume
```

只有数据包、策略、模式指纹一致时才允许恢复；已完成样本不重复执行；`unknown` 状态禁止自动重发。

### 比较

```powershell
python -m tools.ocr_replay compare `
  --manifest nexus_backend/tests/fixtures/ocr_replay/manifest.json `
  --baseline tmp_test/h01/baseline `
  --candidate tmp_test/h01/candidate `
  --out tmp_test/h01/report.json
```

### 一键演示

```powershell
python -m tools.ocr_replay demo --out tmp_test/h01_demo
```

无需密钥、网络或数据库。

### 归档结果模式

```powershell
python -m tools.ocr_replay run `
  --manifest ... --policy candidate `
  --mode archived_results `
  --archived path/to/results.jsonl `
  --out tmp_test/h01/archived
```

归档行必须包含固定 `sample_id`、`status`、`review_required`；缺少/重复/未知样本直接拒绝。不会调用提取器。

---

## 4. 冻结数据契约

`manifest.json` 包含：

- `schema_version`、`dataset_id`、`dataset_version`；
- `data_nature=synthetic`；
- 两家虚构企业及 ledger/book/period；
- sample_id、来源标识、OCR/金标路径和 SHA-256；
- 开发集/验收集；
- 类别及范围外标记；
- 多页单票；
- 同源重复检测（跨 dev/validation 拒绝）。

每个样本：

- 一条样本对应一张明确配对的票据；
- OCR 页有页码和文本 hash 校验；
- 金标单独文件，不传给提取器；
- 多票样本显式标记 `unsupported_multi_invoice`，并转人工。

数据边界：

- 本地 `company_id` 不是生产授权；
- 不证明 RLS；
- 未来真实数据必须走既有授权导出流程。

---

## 5. 基线与候选

固定策略：

- `baseline`：`h01-baseline-v1`
- `candidate`：`h01-candidate-gross-label-v1`

两策略有不同 prompt、版本和请求指纹；候选确实进入执行请求。

候选是手工声明式策略：

- 识别 `价税合计（小写）`；
- 额外做了一个**故意的票号前导零去除**，用于证明关键字段退步会被报告，而不是只报告平均分提升。

因此示例报告是：

```text
status = regression
```

这符合“正确保留基线、允许候选被拒绝”的要求。

没有自动候选生成、递归进化或规则发布。

---

## 6. 证据与错误

实现：

- 证据必须引用当前样本、存在的页码、冻结文本中真实存在的 quote；
- 证据合法性独立统计；
- `raw_text` 不能由模型输出替换；
- 缺字段/null/零严格区分；
- 金额用 Decimal 字符串，拒绝科学计数法/负数/超两位小数；
- 日期校验日历有效性；
- 票号、税号保持字符串；
- 重复 JSON 键、NaN、非法字段拒绝；
- 缺字段不补零，转人工；
- `review_required` 在读取金标前由提取结果和固定校验决定；
- mock 的 `cost_cny=null`，不把零成本当真实免费。

示例候选的票号前导零错误会进入 `regressed_fields` 和 `critical_regressions`，即使全字段一致率没有下降。

---

## 7. 报告状态

支持：

- `experiment_invalid`：manifest/hash/配对/配置无效；
- `incomplete`：缺结果或结果集不完整；
- `regression`：存在关键字段退步；
- `no_improvement`：没有达到改善；
- `promising_offline`：固定样本内值得继续验证。

示例报告为 `regression`。任何状态都不代表发布许可。

固定声明：

> 本报告仅用于离线比较，不授权规则发布、自动审核、记账或导出。

---

## 8. Harness² 参考

参考固定版本：

`https://github.com/google-research/harness2/tree/1cfe254e8d25b15f37048c3730134686abe60335`

本轮实际摘取并接入了两个最小片段，未复制 benchmark、third_party、proposer、默认候选池或执行器：

- `harness2/core/runmark.py` → `tools/ocr_replay/vendor/harness2_runmark.py`
  - 使用 `fingerprint()` 绑定工具树身份；
  - 使用 `mark_complete()` 在运行完成时写入 `.adapt_complete`；
  - 使用 `is_complete()` / `why_not()` 拒绝“状态写成 completed 但没有有效完成标记”的恢复。
- `harness2/core/tree.py` → `tools/ocr_replay/vendor/harness2_tree.py`
  - 只摘取 `normalize_rel()`；
  - H01 在解析 manifest 路径前调用它，拒绝 `..` 和绝对路径。

许可证与来源：

- `tools/ocr_replay/vendor/LICENSE`：Apache License 2.0；
- `tools/ocr_replay/vendor/NOTICE`：commit、源文件、修改说明；
- 两个 vendor 文件保留 Google LLC 的 Apache-2.0 版权头，并在 docstring 中注明来源与修改。

未摘取 `selection.py`：H01 只有固定 baseline/candidate，匿名候选选择会削弱策略透明度，不需要。

参考但未复制的组织方式：

- `core/context.py`：任务、输入、证据、输出、差异分离；
- `core/selection.py`：稳定匿名编号（本工具不采用）；
- `core/spec.py`：适配器与执行器分离。

---

## 9. 测试证据

```powershell
pytest nexus_backend/tests/test_ocr_replay_engine.py `
       nexus_backend/tests/test_ocr_field_evaluation.py -q
# 54 passed
```

覆盖：

- 两家企业运行与范围字段；
- 多页单票；
- 基线与候选请求指纹/prompt 不同；
- 候选修复价税合计；
- 候选票号前导零退步；
- 证据 quote/页码；
- 缺字段/null；
- 非法金额/日期；
- 输入 hash、路径穿越、同源重复；
- 恢复不重复执行；
- 配置变化禁止旧结果复用；
- 归档结果模式；
- 缺结果/重复结果拒绝；
- mock cost 未知；
- 旧评分契约不回归。

**未覆盖：**

- 真实模型调用；
- 真实 PaddleOCR 图片识别；
- 真实企业数据；
- 生产 RLS/授权；
- 独立未见验收集；
- 浏览器/业务审核流程。

---

## 10. 示例结果

- 固定样本：4（A1/A2/A3 为 firm-a，B1 为 firm-b）
- 范围外：1（B1 多票需拆分）
- 基线全字段一致：2
- 候选全字段一致：1
- 候选字段修复：A1/A2 `gross_amount`
- 候选字段退步：A1/A2/B1 `invoice_number`
- 证据合法：4/4
- 状态：`regression`

示例报告：

- [ocr_replay_h01_example_report.md](ocr_replay_h01_example_report.md)
- [ocr_replay_h01_example_report.json](ocr_replay_h01_example_report.json)

---

## 11. 未验证与后续

真实模型实验所需条件：

- 显式本地测试端点；
- 固定模型/参数；
- 输入/输出/请求数/超时上限；
- 不使用生产环境变量、不自动下载、不自动安装；
- 独立未见验收集；
- 真实 OCR 与端到端链路另行验收。

本轮没有修改：

- C01 subledger/迁移/测试/报告；
- 生产路由、数据库、worker、对象存储、配置；
- `EXTRACTION_PROMPT_VERSION` 或默认模型行为。

本轮未提交、未推送、未部署。
