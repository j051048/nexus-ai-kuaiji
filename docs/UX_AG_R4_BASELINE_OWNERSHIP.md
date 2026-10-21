# UX/AG-R4 基线与归属

## 基线

- HEAD：`5c48399a` + 未提交本地改动。
- AG01 生产 provider/meter/worker 冻结，本轮未修改。
- C01、迁移历史、PG 投影冲突文件不改。

## 归属

| 文件/范围 | 本轮状态 |
|---|---|
| `tools/ai_inference_lab/investigation.py` | 本轮修改 |
| `test_ag04_investigation.py` | 本轮修改 |
| `tools/ai_inference_lab/examples.py` | 本轮修改 |
| `test_ag05_examples.py` | 本轮修改 |
| `src/App.tsx`、`WorkspaceHome.tsx`、`workspace-presentation.ts` | 前几轮修改，本轮未再改 |
| AG01 provider/meter/service | 冻结，未改 |
| C01/迁移/PG | 只读/未改 |

AG01 修改仍待其负责人接收；不宣布 AG01 或联合接入通过。

## 已知 PG

- 真实迁移 135/134 版本通过；
- 4 个代表套件 79 passed / 1 known conflict；
- 136/137 投影角色试验均因授权/迁移污染回退，未保留。
