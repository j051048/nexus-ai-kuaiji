# DCP06 批量导出与交付准备（A 刀：批量准备复核资料）

日期：2026-11-02　状态：A、B、C 编排完成，本地提交；C 的端到端执行未验证

## 结论

先做手册里的 A：把「多家企业的待复核对象 + 阻断」整理成一份可核对清单，**不批准、不过账、不导出、不发送**。
复用既有复核服务读取对象，不新建队列；清单里每一行绑定企业、账套、期间和**逐张凭证的 id + 版本**，
并给出可核对的 `manifest`（64 位 sha256）。B（受控导出）与 C（月度包草稿）仍未做。

## 契约

`POST /api/firm/review-packs`，体 `{company_ids: [...], period: "YYYY-MM"}`（最多 200 家）。

```json
{
  "period": "2026-10",
  "manifest": "整包指纹",
  "ready": 1, "blocked": 2,
  "items": [
    {
      "company_id": "…", "book_id": "…", "book_name": "…", "period": "2026-10",
      "status": "ready",
      "draft_count": 2,
      "drafts": [{"draft_id": "…", "version": 1}, …],
      "manifest": "该企业清单指纹",
      "blockers": []
    }
  ]
}
```

阻断值：`no_book`（该期间没有账套）、`no_access`（没有该账套读取权，不暴露名称）、
`no_drafts`（没有待复核对象）、`draft_limit`（超过单企业 200 张，要求缩小范围而不是静默截断）、
`period_locked`（期间不是 open）。

规则：

1. **事务所侧动作**：调用者必须有事务所复核岗位（owner/admin/reviewer）；客户财务 403。
2. **逐家授权**：每家企业单独过 `access.book(..., 'read')`；无权账套只回 `blocked`，不回对象与名称。
3. **版本绑定**：清单里的 `version` 就是复核预览要求的 `expected_version`，后续导出必须按此版本核对。
4. **不截断**：单企业超过 200 张直接阻断并提示缩小范围。
5. **指纹**：单企业指纹 = sha256(排序后的 `draft_id:version`)；整包指纹 = sha256(各企业 company/book/manifest)。

## 交付物

- `nexus_backend/app/review_packs/{service,router}.py`
- `nexus_backend/app/main.py` 注册 `review_packs` 服务与 `/api/firm/review-packs`
- `nexus_backend/tests/test_dcp06_review_packs.py`（5 例）

## 证据

| 项 | 结果 |
| --- | --- |
| `pytest tests/test_dcp06_review_packs.py` | 5 passed |
| `pytest tests/test_dcp05_handoff.py tests/test_dcp06_review_packs.py` | 12 passed |
| `ruff check` | 通过 |
| `python -c "import app.main"` | 通过（路由注册无冲突） |

覆盖：ready/blocked 混合、无权账套不暴露、期间过滤、新增草稿改变指纹、客户财务 403、
重复企业去重、期间格式校验。

## B：批量受控导出

复用同一张 `batch_operations`（新增 kind `review_export`，迁移 `sqlite/123`、`postgres/194`），
不新建队列、不新写导出器：逐企业调用既有 `AccountingService.export`。

- `POST /api/firm/batch-operations/review-export/preview` → 逐家列出 **已审核** 凭证的 `id+version`、
  单企业 `manifest`、整包 `manifest` 与阻断码（`no_book` / `no_access` / `no_approved` / `draft_limit`）。
- `POST /api/firm/batch-operations/review-export`（202）→ 必须带上预览时确认的 `confirmed_manifest`；
  清单在确认后变化（多批了一张、少了一张、版本变了）返回 409「清单已变化，请重新预览后再确认」。
- 后台逐项执行：
  - 阻断企业直接 `skipped`，不进入导出；
  - 执行前重新读该账套的已审核集合，与清单逐条比较，不一致 → 409，**不导出新集合**；
  - 一致才调用既有导出，结果写回 `export_id / filename / count / manifest`。
- 导出仍受既有守卫：只导出 `approved`、必须有过期复核凭据、账套只读期/锁定期内拒绝。
- 幂等：同一 `idempotency_key` 重放返回同一批次；导出本身按 `batch:{batch_id}:{company}` 幂等。

### 证据

| 项 | 结果 |
| --- | --- |
| `pytest tests/test_dcp06_review_export.py` | 5 passed |
| `pytest tests/test_batch_operations.py tests/test_dcp04_batch_control.py tests/test_dcp06_review_export.py` | 12 passed |
| 迁移门禁 | exit 0 |
| `ruff check`（批量模块 + 新测试 + 迁移） | 通过 |

覆盖：预览只收已审核、阻断码、确认清单不符 409、真实导出产出 `export_id/filename/count` 并落 `exports` 行、
预览后再批准一张凭证 → 执行拒绝并要求重新预览。

## C：月度包草稿（编排）

同一批量表再增 kind `package_draft`（迁移 123/194 一并放开）：

- `POST /api/firm/batch-operations/package-draft/preview`：逐家找**已复核或已发布**报表
  （`report_runs.status IN ('reviewed','published')`），绑定 `report_id + version`；没有报表 → `no_report`。
- `POST .../package-draft`（202）：必须带确认过的 `manifest`；清单变化即 409。
- 执行：重读报表版本，不一致 → 409「预览后报表版本已变化」；一致才调用既有
  `MonthlyPackageService.create`（幂等键 `batch:{batch_id}:{company_id}`），结果记录
  `package_id / package_version / report_id / report_version`。发布与发送都不在本包内。
- 阻断企业执行时直接 `skipped`，不建包。

### 证据

| 项 | 结果 |
| --- | --- |
| `pytest tests/test_dcp06_package_drafts.py` | 3 passed |
| 批量回归（operations + dcp04 + review_export + package_drafts） | 15 passed |
| 迁移门禁 / `import app.main` | exit 0 / 通过 |

覆盖：无已复核报表 → 阻断且 `ready=0`、确认清单不符 409、阻断项执行 `skipped` 而非 `failed`。

**未验证**：`MonthlyPackageService.create` 的真实成功路径（需要已复核报表 + 月结锁期的完整夹具），
以及浏览器端的勾选→预览→启动→查失败。

## 未完成（DCP06 验收）

1. **C 成功路径**：用 close_case 级别的夹具跑通「已复核报表 → 建包草稿 → 记录 package 引用」。
2. **浏览器验收**：桌面/手机勾选、预览、启动、查失败、单项处理、返回。
3. **暂停/续跑**：暂停只停未领取项；续跑不重复已完成项（DCP04 的取消语义可复用，但本包未接线 UI）。
4. **PG16/17**：本地 WSL 集群在线但 Windows 侧连不上（`127.0.0.1:25531` 超时、无 portproxy）。
5. `period_locked` 分支本地不可测：关闭期间需要「已复核的关账回执」。
6. 生产未应用 123/194，**未推送**。
