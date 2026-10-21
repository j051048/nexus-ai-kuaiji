# AK05 基线与归属

## HEAD

```text
baf3f414187a198dbde1c41f8a90e42dee2629ca
branch: main
```

## 本轮修改

| 文件 | 用途 |
|---|---|
| `nexus_backend/app/unified_worker.py` | 两槽有界并发、角色级在途、停止排空 |
| `nexus_backend/app/assistant/monitor.py` | 将轻量心跳从告警刷新中拆出 |
| `nexus_backend/tests/test_ak05_unified_concurrency.py` | T01/T05 专项 |
| `nexus_backend/tests/test_stage15_assistant_monitor.py` | 心跳不触发刷新 |

## 未修改

- C01、总账、子账、关期、审核、导出；
- `accounting/database.py`、迁移、PG 投影；
- provider、meter、ContextPack、事实校验；
- 前端。

## 受保护文件核对

上述文件未在本轮修改。工作区其他修改属于 AK01–AK04/R5/PG 前序任务，未回退。

## 分层

- L：本轮有界并发与心跳单元/服务适配；
- D：真实隔离 PG 多进程未执行；
- P：真实供应商/线上未验证。
