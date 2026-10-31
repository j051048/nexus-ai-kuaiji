# 助手第四批验收：执行进度与增量续读

2026-10-31。**本地工程验收通过**。未提交、未推送、未上线、未调用真实模型。企业隔离、会计复核后导出保持原约束。

## 完成内容

1. 复用已持久化的任务保存、发送标记、结束事实，新增授权后可续读的 `/events` 接口；每页最多三项，不读取整份快照或回答。
2. 游标绑定企业、私有会话、任务、发起人及已读前缀；每次请求重新检查授权。异常游标不能绕过权限，改变范围不能混入旧进度。
3. 助手抽屉接入增量读取和“查看处理过程”，保留原执行记录详情。断线续读、重复去重、409 从头读取、撤权清空、切企业拒绝迟到响应；旧服务只有路由缺失才回退。
4. 明确“发送标记不等于供应商收到”；超时、中断和发送后取消提示核对，不自动重发。完成状态不代表会计审核或导出。

本批是**已有任务事实的只读投影**，没有新增事件表、工具步骤、SSE 或未校验模型文字流；没有增加模型调用、费用或重试分支。

## 失败测试与修复证据

- `tmp_test/assistant-events-red.xml`：初始 9 项因缺少 `AssistantService.events` 失败；另 1 项 HTTP 测试被禁止网络夹具误伤 Windows asyncio 的回环 socket，属于测试环境问题，不算产品缺陷。修正为仅允许回环后完成 HTTP 验收。
- 后续修正一个夹具中不存在的 `outsider` 身份，使用已有企业员工和事务所管理员验证私有会话；真正跨企业与撤权另由 PG 动态测试验证，没有扩大权限。
- 前端契约测试先因缺少 `assistant-events` 模块失败，再补解析器；最终新增 16 项单测通过。
- `tmp_test/assistant-events-browser-red.log`：实际组件中找不到“助手执行进度”，复现尚未接线。
- `tmp_test/assistant-events-browser-r1.log`：7 passed、2 failed；进度展开和原详情按钮同名导致歧义。将进度入口改为“查看处理过程”，没有删除原详情入口或放宽断言。
- 最终浏览器 r2：11 passed，0 failed，无重试。早期失败产物保留，不覆盖旧报告。

## 最终回归

| 范围 | 结果 |
| --- | --- |
| 统一离线基线，含新增 12 项进度测试 | 300 passed，0 skipped，143.22s |
| 既有对话、Provider、调用限额、usage 与门槛兼容 | 160 passed，0 skipped，53.30s |
| PostgreSQL 16.15，受限 API/worker | 38 passed，0 skipped，49.83s |
| PostgreSQL 17.11，相同必验清单 | 38 passed，0 skipped，49.79s |
| 前端全部单测 | 105 文件 / 1,092 passed |
| 构建预览真实浏览器 | 11 passed，36.4s，含 1440px 桌面和 390px 手机视口 |
| TypeScript、ESLint、相关 Python Ruff、diff 检查 | 通过 |

各组有重复覆盖，不将次数相加冒充独立业务场景数。现有 Starlette/Node 弃用提示未隐藏。手机是 Chromium 视口验收，不是 iOS 真机验收；未运行远端 CI。

冻结的六类交付任务安全门槛仍为 6/6；正常、缺资料、冲突回答完整性仍为 3/3。输入、金标、分母与评分口径未变化。

新增 PG 证据包括：运行中读取发送标记、终态续读与新服务重放；多次读取前后任务行与费用行完全一致；attempt 保持一次；未知结果不重发；跨企业游标拒绝、私有会话拒绝、撤权先于游标解析。前轮版本失效、两进程竞争、发送后进程崩溃、费用与复核后导出门槛继续通过。

浏览器覆盖：网络 503 后保留游标恢复；终态才重新读取完整工作区；零 POST 模型重发；错企业、409、403 清理与恢复；A 企业迟到响应不污染 B 企业；刷新后从数据库重读；旧路由兼容及原轻量进度回归。两个尺寸截图已经人工查看，进度内容可展开、无横向溢出。

## 同候选与证据

- HEAD：`9b4a05c3104ec42979a1d0792348db2643a797a9`，另含本线程前四批未提交内容。
- 最终完整源候选：`e995fef4808339edf133a5cf8a81400b06851a63652bba657b4e1550c7004437`，1599 个文件。
- 迁移摘要：`26e9be85529136507e36a2fe5b25e7a740274d8679cc635302601d596457275a`，与前批一致。
- 离线候选：`assistant-durable-milestones-v4`；离线指纹 `a0e5283ce857db94433c5295c9b981e3e9cf88460d3d86fa1b65064ebebc6e72`。
- PG 必验清单 v3：38 项；检查器拒绝跳过、缺失、替换或重复用例。

最终证据：

- `tmp_test/assistant-events-baseline-final/`
- `tmp_test/assistant-events-pg-final/`
- `tmp_test/assistant-events-compatibility-final.xml` 与 `.log`
- `tmp_test/assistant-events-vitest-final.log`
- `tmp_test/assistant-events-browser-r2.log`
- `tmp_test/assistant-events-final-candidate.json`
- `artifacts/assistant-events-1440.png`、`artifacts/assistant-events-390.png`
- `tmp_test/assistant-events-environment-stop.json`

[JSON 汇总](ASSISTANT_DURABLE_PROGRESS_ACCEPTANCE_20261031.json) 记录结果、候选与产物 SHA-256；原始日志/截图为本机产物，不含配置密码或数据库连接。PG 门禁运行前后及最终汇总再次比对完整候选，保持一致。

## 归属与运行边界

对上一批保留的文件摘要逐一比较：既有后端运行文件只新增修改助手 `router.py`、`service.py`，新增投影 `events.py`。C01、账务内核、Worker、Provider、费用、迁移源码保持上一批内容。前端仅助手组件、类型、样式与新的进度解析/读取/展示模块；没有改变首页或导航。

只使用本线程 `25616/25617` 专用隔离数据库，两版本的 168 项 PG 迁移 checksum 一致、9 张相关表 FORCE RLS、持久化设置开启、业务角色无 superuser/BYPASSRLS。验收后已停止本轮专用集群及浏览器测试服务，保留虚构数据和证据。没有操作其他智能体工作区或生产服务。

实现说明与复跑方式见 [执行进度说明](ASSISTANT_DURABLE_PROGRESS.md)。本地通过不等同于真实模型质量、生产容量或上线批准。
