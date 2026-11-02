# DCP07 验收：通知适配器与送达语义（离线联调）

日期：2026-11-02　状态：SMTP 适配器与分类完成，本地提交；未推送、未上生产、未发送真实通知。

## 范围

N03 已实现两阶段发送（claim → begin 先持久化 → 事务外发送 → fenced finish）、围栏与
unknown 不自动重发。本包只新增一个渠道适配器、把「收件人当前邮箱」接进发送前检查，
并验证「服务器接受 ≠ 客户收到」。

## 交付

| 文件 | 作用 |
| --- | --- |
| `nexus_backend/app/delivery/smtp_channel.py` | loopback-only SMTP 适配器；最小摘要 + 需登录链接；结果分类 |
| `nexus_backend/app/delivery/followups.py` | `channel_from_env("smtp")`（延迟导入避免循环）；dispatch 发送前读当前 `users.email` 注入信封 |
| `nexus_backend/tests/test_dcp07_smtp_channel.py` | 本文件内的真实 SMTP 服务器（socket 级协议），6 例 |

### 适配器规则

- 默认 `DisabledChannel`；`DELIVERY_SMTP_HOST` 只允许 `127.0.0.1/localhost/::1`，否则
  `ChannelDisabled`（生产启用需另行授权，不配真实收件人）。
- 邮件只含：期间、包版本、需登录的包链接、以及「不含金额/报表/附件」说明；无金额、无附件、
  无长期公开下载地址。
- 分类：
  - 连接失败、EHLO 失败、全部收件人被拒、5xx 数据拒绝 → `DeliveryNotSent`（可证未发送，走有界重试）。
  - 4xx、超时、DATA 之后断连 → 抛出非 `DeliveryNotSent` 异常，dispatch 记 `unknown`，不自动重发。
  - 服务器 250 接受 → `sink_written`，`sink_ref=smtp:<Message-ID>`；只代表「通道已接受」。
- 发送前重新读取收件人当前邮箱；新邮箱不会继承旧联系人冻结的包版本授权（包/版本/manifest 仍由
  `_notification_stale` 校验）。

## 证据

| 项 | 结果 |
| --- | --- |
| `pytest tests/test_dcp07_smtp_channel.py` | 6 passed |
| 交付回归（`test_delivery_followups.py` + 本包） | 19 passed |
| 真实协议 | 本地 socket SMTP：成功 1 次接受、拒收 0 条、DATA 后断连 1 次会话且状态 unknown |

覆盖的负向：拒收收件人（可证未发送）、DATA 后断连（不得当作未发送）、缺邮箱、非本机 host、
非绝对链接、unknown 后再次 dispatch 不重复投递。

## 未完成 / 未验证

1. **UI 语义**：页面尚未把 `sink_written` 标成「通道已接受，送达未确认」，也没有「未知→调查」入口；
   后端状态与审计已就绪。
2. **PG16/17**：围栏与恢复复用 N03 既有实现，本包未在 PG 上重跑（WSL 集群在跑，Windows 侧仍连不上）。
3. **真实渠道**：SMTP 只对 loopback 测试服务器；送达率、反垃圾、真实客户体验标 X 未验证。
4. 无回执通道的回调校验：本渠道不支持回执，未造回执。
