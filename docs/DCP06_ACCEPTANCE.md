# DCP06 批量导出与交付准备（A 刀：批量准备复核资料）

日期：2026-11-02　状态：A 完成，本地提交；B/C 未开始

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

## 未完成（DCP06 B/C 与验收）

1. **B 受控导出**：对已复核且版本有效的对象调用既有导出冻结 manifest，逐项产出 export 引用；
   需要迁移放宽 `batch_operations.kind`（当前 CHECK 只允许 `monthly_open`），按手册要求不建并行队列。
2. **C 月度包草稿**：满足月结/发布政策才准备草稿，发布仍需有权限的人明确确认；本包不发送。
3. **批量作业接线**：preview→确认清单→后台逐企业执行→逐项结果/失败/跳过；暂停只停未领取项。
4. **浏览器验收**：桌面/手机勾选、预览、启动、查失败、返回。
5. **PG16/17**：本地 WSL 集群在线但 Windows 侧连不上（`127.0.0.1:25531` 超时、无 portproxy）。
6. `period_locked` 分支本地不可测：关闭期间需要「已复核的关账回执」，测试因此未覆盖该分支。
