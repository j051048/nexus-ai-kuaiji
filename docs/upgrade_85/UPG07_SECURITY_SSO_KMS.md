# Batch 07：企业级 OIDC SSO、KMS 信封加密、多租户配额与 CI 安全门禁

- **先读**：`docs/upgrade_85/UPG00_MASTER.md`、`nexus_backend/app/auth/supabase.py`、`nexus_backend/app/auth/admin_security.py`、`nexus_backend/app/security/`、`.github/workflows/accounting.yml`
- **前置**：Batch 01 + Batch 03 完成
- **提分目标**：对标阿里/谷歌级 SaaS 安全合规工程（SOC2 / ISO27001 / 等保三级底座），推动维度 7 突破 8.8、维度 6 突破 9.1

---

## 1. 实施任务

### 1.1 标准企业级统一身份认证（OIDC SSO）与高危操作 MFA（AAL2）护栏
- 保持 `local` 与 `supabase` 模式完全向后兼容，新增 `nexus_backend/app/auth/oidc.py`，支持 `ACCOUNTING_AUTH_MODE=oidc`：
  - 标准 OIDC Discovery + JWKS 公钥缓存与轮换；
  - 严格校验 `iss / aud / exp / nbf / sub` 与算法白名单（拒绝 `none` 及对称降级）；仅从受信任声明映射身份，绝不信任用户可编辑 metadata；
- 新增可配置的高危操作 **`AAL2 / MFA` 强校验守卫**（覆盖资金审批放款、账期锁定、正式报表发布、限时复核委托、全量原件导出），通过策略开关控制；配套本地 OIDC IdP 测试替身验证正常登录、过期 Token、伪造签名、低 AAL 拒绝。

### 1.2 应用层 KMS 信封加密与无停机密钥轮换
- 新增 `nexus_backend/app/security/kms.py`：定义 `KeyProvider` 抽象接口（默认提供标准 AES-256-GCM 的 `LocalEnvKeyProvider`，并预留外部云 KMS / 国密硬件加密机适配器接口）；
- 实现信封加密（随机 DEK + KEK 包裹 + `key_version` 元数据），应用于新写入的高敏感凭据字段（如收款银行账号全号、外部连接器密钥配置）及可选私有原件块加密；
- 编写无停机密钥版本轮换测试：升级 `key_version` 后，历史密文可正常按旧版本解密，新写入与重加密操作自动使用新版本密钥，严禁明文落日志。

### 1.3 多租户配额治理（防单租户雪崩）
- 新增双库前向迁移（SQLite + PG + `FORCE RLS`）建立租户配额表 `firm_quotas`（按 `firm_id` 配置单次导入最大行数、单日异步任务数、原件总容量上限、并发请求限额，附合理默认值）；
- 在导入、异步入队、原件上传入口统一执行配额核验，超限返回清晰 `429 / 413` 并写入可观测指标，防止单一企业大数据导入影响同集群其他企业。

### 1.4 CI 自动化安全扫描门禁（SCA / SAST / 密钥防泄漏）
- 新增静态防密钥泄漏扫描脚本 `scripts/check_secrets.py`（检查高熵密钥、私钥块、硬编码生产 DSN/Token，排除明确标注的测试夹具白名单）；
- 在 `.github/workflows/accounting.yml` 新增独立 `security` 门禁 Job：运行 `check_secrets.py`、Python SAST（`bandit -ll nexus_backend/app`）、依赖安全审计（`pip-audit` 或锁文件漏洞校验、`npm audit --audit-level=high`）。

---

## 2. 验收命令与通过标准

```text
.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_auth_oidc_mfa.py nexus_backend/tests/test_kms_envelope_encryption.py nexus_backend/tests/test_tenant_quotas.py -q
.venv/Scripts/python.exe scripts/check_secrets.py
.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_pg_sql_hygiene.py -q
```

- 新增 OIDC/MFA、KMS 密钥轮换、多租户配额测试全绿；
- `check_secrets.py` 退出码 `0`；静态安全扫描无高危漏洞；
- 更新 `docs/rework/UPGRADE_85_STATUS.md`。
