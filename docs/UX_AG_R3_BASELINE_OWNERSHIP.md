# UX/AG R3 基线与归属

## 基线

- HEAD：`5c48399a`
- 工作区有上轮 AG01-R1 未提交修改，以及本轮 UX/AG04/AG05 修改。
- 不执行 reset/checkout/stash/clean。

## 文件归属

| 范围 | 文件 | 状态 |
|---|---|---|
| AG01-R1 冻结 | `app/intelligence/provider.py`、`models.py`、`endpoints.py`、`service.py`、`app/assistant/provider.py`、`worker.py`、`app/operations/meter.py`、相关调用/费用测试 | 上轮越界修改仍在工作区，归属未由 AG01 负责人正式接收；本轮不再改 |
| UX 本轮 | `src/App.tsx`、`src/workspace-presentation.ts`、测试、`e2e/ux-r1a.spec.ts` | 允许修改 |
| AG04 | `tools/ai_inference_lab/investigation.py`、`test_ag04_investigation.py` | 允许修改 |
| AG05 | `tools/ai_inference_lab/examples.py`、`test_ag05_examples.py` | 允许修改 |
| C01/迁移/生产配置 | 全部 | 禁止 |

## 交接结论

- 不能声称工作区已隔离；AG01-R1 文件确实被上轮修改。
- 本轮未回退、未整文件替换，保留争议片段。
- AG01-R1 负责人需按片段决定接收/撤销；未完成前不运行依赖该候选的联合验收。
- 本轮 UX/AG04/AG05 不依赖 AG01 费用/attempt 改动。
