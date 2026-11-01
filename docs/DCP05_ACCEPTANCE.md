# DCP05 统一人工接手（第一刀）

日期：2026-11-02　分支：main　状态：本地完成，未推送（生产库缺 193）

## 结论

把「岗位」和「指派」拆成两件事：岗位回答该谁办（系统按来源事实推导），指派回答谁在办（人工选择一个**已具备权限**的人）。
没有合格候选人时保持 `pending`，界面显示「待指派」，不会默认挑第一个管理员。首个适配器是税务底稿待复核；
交付包与批量失败项按同一契约接入，但本刀只落地一个来源，避免一次性重写三处领域状态。

## 契约（`GET /api/handoff`）

```json
{
  "source": {"kind": "tax_workpaper", "id": "...", "version": 1, "fingerprint": "64 位 sha256"},
  "scope": {"book_id": "...", "period": "2026-10", "company_name": "...", "book_name": "..."},
  "role": "reviewer",
  "next_action": "复核税务底稿并出具意见",
  "assignee_id": null, "status": "pending", "label": "待指派", "due_on": null,
  "maker_id": "制单人（用于独立性校验，不代替权限）"
}
```

- 指纹取自来源领域自己的 `source_digest`，不改写、不复制金额事实。
- 只投影调用者**有权读取**的账套；无权限的来源直接不出现（不是显示后再 403）。

## 写入

`POST /api/handoff/tax-workpapers/{id}/assign`，体 `{expected_version, assignee_id, due_on?, reason}`。

1. **版本**：`expected_version` 与服务端当前版本不一致时返回 409，正文是 JSON：
   `{message, current_version, current_fingerprint, refresh}`——只暴露调用者有权知道的版本与指纹。
2. **资格**：被指派人必须是事务所复核人（owner/admin/reviewer）**且**持有该账套 `reviewer` 授权；
   两者缺一即 403「指派不会授予权限」。
3. **独立性**：制单人不能被指派复核自己的底稿（409）。
4. **幂等**：同一 `(source, version, role, assignee)` 重复提交返回同一条记录，不产生第二条待办。
5. **改派**：旧记录置 `superseded_at`，历史保留，队列只显示最新一条。
6. **审计**：写 `audit_events` 的 `handoff.assign`，含来源、版本、被指派人、原因。

`GET /api/handoff/candidates?book_id=&role=reviewer` 只返回真正能复核该账套的人：
事务所复核角色且账套授权为 `reviewer`。

## 交付物

- `nexus_backend/migrations/sqlite/122_handoff_tasks.sql`、`nexus_backend/migrations/postgres/193_handoff_tasks.sql`
- `nexus_backend/app/handoff/{service,router,models}.py`
- `nexus_backend/app/main.py` 注册 `handoff` 服务与路由
- `nexus_backend/tests/test_dcp05_handoff.py`（7 例）

## 证据

| 项 | 结果 |
| --- | --- |
| `pytest tests/test_dcp05_handoff.py` | 7 passed |
| `pytest tests/test_stage8_cases.py tests/test_dcp05_handoff.py` | 21 passed |
| `pytest tests/test_tax_workpaper.py` | 9 passed |
| `ruff check` / `ruff format` | 通过 |
| 迁移门禁 `scripts/check_migration_idempotency.py` | exit 0（仅历史债） |

红→绿覆盖的行为：待指派标记、资格不足 403、制单人自审 409、版本冲突回报真实版本与指纹、
重复指派幂等、改派不删历史、跨账套 404、候选列表只含合格复核人。

## 未完成（DCP05 其余工作项）

1. **时间线**：把税务、交付、批量的既有事实按时间合并到同一视图（本刀只做队列契约）。
2. **交付包 / 批量项适配器**：同类契约，需要各自的「待处理」判据与版本字段。
3. **unknown 通知调查**：确认已送达 / 确认未发送 / 仍未知 + 受控重发（依赖 DCP07）。
4. **前端**：首页「现在轮到谁、还有多久、点哪里」与任务详情的接手面板。
5. **PG 双会话证据**：本地 WSL 集群在线但 Windows 侧连不上（`127.0.0.1:25531` 超时、无 portproxy），
   PG 侧并发/撤权竞态未实测。
6. **生产**：未应用 193，因此**未推送**；推送前须先在 Supabase 按字节应用并核对 `migration_digest()`。
