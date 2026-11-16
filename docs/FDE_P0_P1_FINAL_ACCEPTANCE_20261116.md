# FDE P0/P1 本地验收（2026-11-16）

## 候选

- HEAD：`50893961edcf85a62238d9f8a7b0acfa980702c8`
- source：2196 / `9256af545b018fb0ce090834ab9186bfcc34ce1df1e40e57f1155ea0c1906835`
- migration：386 / `86b11b3b90cd79e57e0f73c5e4c13d3121dc1ff83b3850081d10d6f83789d1d5`

## P01

`tmp_test/fde-p0/p01-final2/reports/summary.json`：**passed**，run `65f4e6ec32fa40999e0c4c4c3125b834`。

| 阶段 | 通过 | 跳过 | 耗时 |
|---|---:|---:|---:|
| regression | 188 | 0 | 168.621s |
| joint | 2 | 0 | 88.070s |
| identity | 4 | 0 | 58.599s |

PG16 16.15 / PG17 17.11，migrations 229，restored capability grants 1020，九项 `p01_checks` 全 true。

首次 `p01-final` 因“Candidate changed during acceptance”失败；失败报告保留，未覆盖。新 pair 重跑通过。

## 已完成（本地）

- P0-1 候选/恢复（最终 P01）
- P0-2 上线就绪度评分与阻断摘要
- P0-3 L1/L2/L3 路由与 `ai_route_decisions`
- P0-4 价值基线（四率、系统耗时、AI 单笔成本）
- P1 持续结账雷达
- P1 确定性偏差归因（本地/HTTP/PG/浏览器）
- P1 FDE 适配器目录
- P1 共享中心 SLA 告警
- P1 事务所级撤员
- P1 安全闭环本地矩阵
- P1 固定路由评测 v1 21/21、v3 10/10

## 外部门槛 / 未完成

- **P0-5 真实 T+ 模板认证**：无客户 54 列模板与授权；现有仅为合成适配，不得写成已接通。
- 真实客户数据、真实模型、真实通知、真机/人工屏幕阅读器、远端 CI、生产发布均未验证。
- 未提交、未推送、未部署。
