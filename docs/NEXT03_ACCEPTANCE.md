# NEXT03 关键安全边界验收

- 状态：**进行中（本地动态探测通过；登录限流受身份服务边界限制，未完成）**
- 候选：HEAD `df3fface95d1f69c28e859e89015465955c517b4`
  - `source_files=1796`
  - `source_sha256=2fb65bb665c088f8913a9d53df56b5b1e77d7c656fa0182fc0e256f8f03848f5`
  - `migration_files=316`，`migration_sha256=f07ba1cd…`（未变化）
- 运行：`tmp_test/next/next03-acceptance-20261102-r2/`，`status=passed`，候选前后一致。
- 最终候选补跑（2026-11-03）：同一 7 文件安全回归 **186 passed, 1 warning in 51.48s**，JUnit `tmp_test/next/next03-security-final.xml`；候选 source 1816/`dddef1a8…`、迁移 325/`48fc7b47…`。动态探测仍是 1796 旧候选证据，未在最终候选重接身份服务。
- 隔离环境：真实本地 GoTrue 25511、API 18100、PG17 源（WSL 25496–25499）；未连接生产或他人集群。

## 动态探测（`scripts/next03_security_probe.py`）

证据：`tmp_test/next/next03-security/probe.json`。只记录状态码与布尔值，不写凭据。

| 探测 | 结果 | 断言 |
| --- | --- | --- |
| 无 Bearer 访问工作台 | 401 | 拒绝 |
| 伪造 Bearer | 401 | 拒绝 |
| 本企业账簿读取 | 200 | 允许 |
| 跨企业试算表／报表／月度包／原件 | 均 404 | 不泄露存在性 |
| 外部企业身份读本企业账簿 | 404 | 拒绝 |
| 客户替事务所复核报表 | 403 | 拒绝 |
| 客户读本企业原件 | 200 | 允许（本企业证据可见） |
| 客户读外企业原件 | 404 | 拒绝 |
| 重复复核同一版本 | 409 | 不重复记账 |
| SVG／伪装 PNG／伪装 PDF／含外部实体 XML | 均 422 | 拒绝且不改父记录 |
| 超 10 MB 附件 | 413 | 拒绝 |
| 6000×6000 解压图片 | 413 | 拒绝 |
| 12 次错误密码 | 均 400 | 身份服务拒绝 |
| 伪造 `X-Forwarded-For` 后错误密码 | 400 | 未改变拒绝语义 |
| 连续失败后无关账号正常登录 | 200 | 未任意封禁其他身份 |

## 回归证据

命令（由 harness 执行）：

```text
.venv/Scripts/python.exe -m pytest -p scripts.qa_no_skip -q \
  nexus_backend/tests/test_auth_supabase.py \
  nexus_backend/tests/test_admin_security.py \
  nexus_backend/tests/test_qa_http_failures.py \
  nexus_backend/tests/test_qa_attachment_probes.py \
  nexus_backend/tests/test_attachment_storage.py \
  nexus_backend/tests/test_uploads.py \
  nexus_backend/tests/test_ingestion_service.py
```

结果：`186 passed`，0 failed，0 skipped（`security-regression.xml`）。

覆盖要点：
- `test_auth_supabase.py`：错误签发方／受众、过期／未生效／未来签发时间、缺失标准声明、`none`／HS384／RS384 算法、篡改签名、未知 `kid`、算法混淆、吊销／禁用／删除／匿名用户、未确认邮箱、管理 MFA 声明只取已验证声明。
- `test_admin_security.py`：23 条管理入口要求服务端 MFA；普通业务入口不误加。
- `test_qa_http_failures.py`：认证与来源拒绝仍带 `no-store`／`nosniff`／`no-referrer`／请求编号；数据库故障 GET 为 `database_unavailable`、写请求为 `result_unknown`，不泄露驱动文本、不承诺回滚。
- `test_qa_attachment_probes.py`、`test_attachment_storage.py`、`test_uploads.py`：MIME／扩展名伪装、SVG、XML 实体、尺寸与解压炸弹、路径穿越、对象归属与下载头。
- `test_ingestion_service.py`：回调 token 不写入 URL 或日志；相同 `X-Event-Id`／相同正文返回 duplicate；停用连接不接收。

## 未完成与边界

1. **登录限流未验证**：本地 GoTrue v2.197.0 对密码登录没有 429 限流（12 次均为 400），仓库也没有反向代理限流配置。生产 Supabase 边缘限流属于外部身份服务，本轮未授权、无法验证。不得写成“限流已验证”。
2. **回调签名未实现**：当前数据接入 webhook 是“不透明 token + `X-Event-Id` 幂等 + 256 KiB 上限”，没有 HMAC 签名或时间窗。通知渠道回调尚未接线，按任务书留到 NEXT06 复验。
3. 有限动态探测不构成独立渗透测试或认证；未做真实恶意文档引擎检测，文件头校验不能替代完整恶意文档扫描。
4. 未对生产或他人服务做任何扫描。

## 结论

本地认证态、跨企业隔离、上传边界、幂等冲突与错误脱敏有可复现证据；登录限流和回调签名是明确的外部／后续门槛，因此 NEXT03 不标记为完整通过。
