# RSI 离线比较

候选已冻结；验收集不参与选优。真实模型效果未验证，不授权发布。


## e26ad9210dd241cfb9640b4131218fc73b2579a44cb63a451f4988e22b2daa7d

# 样例评测报告

- 固定分母：2
- 数据集 SHA-256：f84551f8f29eb145a7a1b11b109e2cee9a1e25389e7e17764fa8926b1abf507c
- 基线配置：e1d562f3b363804d0916c68e7fdba8b8c29357544970c231f6efa6ad90532f7f
- 候选配置：489e4bc5dd5de095df81b55b0ddf4c7f951314378f9e79d7a5cddfb6453b09a5
- 相对结论：regression
- 最终验收：rejected
- 候选正确率：0.5
- 绝对安全门槛失败：True
- 缺点数：0；未知数：0
- 基线缺失：[]；基线未知：[]
- 风险统计：{'tp': [], 'tn': ['A1', 'A3'], 'fp': [], 'fn': [], 'unobserved': [], 'correct_abstentions': []}

| 样本/企业/账套/期间 | 基线 | 候选 | 风险真值 | 原因 | 来源/输入/金标摘要 |
|---|---|---|---|---|---|
| A1 / company-a / book-a / 2026-10 | succeeded / correct=False | succeeded / correct=False | False | field_incorrect:gross_amount, risk_false_positive, field_incorrect:invoice_number, field_regression:invoice_number | ed009b7fcaba641c9c3387066a617b1901f09e46b681f9c370bbe06646ced8bf / ed009b7fcaba641c9c3387066a617b1901f09e46b681f9c370bbe06646ced8bf / a2267dc01d65cb25eea9ff93ba6f4c8e31c6228c196412c8992667f984b0a852 |
| A3 / company-a / book-a / 2026-10 | succeeded / correct=True | succeeded / correct=True | False | ok | 6ec128d7bd5a1cc14fbb3baee7858330d9ecc8592698277ffde5ef9529848140 / 6ec128d7bd5a1cc14fbb3baee7858330d9ecc8592698277ffde5ef9529848140 / 5d74e0b844017f0e5bcdb3489dbaea6d42b7bb0ee5c3d07e1f832a07bafb5b4a |

仅限离线评测；不授权模型发布、会计审核、记账或导出。

## 3fa35778350d28da8fb3ff8af49cfa66cfaf0ce3e9012ee6c86ffc5f9f40daaa

# 样例评测报告

- 固定分母：2
- 数据集 SHA-256：f84551f8f29eb145a7a1b11b109e2cee9a1e25389e7e17764fa8926b1abf507c
- 基线配置：e1d562f3b363804d0916c68e7fdba8b8c29357544970c231f6efa6ad90532f7f
- 候选配置：564c31d7eb7dc42df0c944c4d9f6b507ad0bddc91d5ea65f66187e5ff2202c2e
- 相对结论：improvement
- 最终验收：accepted_offline
- 候选正确率：1.0
- 绝对安全门槛失败：False
- 缺点数：0；未知数：0
- 基线缺失：[]；基线未知：[]
- 风险统计：{'tp': [], 'tn': ['A1', 'A3'], 'fp': [], 'fn': [], 'unobserved': [], 'correct_abstentions': []}

| 样本/企业/账套/期间 | 基线 | 候选 | 风险真值 | 原因 | 来源/输入/金标摘要 |
|---|---|---|---|---|---|
| A1 / company-a / book-a / 2026-10 | succeeded / correct=False | succeeded / correct=True | False | field_incorrect:gross_amount, risk_false_positive | ed009b7fcaba641c9c3387066a617b1901f09e46b681f9c370bbe06646ced8bf / ed009b7fcaba641c9c3387066a617b1901f09e46b681f9c370bbe06646ced8bf / a2267dc01d65cb25eea9ff93ba6f4c8e31c6228c196412c8992667f984b0a852 |
| A3 / company-a / book-a / 2026-10 | succeeded / correct=True | succeeded / correct=True | False | ok | 6ec128d7bd5a1cc14fbb3baee7858330d9ecc8592698277ffde5ef9529848140 / 6ec128d7bd5a1cc14fbb3baee7858330d9ecc8592698277ffde5ef9529848140 / 5d74e0b844017f0e5bcdb3489dbaea6d42b7bb0ee5c3d07e1f832a07bafb5b4a |

仅限离线评测；不授权模型发布、会计审核、记账或导出。

## 独立验收

# 样例评测报告

- 固定分母：2
- 数据集 SHA-256：3fabb1dd42a1a2ca75ac37e25dcd5a4802c507ed7c8a930553d90d1887015a1d
- 基线配置：e1d562f3b363804d0916c68e7fdba8b8c29357544970c231f6efa6ad90532f7f
- 候选配置：564c31d7eb7dc42df0c944c4d9f6b507ad0bddc91d5ea65f66187e5ff2202c2e
- 相对结论：improvement
- 最终验收：accepted_offline
- 候选正确率：1.0
- 绝对安全门槛失败：False
- 缺点数：0；未知数：0
- 基线缺失：[]；基线未知：[]
- 风险统计：{'tp': ['B1'], 'tn': ['A2'], 'fp': [], 'fn': [], 'unobserved': [], 'correct_abstentions': ['B1']}

| 样本/企业/账套/期间 | 基线 | 候选 | 风险真值 | 原因 | 来源/输入/金标摘要 |
|---|---|---|---|---|---|
| A2 / company-a / book-a / 2026-10 | succeeded / correct=False | succeeded / correct=True | False | field_incorrect:gross_amount, risk_false_positive | 94cedb4d846334f0ea9fa3c2f70d26f878a2304e3c4d229463f7e1ba45ea0e26 / 94cedb4d846334f0ea9fa3c2f70d26f878a2304e3c4d229463f7e1ba45ea0e26 / 8c7e60a5cc0e660d42dcbf4f4f5b828ab12a13c878a87ae5f0f19bb96b45a4d2 |
| B1 / company-b / book-b / 2026-10 | expected_abstention / correct=True | expected_abstention / correct=True | True | correct_abstention, correct_abstention | c9a7d0a6306820dcc52a3c05116d10c310ea0e5a7a1dbb614ace788a576cbc82 / c9a7d0a6306820dcc52a3c05116d10c310ea0e5a7a1dbb614ace788a576cbc82 / 2c35c7040d90923f6d731614ed32ea5b6e8b97ba3a49037829672f0944974e96 |

仅限离线评测；不授权模型发布、会计审核、记账或导出。
