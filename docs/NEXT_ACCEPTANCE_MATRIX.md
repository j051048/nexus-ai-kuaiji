# NEXT01–NEXT11 验收矩阵

- 状态口径：工程状态（未开始／进行中／本地验收通过／失败／本地阻断）与外部验收（不适用／未授权／缺少环境／已验证）分开。
- 本地证据必须绑定同一候选；`tmp_test/next/<run>/manifest.json` 的 `candidate.before/after` 必须一致（若为 harness 运行）。
- 最终候选：HEAD `0a0c6cab…`，source 1821/`d3f7a1fbd9163d2b4f78d2fe26a39e85aafc3e7a0671993267f7406f61fc2f07`，migration 328/`8bbd6f3f…`。
- PG16/17 只用本轮隔离集群；浏览器只用本地身份、FakeProvider 与 fresh SQLite/测试 PG。

| 工作包 | 本地实现 | 离线验证 | PG16 | PG17 | 浏览器 | 外部验收 | 当前结论 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| NEXT01 候选与资源治理 | 已完成 | final：14+4+3 passed，manifest stable | 不适用 | 不适用 | 不适用 | 不适用 | 本地验收通过 |
| NEXT02 真实浏览器主链 | 57bb 单期主链与权限负向通过 | 8 passed/28.8s（57bb） | 6 passed 跨年（57bb） | 6 passed 跨年（57bb） | Chromium 桌面+手机 8 passed（57bb） | 不适用 | 进行中：连续两期/多银行 |
| NEXT03 安全边界 | 本地探测、隔离、上传、幂等 | 57bb 回归 188 passed；动态探测 19/19 | 未单独重跑 | 探测跑 PG17（57bb） | 未新增浏览器用例 | 未授权：生产边缘限流、独立渗透 | 进行中（登录限流/回调签名边界） |
| NEXT04 人工接手时间线 | 四类来源统一队列；来源业务状态只读投影 | d3f7：15 passed/33.81s | d3f7：28 passed/34.20s | d3f7：28 passed/33.34s | 税审 1 passed/31.8s（cf0fe）；交付包 reviewer 队列+SPA 原记录 1 passed/14.9s（d3f7，脚本在候选外）；批量/通知未验 | 不适用 | 本地工程通过、批量/通知浏览器缺口 |
| NEXT05 税务证据闭环 | 回执原件/版本/更正作废/月度包 v2 | 57bb：70 passed/203.82s + tsc | 月度包 4（历史） | 月度包 4（历史） | **57bb 1 passed/33.0s：登记+上传+下载哈希+更正历史** | 不适用（不接真实税局） | 本地验收通过 |
| NEXT06 通知上线准备 | 回调签名/事件/worker/部署文档 | 57bb：66+42 passed，manifest stable | 23 passed（历史） | 23 passed（历史） | local_sink/MiniSmtp 契约 | 供应商沙箱缺失 | 本地验收通过；真实通道未验 |
| NEXT07 弱网与纠错 | 修订迟到响应保护；跨引擎配置 | d3f7：25 passed/1.4m | 不适用 | 不适用 | 3 引擎+手机 25 passed（d3f7） | 真机/微信/屏幕阅读器未验证 | 本地浏览器验收通过 |
| NEXT08 批量导出交付 | 批量/取消/重试/冻结 manifest | 57bb：SQLite 69 passed/145.71s | 57bb：10 passed/13.07s | 57bb：10 passed/13.14s | 57bb 批量开期 1 passed/28.8s | 不适用 | 本地验收通过；真实对象存储/多进程未验 |
| NEXT09 容量与多实例 | `qa_mixed`/HTTP 脚本 | 50 clients 0 error；100 clients 132 次 queue_full 拒绝 | 未跑 | final：10 公司/1 轮 passed | 未做 HTTP 层并发浏览器 | 生产容量/多节点未验证 | 进行中：100 并发边界已定位（连接池/队列）、万行、多实例、资源采样 |
| NEXT10 助手任务效果 | 固定回放/FakeProvider | final2：353 passed，answer 3/3 | 不适用 | 不适用 | 未做 | 真实模型/客户未授权 | 本地离线验收通过 |
| NEXT11 最终联合验收 | 候选冻结与联合重跑 | d3f7：N04/N07 与 P01 通过；其余包证据在 57bb 同后端候选 | d3f7：N04 28、P01 全 true | d3f7：N04 28、P01 全 true | d3f7：N07 25；交付包 1；N02/N05/N08 在 57bb | B/C/D 未完成 | 进行中：N02 多期、N04 批量/通知浏览器、N09 100 |

## 不能相加或不能宣称

- SQLite 69 与 PG 10 有重叠用例，不是 79 个独立业务用例。
- 历史候选（1794/1796/1803/1817-a1ed）的通过结果保留，但不能拼成最终候选的一次验收。
- 模拟通知、FakeProvider、MiniSmtp 不等于真实供应商、真实模型或真实客户效果。
- 同机多进程不是多节点；本地 50 clients 不是生产 50 并发。
- 未推送、未部署、未执行线上迁移、未调用真实模型/通知。
