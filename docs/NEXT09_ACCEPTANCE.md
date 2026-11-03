# NEXT09 验收记录：HTTP、多实例与持续容量

- 工程状态：**进行中（本地小规模 PG17 通过；未完成 100 并发、万行导入、多节点/长时间稳定性）**。
- 外部验收：未验证（生产容量、跨节点网络故障未授权/无环境）。
- 候选：head `0a0c6cab…`，source `1821`/`57bb7b7061c1b30d02ae4a825e411fc2fcf7e0c265e08a4337264b6be701c46a`，migration `328`/`8bbd6f3f…`。
- **57bb 最终重跑**：`tmp_test/next/next09-capacity-57bb/report.json`，`status=passed`；5/20/50 clients 三阶梯 0 error/0 admission rejection，读 p50 70.47/225.93/510.69ms、p95 88.61/238.37/554.55ms，导入 p50 85.50/315.17/783.95ms；持续轮读 p50 62.11/229.15/584.96ms、导入 p50 77.04/312.15/915.00ms；两个 worker 各 5 项成功，`new_books=10`、`duplicate_books=0`、`open_periods=10`；`database_cpu_measured=false`。
- 8475 历史重跑：`tmp_test/next/next09-capacity-8475/report.json`，`status=passed`，候选前后一致（1817/`847518c9…`）；三阶梯 0 error/0 admission rejection，读 p50 70.09/204.18/498.89ms，导入 p50 106.88/285.05/793.37ms；持续轮 5/20/50 clients 读 p50 58.84/210.71/545.64ms、导入 p50 75.04/321.25/867.99ms；两个 worker 各 5 项成功，`new_books=10`、`duplicate_books=0`、`open_periods=10`；`database_cpu_measured=false`。
- 环境：Windows 32 vCPU；WSL 隔离 PG17（端口 25497）；单个 API 进程、连接池 `pool_max=4`；同机两个 worker 子进程；`companies=10`、`rounds=1`、导入为测试生成流水。**同机多进程不是多节点。**
- 历史 ddb 报告 `tmp_test/next/next09-capacity-ddb/report.json` 保留，但不作为 8475 候选依据。

## 命令与结果

```text
.venv/Scripts/python.exe tools/capacity/qa_mixed.py \
  --config tmp_test/next02-env/runs/4fb7aceea75d4232a2aaeb99c00705c9/pg17-recovery_source.json \
  --major 17 --companies 10 --rounds 1 \
  --out tmp_test/next/next09-capacity-8475
```

- exit 0，`status=passed`，`acceptance_mode=all_requests_completed`；报告 `tmp_test/next/next09-capacity-8475/report.json`。
- 三个阶梯均 **0 error / 0 admission rejection**：
  - 5 clients / 10 req：read p50 70.09ms、p95 117.24ms；import p50 106.88ms、p95 130.84ms。
  - 20 clients / 40 req：read p50 204.18ms、p95 218.70ms；import p50 285.05ms、p95 360.01ms。
  - 50 clients / 100 req：read p50 498.89ms、p95 549.97ms；import p50 793.37ms、p95 980.33ms。
- 第二轮持续阶段 5/20/50 clients：读 p50 58.84/210.71/545.64ms、导入 p50 75.04/321.25/867.99ms；50 clients 时 read p95 601.93ms、import p95 1068.99ms；仍 0 error，但延迟随并发上升，是单进程/4 连接池下的排队，不是容量通过。
- 连接池：max 4；`database_cpu_measured=false`，因此**没有数据库 CPU 证据**，不能用应用 CPU 代替。
- 并发 worker：2 个进程各处理 5 个任务，全部成功；`items_by_status.succeeded=10`、`new_books=10`、`duplicate_books=0`、`open_periods=10`，账务守恒无重复。
- 复核/导出隔离由 capacity 工具内检查（不合格即失败），本报告通过。


## 100 并发边界诊断（非验收）

- 为避免改动最终候选，复制 `tools/capacity/qa_mixed.py` 为 `tmp_test/qa_mixed_100.py`，仅把并发上限扩到 100、阶梯改为 20/50/100；公司 10、rounds=1、PG17。
- 报告 `tmp_test/next/next09-capacity-100-diag/report.json`，`status=failed`：20/50 clients 均 0 error/0 rejection；100 clients 时 **132 次 `DatabaseCapacityError`（`queue_full`）**，成功读 51、导入 17，读 p50 381.23ms/p95 679.55ms、导入 p50 756.07ms/p95 785.28ms；PG 采样 max connections 4、max active 4、lock waiters 0，连接池 max 4、队列等待上限 64。
- 结论：本机单进程、4 连接池下，100 并发在**准入队列**处被明确拒绝，不是“全部成功”，也不是数据库锁死；瓶颈是连接池/队列容量，不能通过自动重试或放大连接数掩盖。该诊断脚本不在最终候选内，只作边界定位，不计入 NEXT09 验收通过。

## 未完成/不得宣称

- 未测 100 并发、万行流水导入、批量已复核导出、大附件下载、多 API 实例、单账套热点、持续数小时资源增长、跨节点网络故障。
- 未收集 PG CPU/内存/连接数/锁等待的服务端采样（仅应用侧 pool/lock 计数）。
- 未与 QA05 旧候选基线做同环境对照；不能用本报告证明“扩展 QA05 边界”。
- 真实浏览器只做了 NEXT08 的批量开期，不是 HTTP 层 50/100 并发。

结论：小规模 PG17 全成功，可作为 NEXT09 的起点证据；**NEXT09 尚未达到任务书完成门槛**。
