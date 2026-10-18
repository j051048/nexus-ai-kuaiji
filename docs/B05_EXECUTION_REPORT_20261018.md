# B05 执行报告：可信端点、外发政策、本地兼容与费用边界

日期：2026-10-18
基线：`a9daee5b` + 未提交 B03/B04
性质：本机替身/合成数据技术验证；**未提交、未推送、未部署、未调用真实收费模型**

---

## 0. 结论

B05 已完成可信端点目录、企业外发政策、受控本地 JSON 围栏兼容、无鉴权本地服务、发送前政策校验、缓存政策键和费用事实测试。

**不能宣称 B05 完整通过：**

- 生产未创建 managed policy 的企业会阻断 AI；local/dev 才保留 legacy；批量迁移工具未实施；
- 没有真实受限 PostgreSQL 的预算/政策/并发验证；
- 没有真实供应商/真实本地模型联调；
- 没有逐字段自动裁剪、DNS pinning、完整 SSRF 防护；
- 没有管理 UI；
- B02-B04 的正式报表/记账仍未完成 PostgreSQL/业务验收。

---

## 1. 前置验收表及 B02-B04 功能依赖

| 前置 | 状态 | 证据/缺口 | B05 影响 |
|---|---|---|---|
| 企业/账套/部门/本人权限 | 已有回归 | B01-B04 权限测试 | 政策复用 |
| AI 默认开启/暂停/限额 | 已有 | `test_company_ai_defaults` | 暂停语义保持 |
| 草稿/人工复核/导出 | 已有 | B01-B04 | AI 不绕过 |
| 任务幂等/租约/恢复/费用 | 已有 | assistant/ocr/proposal、`model_costs` | 不重建 |
| 未知结果不自动重试 | 已有 | assistant interrupted/unknown、cost uncertain | 保留 |
| B01 安全/恢复 | 部分 | 恢复守卫/备份；真实恢复未验收 | 外发仍受恢复守卫 |
| B02 科目/期初 | 部分（SQLite） | PostgreSQL 未验收 | 建议只基于当前可见科目 |
| B03 正式凭证/账簿 | 部分（SQLite） | PG/并发未验收 | 正式解释不能超越读取权 |
| B04 报表快照 | 部分（SQLite） | 发布/权限未验收 | 不宣称正式报表解读可用 |
| 真实受限 PostgreSQL | **未验收** | 全批缺 | 预算/政策不能签收 |

结论：B05 独立能力可做隔离验证；依赖 B02-B04 的正式解释/报表解读保持不可用或受限。

---

## 2. 实际完成范围

### 2.1 可信端点目录

- `ai_trusted_endpoints`：协议、主机、端口、路径前缀、服务身份、HTTP/私有地址批准、active/version。
- `ai_endpoint_capabilities`：OCR/记账建议/助手场景、模型、json_schema/json_object、图片、上下文/输出上限、超时、bearer/none、价格版本、处理位置。
- 端点由 owner/admin 创建；企业管理员只选择已批准端点和场景。
- 同名模型不同端点通过 `endpoint_id` 区分。

### 2.2 企业外发政策

- `ai_company_policies` 版本化，历史不可改。
- 字段：managed、local_only、允许场景、允许数据类别、允许端点、原因/更新人。
- 新 API：
  - `GET/POST /api/operations/ai/endpoints`
  - `GET/PUT /api/companies/{company_id}/ai-policy`
- `check_send` 在发送前校验权限、场景、类别、URL/端点、模型、仅本地、鉴权方式。
- 生产未创建 managed policy 的企业阻断发送；local/dev 才保留 legacy env 配置。

### 2.3 本地兼容与严格解析

- `allow_single_json_fence`：只接受唯一完整 ```` ```json ... ``` ```` 外包裹；拒绝多段/第二围栏/前后说明。
- `allow_no_auth`：明确批准的本机无鉴权服务；不发送 Authorization。
- 保留：重复键、截断、拒绝、工具调用、超长/编码/重定向/代理检查。
- 兼容开关默认关闭，不改变常规严格路径。

### 2.4 上下文/缓存/发送点

- OCR 发送前检查政策，并在缓存键加入 policy/endpoint 维度；
- 记账建议发送前检查政策；
- 助手 worker 在真正发送前检查政策；
- 结构化 XML 本地解析不外发；
- 缓存仍按企业/事项权限读取。

### 2.5 规则、发送、费用

- 规则治理复用现有 `learning`：候选/评测/发布/停用/回退，未新建平行规则系统。
- 发送状态/未知结果/租约/费用复用现有 `ai_runs`、`assistant_turns`、`model_costs`。
- 测试确认：解析失败仍保留 usage，费用事实不因建议无效归零；本地无鉴权不转发密钥。

---

## 3. 明确未实施/未验证

| 项 | 状态 |
|---|---|
| 真实供应商/真实本地模型联调 | **未实施** |
| 真实收费调用 | **未实施** |
| 真实受限 PostgreSQL 政策/预算/并发 | **未验证** |
| DNS pinning/完整 SSRF 防护 | **未实现** |
| 逐字段自动最小化 | **未实现**（类别 gating） |
| 端点级密钥引用/轮换 | **未实现**（环境配置） |
| 生产未配置政策 | **已阻断；批量迁移工具未实施** |
| 管理 UI | **未实现**（只有 API） |
| B04 正式报表解读 | **依赖未验收，不宣称可用** |
| 真实会计准确性 | **未验收** |

---

## 4. 修改文件与迁移

**新增**

- `nexus_backend/app/intelligence/endpoints.py`
- `nexus_backend/app/intelligence/policy_router.py`
- `nexus_backend/migrations/sqlite/082_b05_ai_endpoint_policy.sql`
- `nexus_backend/migrations/postgres/107_b05_ai_endpoint_policy.sql`
- `nexus_backend/tests/test_b05_policy.py`
- `nexus_backend/tests/test_b05_api.py`
- `docs/B05_AI_ENDPOINT_POLICY_CONTRACT.md`
- `docs/B05_EXECUTION_REPORT_20261018.md`

**修改**

- `nexus_backend/app/intelligence/provider.py`：单 JSON 围栏、无鉴权本地、可选开关。
- `nexus_backend/app/intelligence/cache.py`：缓存键加入 policy/endpoint。
- `nexus_backend/app/intelligence/service.py`：OCR/建议发送前政策检查。
- `nexus_backend/app/assistant/worker.py`：助手真正发送前政策检查。
- `nexus_backend/app/main.py`：注册 AI policy 服务/路由。

**兼容/回退**

- 新表增量；旧 AI 配置不改。
- 生产未创建 managed policy 的企业阻断 AI；local/dev 才走 legacy；不会静默扩大。
- PostgreSQL 107 未执行。
- 真实回退未验证。

---

## 5. 测试命令与结果

```powershell
$env:PYTEST_PLUGINS='tmp_test.pytest_plugin'
.\.venv\Scripts\python.exe -m pytest `
  nexus_backend/tests/test_b05_policy.py `
  nexus_backend/tests/test_b05_api.py -q
```

结果：**7 passed**（6 policy/provider + 1 HTTP）。

AI 回归：

```powershell
pytest nexus_backend/tests/test_company_ai_defaults.py `
  nexus_backend/tests/test_intelligence_provider.py `
  nexus_backend/tests/test_intelligence_http.py `
  nexus_backend/tests/test_stage15_assistant_provider.py -q
# 112 passed

pytest nexus_backend/tests/test_stage15_assistant_meter.py `
  nexus_backend/tests/test_stage15_assistant_actions.py `
  nexus_backend/tests/test_intelligence_workflow.py -q
# 61 passed

pytest nexus_backend/tests/test_stage10_settlement_learning_scope.py `
  nexus_backend/tests/test_stage8_learning.py -q
# 21 passed
```

B01 回归 **72 passed, 4 skipped**；B02-B04 回归 **26 passed**。

**本轮选择回归合计：299 passed, 4 skipped。** 不是全仓测试。

未运行：

- PostgreSQL 107 及真实受限角色；
- 真实供应商/本地模型；
- 真实 DNS/重定向/元数据网络环境；
- 管理 UI/E2E；
- 真实恢复。

---

## 6. 端点、网络与密钥边界

- HTTPS 默认；HTTP 仅显式批准且回环。
- 拒绝元数据/链路本地/组播/保留/未指定地址；私有/回环需 allow_private。
- 拒绝通配符、用户信息、查询串、片段。
- 不跟随重定向、不继承环境代理。
- 无鉴权本地服务不接收 Bearer。
- 端点目录只由 owner/admin 管理；企业管理员不能填任意 URL。

**未解决**：DNS 解析后 pinning/重绑定；当前只验证字面 host/URL，HTTP 客户端会再次解析。该残余风险已列入真实联调前置。

---

## 7. 数据政策、暂停与在途语义

- AI 默认开启保持不变；暂停/恢复/限额复用现有设置。
- 暂停后不再授予新发送许可；已发送/在途请求保留可审计状态，不能承诺撤回或免计费。
- `local_only` 禁止云回退；生产无 managed policy 的企业阻断 AI，需运维迁移。
- 每次发送前重新检查权限、企业状态、AI 开关、政策、端点、预算、任务取消/事实版本。
- 没有持有数据库长事务等待模型响应。

---

## 8. 解析、失败与费用

- 单围栏兼容只做外围去除，不修复 JSON、不挑选多对象、不改金额/科目/日期。
- 严格 schema、重复键、截断、拒绝、工具调用、超长/编码检查保留。
- 解析失败仍保留供应商 usage；`ProviderError.usage` 可进入费用处理。
- 未知用量保持 uncertain，不按零释放；本地 API 零费用不等于硬件/运维零成本。
- 多真实调用费用不能合并为一笔；业务结果最多生效一次。

---

## 9. 规则治理

复用 `learning` 现有能力：

- 候选规则不自动发布；
- 正例/负例/反例评测；
- 发布绑定当前评测；
- 停用/回退保留历史；
- 不自动扩散企业、不改历史凭证、不跳过复核。

本批未新增平行规则系统；回归 21 个 learning 测试。

---

## 10. 指标口径

本轮仅合成测试，不能对外宣称准确率：

| 指标 | 样本 | 口径 |
|---|---|---|
| 严格解析通过 | 合成 JSON/单围栏 | 结构校验，不代表会计正确 |
| 多围栏/冲突拒绝 | 合成恶意响应 | 解析拒绝 |
| 端点政策拒绝 | 合成企业/端点 | 场景/类别/URL/auth |
| 本地无鉴权 | MockTransport | 不发送 Authorization |
| 费用保留 | 合成 usage | 解析失败仍保留 usage |
| 现有未知结果 | 既有 assistant/cost 测试 | 不自动重发 |

未统计真实字段正确率、会计接受率、规则命中率、真实费用。

---

## 11. 后续真实联调条件

- 真实受限 PostgreSQL 107/101 等迁移与 RLS；
- 运维把现有企业迁移到 managed policy；
- DNS pinning/网络层 SSRF 防护；
- 逐字段最小化/脱敏；
- 端点级密钥引用与轮换；
- 真实供应商/本地模型授权与合成到真实的对照；
- 管理 UI；
- B02-B04 正式能力验收后再开放正式报表/账簿解释。

---

## 12. Git 与执行边界

- 当前 `HEAD=a9daee5b`；B03/B04/B05 均为未提交工作区改动。
- 未执行 `git commit`/`git push`/部署/线上迁移。
- 未访问生产数据库/存储/凭据，未调用真实收费模型或外部 OCR。
- 未改变真实企业账本归属。

**B05 是端点/政策/本地兼容的受控原型，不是完整离线部署，也不是真实模型会计准确性验收。**
