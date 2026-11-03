# NEXT06 部署与上线检查（本地模拟范围）

本文件描述通知通道的配置、签名回调和上线前必须完成的外部检查。当前仓库只验证了本地模拟通道（SMTP 测试服务器与 local sink），**没有接通真实供应商**。

## 1. 通道选择

| 环境变量 | 值 | 行为 |
| --- | --- | --- |
| `DELIVERY_CHANNEL` | 空／`disabled` | `DisabledChannel`：只登记待办，不发送 |
| `DELIVERY_CHANNEL` | `local_sink` | 本地证据用，返回 `local-sink:<id>`，不离开进程 |
| `DELIVERY_CHANNEL` | `smtp` | 只允许本机 SMTP 测试服务器（127.0.0.1/localhost/::1） |
| `DELIVERY_SMTP_HOST` | 默认 `127.0.0.1` | 非回环地址会被拒绝 |
| `DELIVERY_SMTP_PORT` | 默认 `1025` | 1–65535 |
| `DELIVERY_SMTP_SENDER` | 默认 `noreply@example.invalid` | 必须含 `@` |
| `DELIVERY_SMTP_BASE_URL` | 默认 `http://127.0.0.1:5173` | 通知中的登录链接，必须为绝对地址 |

SMTP 邮件只包含“月度简报已就绪”的登录提醒，不包含金额、报表、附件或签收链接。客户必须登录后按权限查看与签收。

## 2. 回调签名

- 端点：`POST /api/delivery/provider-callback`（不要求用户 Bearer；只接受有效 HMAC）。
- 密钥：`DELIVERY_CALLBACK_SECRET`，至少 32 字符，从部署密钥管理注入，禁止写入仓库或日志。
- 请求头：
  - `X-Delivery-Timestamp`：Unix 秒。
  - `X-Delivery-Signature`：`sha256=<hex>` 或裸 hex；HMAC-SHA256(key, `timestamp + "." + raw_body`)。
  - `X-Delivery-Event-Id`：供应方事件唯一编号，必须与正文 `provider_event_id` 一致。
- 时间窗：±300 秒；过期或未来时间返回 401。
- 正文：不超过 64 KiB；字段 `notification_id`、`provider_event_id`、`event_type`（`accepted`/`delivered`/`viewed`/`bounced`）、`occurred_at`（含时区 ISO-8601）。
- 幂等：同一事件编号、同一正文哈希返回 200 + `duplicate=true`；同一编号不同正文返回 409。
- 乱序：事件按 `occurred_at` 展示，允许先到 `delivered` 后到 `accepted`，不回退通知状态。
- 边界：回调只写 `delivery_provider_events`（append-only）。它不会改变 `delivery_notifications.state`、不会重发、不会创建 `monthly_delivery_acknowledgements`。客户签收只由客户在登录后完成。

## 3. Worker

```text
ACCOUNTING_DATABASE_URL=... DELIVERY_CHANNEL=... python -m app.delivery.worker --once
ACCOUNTING_DATABASE_URL=... DELIVERY_CHANNEL=... python -m app.delivery.worker --interval 30 --limit 20
```

- 发送前在事务内提交 `sending` 标记，网络调用在事务外，结算使用租约与恢复代次围栏。
- 读超时、写后断连、模糊响应一律记为 `unknown`，不自动重试。
- 明确未发送（连接被拒、5xx、全部收件人拒收）才进入有界重试。
- 进程崩溃后过期租约按 `unknown` 处置，绝不自动补发。

## 4. 上线前外部检查（本轮未执行）

- [ ] 供应商沙箱或生产账号、域名、TLS 证书与凭据（未授权）。
- [ ] 真实供应商回调签名算法与本文件约定逐项比对（需供应商文档/沙箱）。
- [ ] 真实退信（bounce）与投诉事件样本。
- [ ] 生产反向代理限流、WAF 与回调来源白名单。
- [ ] 真实邮件／短信送达与客户签收演练（需真实客户授权）。
- [ ] 以供应商回执证明“接受/送达/查看”，不得用测试服务器接受冒充送达。

## 5. 本地已验证

- `test_dcp07_smtp_channel.py`：9 项，含接受、拒收、DATA 后断连、unknown 跨重派、客户不可见内部状态。
- `test_next06_provider_receipts.py`：9 项，含签名、时间窗、幂等冲突、乱序、租户隔离、不产生签收、回调路由、worker 单批发送。
- `test_n03_notifications.py` 48 项；`test_delivery_followups.py` + `test_dcp_notification_investigation.py` 22 项；`test_monthly_delivery_package.py` 20 项。
- PG16/17 `test_n03_notification_postgres.py` 各 22 项通过。

本地通过只代表工程契约，不代表真实供应商或客户送达。
