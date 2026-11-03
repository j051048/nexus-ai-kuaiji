# CI OCR 深层响应边界修复

本轮基线：`df3fface95d1f69c28e859e89015465955c517b4`。仅本地修改和离线测试；未提交、推送、重跑远端 CI 或操作生产。未改 SQL、权限、费用状态机及其他任务的 NEXT 文件和浏览器配置。

## 原因与修复

CI Python 3.11 的 JSON 解码器对 10,000 层数组抛出 `RecursionError`。独立 OCR 的 `_recognize` 未捕获该异常，因此上层将其归为 `processing_failed`，而不是带有“字段模型尚未调用”语义的 `ocr_response_invalid`。本机 Python 3.14 原用例通过，不能代替 CI 版本验证。

在 OCR 响应解析边界补获 `RecursionError`，继续走原有受控失败及未调用字段模型的费用释放路径。没有扩大通用异常捕获，没有把模型发送后的未知费用当成未发生。

原深层 JSON 集成用例保留，并增加强制解码器深度溢出的参数分支，使本机较新 Python 也能覆盖该异常。两分支均核验：只有一次 OCR 请求、无字段模型请求、失败分类准确、费用预留已释放。

## 红—绿证据

证据目录：`tmp_test/ci-fix-20261102/`（本地忽略目录）。

| 验证 | 修复前 | 修复后 |
| --- | --- | --- |
| Python 3.11.15，原始深层 JSON 用例 | 1 failed，原样复现 CI | 纳入整组通过 |
| Python 3.14.1，深层响应两个分支 | 1 failed / 1 passed | 纳入 OCR 组通过 |
| Python 3.11.15，用户日志中完整 QA 命令及无跳过插件 | CI 原结果 1 failed / 210 passed | **212 passed / 0 skipped**，152.44 秒 |
| Python 3.14.1，完整 `test_paddle_execution_boundaries.py` | — | **14 passed / 0 skipped**，11.45 秒 |
| 修改文件 Ruff check / format，git diff check | — | 通过 |

JUnit：`ocr-red-311.xml`、`ocr-red-314.xml`、`qa-green-311.xml`、`ocr-green-314.xml`。唯一警告为已有 Starlette/httpx 弃用提示。

修复文件 SHA-256：

- `nexus_backend/app/intelligence/ocr.py`：`C2190FD1E5B68FE6A419F8D7661B0C6F23C5B606625A84B19A24368FF4139119`
- `nexus_backend/tests/test_paddle_execution_boundaries.py`：`ED616434F57E2C5C1D7BD259ED5FF88D1DD72C57858C86A8262C885644CD4461`

## 其他 CI 红灯的独立核对

只读检查同一提交的远端日志及下载的验收工件，不能用本次 OCR 修复宣称整条流水线已通过：

- Accounting run `41434025176`：AK05 核心四组和月度交付通过，但其后的 C01 门禁达到 300 秒超时；该日志不足以断言根因。
- 同 run 的 N02/N03：N02 45 项通过；N03 53 项通过、2 项失败。直接失败为通知 worker 读取 `users.email` 被数据库权限拒绝；另一个慢通知用例未到达发送入口。不能通过给 worker 全表读取权限修复，应另做受通知身份、租约和当前收件人约束的投影与 PG 回归。
- P01 run `41434025035`：151 项回归、2 项联合业务恢复已执行通过；随后收集恢复证据失败，提示每个 PG 版本应只有一份记录。Linux pytest 的 `current` 符号链接重复计数是待复现假设；清理还报告 `bridge_stop_timeout`，需区分监听进程与端口残留状态。
- Accounting 浏览器核心门禁有多个独立失败，涉及旧权限断言、界面文案和恢复场景。企业财务不应为满足旧测试而重新获得事务所正式复核权限。QA workflow `41434025222` 的 Chromium、Firefox、WebKit、mobile-webkit 四个浏览器任务均已通过。
- 本轮尝试的单条本地岗位浏览器测试在 dev warmup 导航超时，尚未进入用例，不计为浏览器通过。该次服务已退出。

并行任务正在修改 `vite.config.ts`、`playwright.p02.config.ts` 及 NEXT 验收文件，且占用本地恢复集群。以上改动及环境均未接管或覆盖。后续修复其他红灯须沿各自证据继续，不能把不同候选的通过结果拼成发布通过。
