# NEXT09 验收记录：HTTP、多实例与持续容量

- 工程状态：**进行中（本地小规模 PG17 通过；未完成 100 并发、万行导入、多节点/长时间稳定性）**。
- 外部验收：未验证（生产容量、跨节点网络故障未授权/无环境）。
- 候选：head `df3fface…`，source `1816`/`dddef1a88eee0bfdc2c2db37d09560dc0290b434fe800993c05b52b7ba2a963e`，migration `325`/`48fc7b47…`。
- 最终候选重跑（ddb）：`tmp_test/next/next09-capacity-ddb/report.json`，`status=passed`，候选前后一致（1816/`ddb…`）；三阶梯 0 error/0 admission rejection，读 p50 61.97/187.25/458.08ms，导入 p50 100.07/265.49/696.75ms；持续轮 5/20/50 clients 读 p50 56.25/211.54/498.38ms、导入 p50 67.48/289.42/787.94ms；两个 worker 各 5 项成功，`new_books=10`、`duplicate_books=0`、`open_periods=10`，`review_export_isolation=true`；`database_cpu_measured=false`。
- 环境：Windows 32 vCPU；WSL 隔离 PG17（端口 25497）；单个 API 进程、连接池 `pool_max=4`；同机两个 worker 子进程；`companies=10`、`rounds=1`、导入为测试生成流水。**同机多进程不是多节点。**

## 命令与结果

```text
.venv/Scripts/python.exe tools/capacity/qa_mixed.py \
  --config tmp_test/next02-env/runs/4fb7aceea75d4232a2aaeb99c00705c9/pg17-recovery_source.json \
  --major 17 --companies 10 --rounds 1 \
  --out tmp_test/next/next09-capacity-pg17
```

- exit 0，`status=passed`，`acceptance_mode=all_requests_completed`；报告 `tmp_test/next/next09-capacity-pg17/report.json`。
- 三个阶梯均 **0 error / 0 admission rejection**：
  - 5 clients / 10 req：read p50 64.94ms、p95 100.15ms；import p50 88.83ms、p95 107.89ms。
  - 20 clients / 40 req：read p50 188.03ms、p95 203.77ms；import p50 254.35ms、p95 327.86ms。
  - 50 clients / 100 req：read p50 464.92ms、p95 509.45ms；import p50 737.79ms、p95 905.97ms。
- 第二轮持续阶段 50 clients / 100 req：read p50 931.34ms、p95 1072.18ms；import p50 1467.16ms、p95 1835.61ms；仍 0 error，但 latency 明显上升，是单进程/4 连接池下的排队，不是容量通过。
- 连接池：max 4、peak active 4、lock waiters 1；`database_cpu_measured=false`，因此**没有数据库 CPU 证据**，不能用应用 CPU 代替。
- 并发 worker：2 个进程各处理 5 个任务，全部成功；`items_by_status.succeeded=10`、`new_books=10`、`duplicate_books=0`、`open_periods=10`，账务守恒无重复。
- 复核/导出隔离由 capacity 工具内 `review_export_isolation` 检查（不合格即失败），本报告通过。

## 未完成/不得宣称

- 未测 100 并发、万行流水导入、批量已复核导出、大附件下载、多 API 实例、单账套热点、持续数小时资源增长、跨节点网络故障。
- 未收集 PG CPU/内存/连接数/锁等待的服务端采样（仅应用侧 pool/lock 计数）。
- 未与 QA05 旧候选基线做同环境对照；不能用本报告证明“扩展 QA05 边界”。
- 真实浏览器只做了 NEXT08 的批量开期，不是 HTTP 层 50/100 并发。

结论：小规模 PG17 全成功，可作为 NEXT09 的起点证据；**NEXT09 尚未达到任务书完成门槛**。
