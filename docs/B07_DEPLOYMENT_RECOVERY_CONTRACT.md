# B07 部署、恢复隔离、长事务与性能契约

日期：2026-10-18
性质：本机工程基础；不代表云部署、真实私有部署、离线部署或生产恢复已验收

---

## 1. 前置条件表

| 条件 | 状态 | 证据/缺口 |
|---|---|---|
| B01 原件登记/审核边界 | 部分 | 8 类原件注册表、恢复守卫、SQLite 测试；真实对象存储未验收 |
| B01 外部接入关闭 | 已隔离 | 代码级关闭、旧 URL 无副作用 |
| B02 长期账套/期初 | 部分（SQLite） | PostgreSQL/RLS 未验收 |
| B03 正式凭证/账簿 | 部分（SQLite） | PG/并发/红字/拆分未验收 |
| B04 子账/报表/月结 | 部分（SQLite） | 正式发布/UI/跨年未验收 |
| B05 端点/外发政策 | 部分（SQLite） | legacy 企业、真实模型未验收 |
| B06 角色首页/复核 | 部分 | 本机 E2E；真机/上传大文件未验收 |
| 真实干净数据库 | 未验证 | 无 Docker/PostgreSQL/psql |
| 私有真实身份 | 未验证 | 当前只有 Supabase/本地模式 |
| 完全离线 | 未验证 | 未做断网环境 |

---

## 2. 部署拓扑

云路径保持不变：

```
Vercel 前端/网关
  → API（受限 API DB 账号）
  → PostgreSQL
  → Supabase Auth（生产身份）
  → 私有原件存储
  → 已批准 OCR/模型端点
```

本地/私有模板：

- `api`：FastAPI，无内嵌 worker，受限 API DSN。
- `worker`：OCR/建议/批量等持久任务消费者，受限 worker DSN。
- `assistant`：独立助手消费者，受限 worker DSN。
- `migration`：旧账业务迁移 worker，不是数据库 schema migration。
- `maintenance`：投影维护进程，使用独立 maintenance/system DSN；本批新增。
- schema migration：`scripts/migrate_accounting.py`，独立执行，不在 API 启动时自动跑。
- 备份/恢复：独立工具，不常驻。

`docker-compose.demo.yml` 已加入 `maintenance` 角色；`run_accounting_service.py` 支持 `maintenance` 入口。每个角色只配置自己的 DSN；管理员 DSN 禁止进入运行容器。

---

## 3. 身份、存储与配置

- 生产 API 要求 Supabase 模式；local/demo 种子禁止生产。
- 私有部署的真实登录（自托管 Auth/OIDC）未实现，仅保留兼容适配方向。
- 原件只走私有存储；禁止公共 bucket。
- 运行容器禁止管理员 DSN、BYPASSRLS、超级用户。
- 缺配置/角色错误/公开存储应启动失败。
- 示例配置只含占位符。
- `requirements.runtime.lock` 为生产运行时锁；不包含 pytest/ruff 等开发工具。Dockerfile 使用该锁。
- 镜像 digest、SBOM、离线权重清单未完成。

三种承诺必须分开：

A. 云部署：未在真实云验证。
B. 私有应用/数据部署：模板存在，未在容器/真实存储验证。
C. 完全离线：**未验收**；身份 JWKS、依赖、权重、字体、OCR 下载、遥测均未断网核对。

---

## 4. 备份边界

当前备份包含：

- accounting schema 数据库快照；
- 已登记的 8 类原件；
- 数据库迁移版本与校验和；
- 原件 storage_key、范围、大小、哈希；
- 对象版本能力缺失时显式标记 `object_version_supported=false`。

不包含/未证明：

- 认证主体、密码、JWT/JWKS 配置的完整灾备；
- 数据库角色、RLS、函数、列级授权的自动重建（PostgreSQL `--no-owner/--no-privileges` 后未补全）；
- 密钥、证书、域名；
- 已发布报表 PDF/派生文件；
- 队列/调用/费用全链路恢复；
- 完整系统灾难恢复。

因此只能称“accounting 数据包 + 原件恢复”，不能称“完整系统备份”。

---

## 5. 恢复隔离

### 5.1 现有门禁

`restore-verification.json` 仍写入恢复目标；恢复回执声明 serving/outbound/worker 禁用。

### 5.2 B07 加固

- `enforce_restore_hold` 不再只依赖可选环境变量。
- 显式 `ACCOUNTING_RESTORE_HOLD_FILE` 存在则阻断；配置了但文件不存在也**fail closed**。
- 未配置环境变量时，自动检查：
  - 数据库路径父目录；
  - `ACCOUNTING_ATTACHMENT_ROOT` / `ACCOUNTING_STORAGE_ROOT` 父目录；
  - 当前工作目录；
  - 是否存在 `restore-verification.json` 或 `restore-hold.json`。
- API 启动、独立 worker、统一 worker、助手、维护进程均接入。
- 恢复副本不会因忘记设置环境变量而静默启动。
- 正常未恢复实例不会因无关目录旧文件被误锁；检测只匹配配置的数据库/原件目录或显式标记。

### 5.3 放行

移除恢复标记/恢复环境变量并重新启动，作为显式人工放行。必须完成：

- 在途/未知任务核对；
- 费用/外部回执核对；
- 权限/身份核验；
- 原件授权读取；
- 业务写入、复核、导出验证。

旧 token/旧进程写入限制未在真实网络环境验证。

---

## 6. 上传长事务

原实现把对象 `put/read`、哈希、解析放在数据库写事务内。B07 改为：

```
短读事务：鉴权 + 读取 upload_intent + 版本/过期
事务外：对象 read/put、哈希、格式校验
短写事务：重新鉴权 + 状态/过期校验 + 条件 UPDATE
事务后：仅对声明为 pending 的失败对象做归属清理
```

- 对象存储与数据库没有共同事务；以不可变 storage_key、条件更新、幂等状态处理中间态。
- 同一上传并发只有一次有效 `pending→stored`；失败清理只在成功 claim `discarded` 后执行，避免删除并发赢家对象。
- 旧结果在权限/状态变化后不能提交。
- 仍有孤儿对象可能（对象写入成功但最终 DB 提交失败）；保留供审计/清理，不容忍重复绑定。

---

## 7. 性能基线

本地脚本：

- `scripts/benchmark_workbench.py`：合成 summary 页，单进程 SQLite。
- `scripts/benchmark_processing.py`：受控本地 HTTP 供应商，测并发重叠，不测真实模型。

B07 实测（仅本机、合成）：

- workbench：2 本书/20 sources/40 lines，4 SQL，summary 25.54 ms。
- processing：2 requests/50ms 合成延迟：
  - concurrency 1: 0.648s, 3.09 req/s, p95 344.4ms；
  - concurrency 2: 0.334s, 5.99 req/s, p95 327.8ms；
  - concurrency 4: 0.351s, 5.70 req/s, p95 349.8ms。

这些不是首页/助手端到端 p95，不包含网络、真实模型、PostgreSQL、浏览器渲染。不能据此声称 200–500 家企业容量。

---

## 8. 可观测性与升级

- 保留现有 telemetry/observability；未新建平行监控。
- 健康探测区分 liveness/readiness；worker health 只表示进程可用，不是业务健康。
- 备份失败、恢复点、队列积压、未知费用仍需运营面板展示；本批未新增完整告警。
- 应用/迁移/worker 版本矩阵、扩展-迁移-收缩流程未完成。
- 数据库迁移仍由独立脚本执行，API/worker 不自动迁移。
- 回退镜像不等于回退财务事实；破坏性变更不能保证无条件回滚。

---

## 9. 明确不实施

- Kubernetes/Kafka/新任务平台。
- 新会计/报表/税务引擎。
- 真实云/私有部署切换。
- 真实模型、OCR、邮件、支付调用。
- 完整离线镜像/权重/断网验证。
- 真实企业迁移或 B08 切换。
