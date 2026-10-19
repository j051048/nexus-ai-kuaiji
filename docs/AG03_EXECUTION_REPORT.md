# AG03 执行报告：结构化弃答与字段校验

状态：**离线结构化结果通过**；生产 OCR/建议适配未接线；真实模型未验证。

实现：`tools/ai_inference_lab/answers.py`

- 五种状态：ready / missing_facts / conflicting_evidence / unsupported_case / requires_accountant；
- ready 必须有分录和可核验 claim；非 ready 不得带可应用分录；
- claim 绑定企业、账套、事实版本、单位、币种、证据；
- 来源事实需字段名、值、币种、版本、作用域全部一致；
- `source_gross_total` 禁止改写为收入/利润；
- 计算只允许固定四则，禁止 eval/任意表达式；
- 旧 JSON 可读，未知 schema 版本拒绝；
- 非 ready 只生成离线人工接手材料，不含分录。

测试：`nexus_backend/tests/test_ag03_answers.py`，**9 passed**。

覆盖 C01–C09。未修改前端、历史结果和 OCR 服务。
