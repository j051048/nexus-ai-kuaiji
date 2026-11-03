# NEXT04 人工接手与任务时间线验收

- 状态：**进行中（税审来源已通过；批量／交付／通知适配器未完成）**
- 候选（ddb 最终重跑）：`source_files=1816`，`source_sha256=dddef1a88eee0bfdc2c2db37d09560dc0290b434fe800993c05b52b7ba2a963e`，迁移 325/`48fc7b47…`；该候选含并发任务的 `TaxFilingRecords.tsx` 修改。
- 运行：`tmp_test/next/next04-acceptance-20261103-final3/`，`status=passed`，候选前后一致，cleanup passed。
- 回归：`handoff-regression` 31 passed／74.93s，0 failed，0 skipped。

## 已复用并验证的现有能力

- `nexus_backend/app/handoff/service.py`：来源投影只读，不复制金额；岗位与指派人分开；无合格候选人保持“待指派”；指派前校验事务所角色与账套当前复核授权，指派本身不授予权限；制单人不可自审；重复相同指派幂等；改派将旧任务标记 `superseded_at`；无当前账套权限的调用者得到 404；版本不符返回当前版本与指纹。
- 路由：`GET /api/handoff`、`GET /api/handoff/candidates`、`POST /api/handoff/tax-workpapers/{id}/assign`。
- `test_ak04_handoff.py` 的异常接手：预览指纹绑定，来源或草稿变化后创建被 409 拒绝，撤权后重放不能恢复访问，同一幂等键不同原因冲突，人工接手不改变未知成本和草稿状态。

## 本次修改

1. **旧版本仍出现在待办中**：税务底稿重新生成会产生同期间新版本行，旧草稿仍为 `draft`，接手队列会同时列出新旧两个来源。
   - 修改：`_tax_pending` 只投影每个账套／期间的最新版本。
   - 证据：`test_source_version_change_requires_a_new_assignment` 生成 v2 后，队列只含 v2；v1 任务仍保留但不再展示。
2. **旧版本仍可被继续指派**：直接持有旧底稿 ID 的管理员可以给已过期版本指派。
   - 修改：`assign` 在状态检查后比较同账套／期间最大版本，旧版本返回 409「已有更新版本」。
   - 证据：同一测试断言旧版本指派 409，历史任务未被删除或改写。
3. **已复核底稿仍可被指派**：补充 `test_reviewed_workpaper_cannot_be_assigned_again`，断言 409 且提示“已复核”。

## 未完成

- 批量失败项、月度包交付阻断、通知 `unknown` 核查尚未接入统一接手队列；当前只有税务底稿适配器。
- 未提供跨来源统一时间线视图；`handoff_tasks` 只记录指派历史，没有把来源的后续处理事件投影进同一条时间线。
- 未做浏览器端从异常进入原记录的验收；本轮只有服务层与 HTTP 合同测试。
- 因此 NEXT04 不标记为通过，后续工作需先补齐上述适配器与浏览器证据。

## 命令

```text
.venv/Scripts/python.exe -m pytest -p scripts.qa_no_skip -q \
  nexus_backend/tests/test_dcp05_handoff.py \
  nexus_backend/tests/test_tax_workpaper.py \
  nexus_backend/tests/test_ak04_handoff.py
```

退出码 0；JUnit `handoff-regression.xml`。
