# N02/N03 同一候选与受控发布预检

本轮只完成本地技术验收工具。工具不连接数据库、平台、模型或通知渠道，不执行迁移、提交、推送、部署和重启，也不读取生产凭据。它不是发布授权工具。

## 1. 两种检查的含义

| 模式 | 必须满足 | 成功结果 | 不代表什么 |
| --- | --- | --- | --- |
| `technical` | 当前完整源码候选的 PG16、PG17 原始 N02/N03 证据全部通过 | `n02_n03_technical_passed` | 不代表全仓、C01、前端浏览器、真实供应商、线上或正式发布已通过 |
| `release` | 技术检查通过、工作区干净、同一候选的人工计划完整 | `local_checklist_complete_for_human_review` | 仅证明本地清单完整，不能证明清单中的远端平台状态、备份或批准是真实的 |

所有结果恒定包含：

```json
{
  "scope": "n02_n03_only",
  "deployment_authorized": false,
  "remote_state_verified": false,
  "approval_verified": false,
  "evidence_trust": "local_reports_not_signed_attestations"
}
```

本地 JUnit 和 JSON 不是数字签名证明。检查器可发现不完整、漂移、矛盾或损坏的证据，不能防御攻击者同时伪造整个工作区与全部报告。需要受信 CI 工件和人工发布审批作为后续边界。

## 2. 完整候选身份

新 `scripts/release_candidate.py` 独立于冻结的 AK05 指纹；未修改旧 AK05/C01 契约。

```json
{
  "schema": "release-candidate-v1",
  "head": "Git 完整 HEAD",
  "source_files": 0,
  "source_sha256": "源码清单摘要",
  "migration_files": 0,
  "migration_sha256": "迁移清单摘要"
}
```

清单来自 Git 跟踪文件和未忽略的新文件，包含前端、网关、后端、测试、脚本、CI、部署配置、依赖锁、静态资源、运行配置与源码内提示词。逐文件使用原始字节 SHA-256，再对有序的相对路径/摘要清单计算摘要；跟踪文件删除也会改变摘要。根级 `.env.*.example` 模板参与摘要，本地环境文件、凭据文件、生成缓存和 `tmp_test/` 不读取、不记录。`docs/` 的人工报告不参与源码摘要。

未提交代码可以有明确的本地候选身份，并通过 `technical` 检查；`release` 另要求整个 Git 工作区干净。不能通过新建提交后沿用旧 HEAD 的报告。前端、依赖、配置、用例、迁移任一变化都要求重新生成两版 PG 证据。

原始字节摘要不会消除 CRLF/LF 差异；不同字节的检出不能混用证据。两版 CI 使用相同检出配置。`migration_sha256` 包含仓内迁移清单，PG 报告 `migrations.count` 则只统计 PostgreSQL `NNN_*.sql`。

## 3. 本地技术检查

先使用统一门禁在两个独立本地库产生证据，配置与凭据留在报告目录外。统一门禁的操作说明见 [N02/N03 PostgreSQL 门禁](N02_N03_PG_GATE.md)。每个输出目录必须包含：

```text
summary.json
n02.xml
n03.xml
```

然后执行：

```powershell
.venv/Scripts/python.exe scripts/check_release_candidate.py `
  --mode technical `
  --pg16 tmp_test/n02-n03-pg16 `
  --pg17 tmp_test/n02-n03-pg17 `
  --out tmp_test/n02-n03-preflight.json
```

`--out` 只创建一个新的本地结果文件，已存在则拒绝覆盖。失败退出码为 1；成功为 0。没有 `--out` 时只向标准输出打印脱敏结果。检查过程中不自动准备数据库或重跑测试。

检查器逐项验证：

1. 两个报告 schema/status，实际 PostgreSQL 主版本 16/17，持久性三项 `on`，受限角色核验标志。
2. 两份候选身份与当前工作区完全相同；检查前后再次计算，防止边检查边编辑。
3. 当前冻结的 45 个 N02 / 55 个 N03 用例清单摘要一致。
4. 固定路径原始 JUnit 的 SHA-256、每个用例身份、计数和结果。重复、替换、遗漏、额外、跳过、失败、错误、伪造总数均阻断；即使修改了摘要让哈希相符也要重新解析 XML。
5. 全部迁移已核验的标志 `verified`、数量及摘要一致。这个标志来自门禁逐文件查询应用状态；它不是检查器自己访问了数据库，也不是数据库快照签名。
6. 拒绝重复 JSON 键、损坏 JSON/XML、符号链接证据、缺文件和过大证据；DTD/实体声明不被接受。

CI 汇总任务还需要 `verify`、浏览器、core PG、AK05 和 N02/N03 等所需 job 均成功；它们各自的证据范围不可互相替代。`technical` 命令本身不读取 GitHub 状态，不应单独称为“整仓 CI 全通过”。

## 4. 人工发布计划输入

正式发布前可在仓外或被忽略的本地目录编写以下 JSON；不要填写密钥、密码或完整连接字符串。`candidate` 必须直接使用当前候选对象，不能只填 HEAD。

```json
{
  "schema": "controlled-release-plan-v1",
  "candidate": {"schema": "release-candidate-v1", "head": "完整提交", "source_files": 1, "source_sha256": "64位摘要", "migration_files": 1, "migration_sha256": "64位摘要"},
  "reviewed_by": "人工复核负责人",
  "review_reference": "线下审批记录编号",
  "platform_checks": {
    "vercel": {"auto_deploy": "disabled", "evidence_reference": "人工核对记录"},
    "zeabur": {"auto_deploy": "disabled", "evidence_reference": "人工核对记录"}
  },
  "migration": {
    "target_digest": "与候选完全一致的64位迁移摘要",
    "prior_digest": "已核对的旧版本64位迁移摘要",
    "backup_reference": "备份及恢复核验记录",
    "compatibility_review_reference": "旧/新 API、worker 与 schema 前后兼容评审记录"
  },
  "runtime": {
    "frontend_gateway": {"head": "完整提交", "artifact_reference": "Vercel 前端与网关候选制品"},
    "api": {"head": "完整提交", "artifact_reference": "API 候选制品"},
    "worker": {"head": "完整提交", "artifact_reference": "票据 worker 候选制品"},
    "assistant": {"head": "完整提交", "artifact_reference": "助手 worker 候选制品"}
  },
  "rollout_steps": ["经人工确认的依次迁移和服务切换步骤"],
  "rollback_steps": ["经人工确认的停止领取、排空和恢复步骤"],
  "unknown_results_preserved": true
}
```

以上是字段说明，不是可直接通过的假审批模板。自动部署状态为未知、enabled、缺核对引用，或网关/API/任一 worker 制品未绑定同一 HEAD，都会阻断本地清单检查：

```powershell
.venv/Scripts/python.exe scripts/check_release_candidate.py `
  --mode release --release-plan tmp_test/human-release-plan.json `
  --pg16 tmp_test/n02-n03-pg16 --pg17 tmp_test/n02-n03-pg17
```

即使该检查成功，仍须人工核对真实平台自动部署开关、原件/数据库备份、兼容性和停机窗口，并取得本次生产操作授权。工具不读取或验证这些平台状态，不发放上线许可；C01 完整恢复、业务复核与其它发布前置门槛继续保留。

## 5. 本轮红绿与边界证据

先写失败测试：

- 沿用原 AK05 的后端范围指纹去代表全产品候选时，修改 `src/App.tsx`、`package-lock.json`、`vercel.json` 均无法使身份失效：3 failed。这是新增全候选契约的适用范围缺口，未更改 AK05 原职责。
- 新检查器尚未实现时，新增报告/负向契约 32 failed，原指纹修复 3 passed。
- 追加真实反例：未列前端网关制品仍放行；运行配置、网关、第三方代码、Git 属性、示例环境模板、源码内 Markdown 提示词遗漏：7 failed。修复后保留断言。

最终离线回归：

```powershell
.venv/Scripts/python.exe scripts/test_accounting_offline.py `
  nexus_backend/tests/test_release_candidate.py -q
# 55 passed

.venv/Scripts/ruff.exe check scripts/release_candidate.py `
  scripts/check_release_candidate.py nexus_backend/tests/test_release_candidate.py
.venv/Scripts/ruff.exe format --check scripts/release_candidate.py `
  scripts/check_release_candidate.py nexus_backend/tests/test_release_candidate.py
# 通过
```

测试只使用临时 Git 仓库、虚构制品和合成 JUnit。55 项工具测试不代表真实 PG 运行通过；真实 PG16/17 结果必须由本轮同一候选的门禁另行生成并核验。原始本地红绿记录位于 `tmp_test/release-fingerprint-red.xml`、`release-fingerprint-green.xml`、`release-checker-red.xml`、`release-final-red.xml`、`release-checker-final.xml`，这些临时记录不提交。
