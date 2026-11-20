# Batch 08：云原生无状态底座（Redis/S3）与可观测三支柱

- **先读**：`docs/upgrade_85/UPG00_MASTER.md`、`nexus_backend/app/security/rate_limit.py`、`nexus_backend/app/uploads.py`、`nexus_backend/app/observability/`、`nexus_backend/app/main.py`、`scripts/env_doctor.py`
- **前置**：Batch 01 完成
- **提分目标**：消除多副本水平扩展阻碍（内存限流 + 本地磁盘）并补齐大厂标准可观测三支柱（OTel Trace + Prometheus + 结构化日志与告警），推动维度 8、维度 9 双双突破 8.7

---

## 1. 实施任务

### 1.1 限流器无状态分布式化（Redis 后端 + 安全降级）
- 重构 `nexus_backend/app/security/rate_limit.py`：抽象 `RateLimitStore` 协议，保留 `MemoryRateLimitStore` 作为本地/单机默认实现，新增 **`RedisRateLimitStore`**（由 `ACCOUNTING_REDIS_URL` 激活，采用原子 Lua 滑动窗口脚本保证跨实例准确计数）；
- 实现可配置故障降级策略（Redis 不可达时记录降级指标与告警，按配置安全回退本地窗口或熔断）；编写双实例共享存储的跨节点限流测试（使用内存协议桩验证无外部 Redis 亦可运行）。

### 1.2 私有原件对象存储抽象（S3 / OSS 兼容）
- 在 `nexus_backend/app/uploads.py` 之上抽象 `ObjectStorageBackend`：保留 `LocalDiskStorage` 作本地默认，新增 **`S3CompatibleStorage`**（支持 MinIO / 阿里云 OSS / AWS S3 协议，完整保持两阶段 `upload_intents`、SHA-256 与魔数校验契约）；
- 增强 `scripts/env_doctor.py`：检测到多副本生产模式环境变量却仍配置本地磁盘存储或内存限流时，输出明确风险阻断/警告。

### 1.3 可观测三支柱（OpenTelemetry + Prometheus + 结构化故障日志）
- **全链路追踪（Tracing）**：新增 `nexus_backend/app/observability/telemetry.py`，实现 W3C `traceparent` 与 `X-Request-ID` 跨层透传（响应头 ↔ FastAPI 中间件 ↔ 数据库会话标识 ↔ 异步 Worker 作业上下文），支持按 `OTEL_EXPORTER_OTLP_ENDPOINT` 开启 OTLP 导出；
- **标准指标（Metrics）**：新增受权/内网受控的 `/metrics` 端点输出标准 Prometheus 文本格式指标，同时覆盖基础设施与**核心财务业务 SLA**：
  - 基础设施：HTTP QPS、P50/P95/P99 耗时直方图、4xx/5xx 计数、数据库连接池占用、异步队列深度；
  - 财务业务：`accounting_vouchers_posted_total`、`tax_workpapers_generated_total`、`payment_orders_state_total`、`workspace_projection_lag_seconds`；
- **结构化脱敏故障日志**：改造 `nexus_backend/app/main.py` 未捕获异常日志，输出标准 JSON 结构（含 `request_id / trace_id / firm_id / route / method / error_class / sqlstate / safe_location`，并强制经 `redaction.py` 脱敏），彻底解决仅打印 `class=DatatypeMismatch` 无法快速定位的运维痛点。

### 1.4 可插拔告警分发与生产应急 Runbook
- 新增 `nexus_backend/app/observability/alerting.py`：支持 Webhook / 钉钉 / 企微 JSON 负载格式、基于告警指纹的去重与静默窗口、失败退避重试（测试使用本地桩验证，不触发真实外网请求）；
- 编写可落地的生产应急手册 `docs/ops/PRODUCTION_RUNBOOK.md`，覆盖：500 故障秒级定位 SOP、连接池打满与慢查询应急、银行/税局 `unknown` 冻结核查流程、PG 备份还原与回滚检查表。

---

## 2. 验收命令与通过标准

```text
.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_distributed_rate_limit.py nexus_backend/tests/test_storage_backend_contract.py nexus_backend/tests/test_telemetry_and_metrics.py nexus_backend/tests/test_alerting_dispatch.py -q
.venv/Scripts/python.exe scripts/env_doctor.py
```

- 验证故意触发一次未捕获异常时，结构化日志准确包含脱敏后的定位字段（含 `trace_id / request_id / error_class / route`）且绝不泄漏银行卡号/身份证/令牌；
- 验证 `/metrics` 输出合法 Prometheus 格式且业务计数器随操作真实递增；
- 更新 `docs/rework/UPGRADE_85_STATUS.md`。
