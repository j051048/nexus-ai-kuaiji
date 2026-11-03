# NEXT01–NEXT11 验收矩阵

- 状态口径：工程状态（未开始／进行中／本地验收通过／失败／本地阻断）与外部验收（不适用／未授权／缺少环境／已验证）分开。
- 本地证据必须绑定同一候选；`tmp_test/next/<run>/manifest.json` 的 `candidate.before/after` 必须一致（若为 harness 运行）。
- 最终候选：HEAD `df3fface…`，source 1816/`dddef1a88eee0bfdc2c2db37d09560dc0290b434fe800993c05b52b7ba2a963e`，migration 325/`48fc7b47…`。
- PG16/17 只用本轮隔离集群；浏览器只用本地身份、FakeProvider 与 fresh SQLite/测试 PG。

| 工作包 | 本地实现 | 离线验证 | PG16 | PG17 | 浏览器 | 外部验收 | 当前结论 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| NEXT01 候选与资源治理 | 已完成 | final：14+4+3 passed，manifest stable | 不适用 | 不适用 | 不适用 | 不适用 | 本地验收通过 |
| NEXT02 真实浏览器主链 | 单期主链与权限负向完成 | 历史 1794：8 passed | 夹具环境已建 | 8 passed（历史） | Chromium 桌面+手机 8 passed（历史） | 不适用 | 进行中：连续两期/跨年/多银行、最终候选重跑 |
| NEXT03 安全边界 | 本地探测、隔离、上传、幂等 | 动态探测 19 项（1796）；最终回归 186 passed | 未单独重跑 | 探测跑 PG17（历史） | 未新增浏览器用例 | 未授权：生产边缘限流、独立渗透 | 进行中（登录限流/回调签名边界） |
| NEXT04 人工接手时间线 | 仅税审来源；其余适配器未实现 | final：31 passed | 未单独跑 | 未单独跑 | 未做 | 不适用 | 进行中 |
| NEXT05 税务证据闭环 | 回执原件/版本/更正作废/月度包 v2 | final：70 passed + tsc | 月度包 4（历史） | 月度包 4（历史） | 未做 | 不适用（不接真实税局） | 进行中：浏览器缺口 |
| NEXT06 通知上线准备 | 回调签名/事件/worker/部署文档 | final2：66+42 passed，manifest stable | 23 passed（历史） | 23 passed（历史） | local_sink/MiniSmtp 契约 | 供应商沙箱缺失 | 本地验收通过；真实通道未验 |
| NEXT07 弱网与纠错 | 修订迟到响应保护；跨引擎配置 | final：25 passed | 不适用 | 不适用 | 3 引擎+手机 25 passed | 真机/微信/屏幕阅读器未验证 | 本地浏览器验收通过 |
| NEXT08 批量导出交付 | 批量/取消/重试/冻结 manifest | final：SQLite 69 passed | final：10 passed | final：10 passed | 批量开期 1 passed（final） | 不适用 | 本地验收通过；真实对象存储/多进程未验 |
| NEXT09 容量与多实例 | `qa_mixed`/HTTP 脚本 | final：小规模 0 error/守恒 | 未跑 | final：10 公司/1 轮 passed | 未做 HTTP 层并发浏览器 | 生产容量/多节点未验证 | 进行中：100 并发、万行、多实例、资源采样 |
| NEXT10 助手任务效果 | 固定回放/FakeProvider | final2：353 passed，answer 3/3 | 不适用 | 不适用 | 未做 | 真实模型/客户未授权 | 本地离线验收通过 |
| NEXT11 最终联合验收 | 候选冻结与部分联合重跑 | 7 个本地门槛在 final 通过 | 部分 | 部分 | NEXT07/08 在 final 通过 | B/C/D 未完成 | 进行中：N02/N03、完整恢复、外部门槛 |

## 不能相加或不能宣称

- SQLite 69 与 PG 10 有重叠用例，不是 79 个独立业务用例。
- 历史候选（1794/1796/1803/1816-a1ed）的通过结果保留，但不能拼成最终候选的一次验收。
- 模拟通知、FakeProvider、MiniSmtp 不等于真实供应商、真实模型或真实客户效果。
- 同机多进程不是多节点；本地 50 clients 不是生产 50 并发。
- 未推送、未部署、未执行线上迁移、未调用真实模型/通知。
