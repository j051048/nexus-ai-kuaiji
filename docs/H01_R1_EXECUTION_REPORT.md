# H01-R1 执行报告：离线回放工具正确性收口

日期：2026-10-19  
结论：**H01-R1 离线工具工程验收通过**（仅限 mock/archived、离线工程契约；真实模型效果未验证）

---

## 1. 基线

| 项 | 值 |
|---|---|
| 起始 HEAD | `59764a9b` |
| 最终 HEAD | `59764a9b`（本轮未提交） |
| 分支 | `main` |
| 工作区 | H01 文件新增/修改；C01 既有未提交内容保留 |
| 本轮范围 | `tools/ocr_replay/`、H01 fixtures/tests/docs |
| 未执行 | 提交、推送、部署、数据库、真实模型、生产 |

本轮未修改：

- C01 的 subledger/service、迁移、PG 测试和报告内容；
- 业务 API、权限、worker、数据库、对象存储、生产 OCR；
- `scripts/evaluate_ocr_fields.py` 的评分契约。

C01 的未提交文件仍是外部并行工作，不计入 H01-R1 成果。

---

## 2. 候选文件哈希（SHA-256）

```text
tools/ocr_replay/replay.py
38B3CFAD726261765098DF29EA132308227D0014EAF0B511FF62F4CEFB01E63C

nexus_backend/tests/test_ocr_replay_engine.py
CEA7239826AD3328D4CA3E3D7ED2726E689493861EB62186C2EC09C03015ED7D

nexus_backend/tests/fixtures/ocr_replay/manifest.json
54D8E7F6DE0DBAAA6E1484DBD4478865D8714279F590AAAFEBEEECEE2B6A35DC

tools/ocr_replay/vendor/harness2_runmark.py
771591EB27D582038B8AD24F99E1254939F6FD102FC6CA717F5025ECCD266769

tools/ocr_replay/vendor/harness2_tree.py
A52B16C80D3642276AD361878870077E4BE87807FA9A95990DE57377AA1E15E4
```

---

## 3. R1–R6 修改

### R1 执行器与金标隔离

- 新增 `ExtractionInput` 冻结输入结构；
- `extract()` 只接受 `ExtractionInput`，不再接受完整 sample；
- `to_extraction_input()` 只保留样本身份、OCR 摘要和页面文本；
- 不传 `gold_fields`、`gold_path`、评分结果；
- mock、archived、未来适配器共用该契约；
- T01 用执行器替身捕获实际输入，断言无 gold。

这是接口隔离，不是 OS 级沙箱。

### R2 统一身份校验

- 新增 `validate_result_identity()`，用于新运行、归档、恢复、比较；
- 校验 firm/company/ledger/book/period、sample_id、OCR SHA、manifest SHA、schema、status、review 结构；
- manifest 新增 firm→company/ledger/book/period 完整归属校验；
- `ocr_sha256`/`gold_sha256` 改为必需字段；
- 归档结果不能重新盖章；比较要求同一运行模式。

### R3 证据重新验证

- 比较阶段用冻结 OCR 重新计算 `validate_evidence()`；
- 不再信任结果里的 `evidence_errors`；
- 检查页码、quote、字段类型、非空字段证据覆盖；
- 对金额/日期/标识符检查 quote 是否支持字段值；
- 结果保存原始证据，比较时重新计算。

### R4 严格金额与确定性检查

- 金额先捕获完整 token，再整体校验；
- 拒绝对 `1e2`、`106.009` 做前缀截断；
- Decimal 精确比较；
- 检查 `gross = net + tax`，不一致产生 `AMOUNT_MISMATCH` 并要求复核；
- 非 CNY 明确标记范围外；
- 缺失字段不补零。

### R5 统一评分与差异

- `_same(field, expected, actual)` 接收字段名，不再按值后缀判断金额；
- 金额 `106` 与 `106.00` 等价；
- 票号按字符串比较，前导零保留；
- 缺字段、null、空串区分；
- failed/unknown 不因残留字段计为成功；
- 不支持样本单独统计，不进入支持范围成功率；
- 开发/验收集分别统计；
- 关键字段退步优先于平均分。

### R6 恢复与文件保护

- 指纹加入完整策略配置、真实 mode、归档内容 SHA、代码树指纹；
- state 保存每个结果的内容摘要；
- 恢复时校验状态结果、持久化 results、完成标记；
- 结果和 run.json 落盘后才写完成标记；
- 输出不能位于输入目录或覆盖 manifest/已有报告；
- 使用临时文件和原子替换。

---

## 4. T01–T20 映射

| 编号 | 测试 |
|---|---|
| T01 | `test_t01_extractor_input_contains_no_gold` |
| T02 | `test_manifest_and_two_company_run` |
| T03 | `test_t03_changed_sample_scope_is_rejected` |
| T04 | `test_t04_missing_content_digest_is_rejected` |
| T05 | `test_t05_archived_identity_cannot_be_rebadged` |
| T06 | `test_t06_compare_recomputes_evidence_instead_of_trusting_flag` |
| T07 | `test_t06_t07_evidence_is_revalidated_not_trusted` |
| T08 | `test_t08_strict_amount_does_not_prefix_truncate` |
| T09 | `test_t09_amount_relation_is_deterministic_and_reviewed` |
| T10 | `test_t10_t11_same_uses_field_type_not_value_suffix` |
| T11 | `test_t10_t11_same_uses_field_type_not_value_suffix` |
| T12 | `test_t12_null_empty_and_missing_are_distinct` |
| T13 | `test_t13_failed_residual_fields_are_not_success` |
| T14 | `test_t14_unsupported_and_splits_are_separate` |
| T15 | `test_t15_modified_state_result_is_rejected`、`test_t15_modified_results_file_is_rejected` |
| T16 | `test_t16_archived_content_change_invalidates_resume` |
| T17 | `test_t17_missing_completion_marker_blocks_resume` |
| T18 | `test_t18_output_must_not_be_manifest_or_existing_report`、`test_t18_output_paths_are_protected` |
| T19 | `test_t19_resume_output_matches_fresh_business_results` |
| T20 | `test_compare_reports_regression_and_fixed_field`、示例 demo |

另有：

- 路径穿越/hash 变化；
- 重复/缺失/额外结果；
- 归档缺样；
- 恢复不重复执行；
- 配置变化；
- vendor 完成标记/路径规范化；
- 旧评分器契约。

---

## 5. 测试命令与结果

```powershell
pytest nexus_backend/tests/test_ocr_replay_engine.py `
       nexus_backend/tests/test_ocr_field_evaluation.py -q
# 40 passed
```

同时运行：

```powershell
python -m tools.ocr_replay validate `
  --manifest nexus_backend/tests/fixtures/ocr_replay/manifest.json

python -m tools.ocr_replay demo --out tmp_test/h01r1demo2
```

示例报告：

- [ocr_replay_h01_example_report.md](ocr_replay_h01_example_report.md)
- [ocr_replay_h01_example_report.json](ocr_replay_h01_example_report.json)

状态：`regression`（候选修复 A1/A2 价税合计，但引入 A1/A2 票号前导零退步；不支持 B1 单独统计）。

---

## 6. Harness² 复用

固定来源：

`https://github.com/google-research/harness2/tree/1cfe254e8d25b15f37048c3730134686abe60335`

实际复制/摘取：

- `harness2/core/runmark.py` → `tools/ocr_replay/vendor/harness2_runmark.py`；
- `harness2/core/tree.py` 的 `normalize_rel()` → `tools/ocr_replay/vendor/harness2_tree.py`。

许可证：

- `tools/ocr_replay/vendor/LICENSE`：Apache-2.0；
- `tools/ocr_replay/vendor/NOTICE`：来源 commit、文件、修改说明；
- 文件保留 Google LLC 版权头。

未复制：benchmark、third_party、selection/proposer、模型执行器、默认候选池、费用 ledger。

---

## 7. 未验证与边界

未验证：

- 真实模型效果；
- 真实 PaddleOCR 图片识别；
- 真实企业数据；
- 生产 RLS/权限；
- 独立未见验收集；
- 票据→业务草稿→复核→导出端到端；
- 生产费用。

合成数据不能称为“模型未见验收集”。本轮通过只代表离线工具工程验收，不代表模型效果、生产权限或 C01 通过。

**未提交、未推送、未部署；未连接数据库、生产或付费服务。**
