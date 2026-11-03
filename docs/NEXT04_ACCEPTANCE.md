# NEXT04 人工接手与任务时间线验收

- 状态：**本地工程通过（税审、批量失败、通知 unknown、交付包四类来源；SQLite + PG16/17 + 统一 HTTP/前端入口）；税审与交付包来源真实浏览器通过；批量/通知两类来源仅 API/DB**
- 候选：`source_files=1821`，`source_sha256=d3f7a1fbd9163d2b4f78d2fe26a39e85aafc3e7a0671993267f7406f61fc2f07`，迁移 328/`8bbd6f3f…`；包含并发任务的 `TaxFilingRecords.tsx` 修改。
- 运行：`tmp_test/next/next04-acceptance-20261103-090b/` 为后端 harness `status=passed`；最终候选仅增加交付包被指派人可见性与 SPA 内“打开原记录”修复，SQLite/PG 均已在 d3f7 复跑。
- 回归：SQLite `test_dcp05_handoff.py + test_next04_handoff_adapters.py` **15 passed / 33.81s**（`tmp_test/next/n04-sqlite-d3f7.xml`）；PG16/17 各 **28 passed / 34.20s、33.34s**（`e1205383…`、`2d8dc966…`）。
- 浏览器（税审来源，cf0fe 历史）：真实 `taxFullFixture` + `taxPaper` 创建未复核底稿，打开 `/workspace/monthly` 的“人工接手”，真实 `POST /api/handoff/sources/tax_workpaper/{id}/assign` 200，显示“已指派”，再点“打开原记录核对”回到税务底稿；**1 passed / 31.8s**。
- 浏览器（交付包来源，d3f7）：真实 P02 fixture 创建未签收包 v2，复核会计 `/workspace/monthly` 选 2026-10 后打开“人工接手”，队列显示“交付包版本 2”“已指派”；点击“打开原记录核对”在 SPA 内跳到“月度交付与回执”，未重新登录。**1 passed / 14.9s**，日志 `tmp_test/next/next04-browser-d3f7/run.log`，截图 `queue.png`、`original.png`。
- 该浏览器脚本放在 `tmp_test/next04-delivery.spec.ts`（不在候选扫描前缀内），因此运行前后候选保持 d3f7；前置 seed 用真实 API 创建包、仅以本地管理员插入指派行。

## 已复用并验证的现有能力

- `nexus_backend/app/handoff/service.py`：来源投影只读，不复制金额；岗位与指派人分开；无合格候选人保持“待指派”；指派前校验事务所角色与账套当前复核授权，指派本身不授予权限；制单人不可自审；重复相同指派幂等；改派将旧任务标记 `superseded_at`；无当前账套权限的调用者得到 404；版本不符返回当前版本与指纹。
- 路由：`GET /api/handoff`、`GET /api/handoff/candidates`、`POST /api/handoff/sources/{kind}/{id}/assign`（保留税审旧路由）。
- `test_ak04_handoff.py` 的异常接手：预览指纹绑定，来源或草稿变化后创建被 409 拒绝，撤权后重放不能恢复访问，同一幂等键不同原因冲突，人工接手不改变未知成本和草稿状态。

## 本次修改

1. **旧版本仍出现在待办中**：税务底稿重新生成会产生同期间新版本行，旧草稿仍为 `draft`，接手队列会同时列出新旧两个来源。
   - 修改：`_tax_pending` 只投影每个账套／期间的最新版本。
   - 证据：`test_source_version_change_requires_a_new_assignment` 生成 v2 后，队列只含 v2；v1 任务仍保留但不再展示。
2. **旧版本仍可被继续指派**：直接持有旧底稿 ID 的管理员可以给已过期版本指派。
   - 修改：`assign` 在状态检查后比较同账套／期间最大版本，旧版本返回 409「已有更新版本」。
   - 证据：同一测试断言旧版本指派 409，历史任务未被删除或改写。
3. **已复核底稿仍可被指派**：补充 `test_reviewed_workpaper_cannot_be_assigned_again`，断言 409 且提示“已复核”。

## 已补齐的适配器

- 批量失败项：只投影 `status='failed'` 且 `result_json.book_id` 精确绑定的项；版本 `max(attempt,1)`，指纹绑定 batch/item/attempt/error/updated_at；目标岗位 `ops`，只允许事务所 owner/admin。
- 通知核查：只投影 `state='unknown'`；版本 `attempts+1`；next_action 明确“不自动重发”，指派不产生发送授权。
- 交付包：只投影尚无签收的最新冻结包；版本为 package.version，指纹为 manifest_sha256；岗位 `delivery`。
- 统一 `queue`/`candidates`/`assign` 与 `POST /api/handoff/sources/{kind}/{id}/assign`；前端 `HandoffDialog` 通过种类切换标签。
- **d3f7 已修复** `batch_item` 的“打开原记录核对”映射：`handoffOriginalHref()` 指向 `/workspace/accounting?book=&period=&company=&batch=`；税审指向 delivery 的 `section=tax`，交付包指向 `section=delivery`，通知指向 `section=notifications`。单测 8 passed，typecheck/build exit 0；批量来源浏览器未重跑。
- 来源业务表仍是唯一状态所有者；`handoff_tasks` 只保存指派历史，来源变化后版本/指纹不符返回 409。
- **d3f7 修复负责人可见性**：`_delivery_pending` 允许 owner/admin 看全部，也允许普通 reviewer 看到已指派给自己的未签收交付包；新增红测先 `assert 0 == 1`，修复后 2 项交付包测试通过。真实浏览器复核会计能看到“交付包版本 2”“已指派”并打开原记录，不再空队列。

## 未完成

- 浏览器端从异常进入原记录：税审底稿与交付包已真实验证；批量失败、通知 unknown 两类来源未做真实异常源浏览器验收。
- 未提供把来源后续处理事件合并展示的跨来源时间线；当前只显示指派历史与当前投影。
- 因此 NEXT04 标为“本地工程通过、批量/通知浏览器缺口”，不宣称完整业务验收。

## 命令

```text
.venv/Scripts/python.exe -m pytest -p scripts.qa_no_skip -q \
  nexus_backend/tests/test_dcp05_handoff.py \
  nexus_backend/tests/test_tax_workpaper.py \
  nexus_backend/tests/test_ak04_handoff.py
```

退出码 0；JUnit `handoff-regression.xml`。
