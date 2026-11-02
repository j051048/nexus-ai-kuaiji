# 阶段 1 交接：把“成功路径”补成可验证的（2026-11-02）

## 一句话

阶段 0 已落地（夹具泄漏 `213→0`、CODEOWNERS 保护验收清单、迁移诊断已由并行智能体完成，提交 `fe456f5b`）。
阶段 1 要做的是：**造一个“已发布态”夹具，然后把四条 happy path 写成测试**——不是再写拒绝路径。

## 一、为什么必须先造夹具

现在的测试大量只验“被拒绝”，因为造不出合法终态：

- 报表生成要求**期初已绑定**，否则 `409 账套未绑定待审期初或已有正式期初`；
- 预检/锁期/报表都要求**复核权限**，而本地只有一个复核人 → 触发“制单人不得复核”；
- 包草稿要求**已复核或已发布的报表**。

这三条叠起来，就是 DCP03 已发布态浏览器证据至今做不出来的原因。

## 二、`published_fixture` 应该长什么样

目标：一次调用得到「已锁期 + 已发布报表 + 两个不同人」的账套。

```
1. 建企业、账套、科目模板（复用 test_business 既有夹具）
2. 第二复核人：review_policy_fixtures.independent_reviewer(db, actor, book_id)
   - 若无此文件，用 demo reviewer 身份（local-reviewer）
3. 期初：frozen 期初包 → POST opening-bindings
4. 凭证：制单人制单 → 复核人审批 → 过账
5. 报表：POST /api/reports（复核人）→ 状态 reviewed
6. 月结：POST /api/books/{id}/closing/preflights → 复核 → POST /api/books/{id}/closing/close
7. 发布：把报表推到 published（或直接构造该状态并记录来源指纹）
```

**验收夹具本身**：调用后断言 `report.status == 'published'`、`period` 已锁、`maker != reviewer`。

## 三、四条 happy path

| # | 场景 | 断言要点 | 依赖 |
| --- | --- | --- | --- |
| 1 | **DCP06-C 包草稿** | 预检返回 pinned 报表 id+version → 执行 202 → 包 `version=1` → 落库 `monthly_delivery_packages` → 可下载 | 夹具 + 已复核报表 |
| 2 | **DCP04 批量开期 200 家** | `succeeded+skipped+failed+cancelled == total`；每户一账套；重跑幂等（replay 不新建） | 200 家种子（已有容量脚本可参考 `tools/capacity/dcp10_capacity.py`） |
| 3 | **月结跑到 locked** | 7 项检查全 `passed` → `locked` → 结转下月 → 下月期初 = 本月期末 | 夹具 + 银行覆盖 + 子账控制科目配置 |
| 4 | **DCP08-C 税务时间线** | 底稿 → 登记回执 → 月度进度三段可回放 | **需先实现 C**（当前只有 A/B），别把它伪装成测试 |

第 4 条要么先做功能，要么在报告里明确写“未实现”，**不要写一个通过的空壳测试**。

## 四、不要做的事

1. 不要用 `route.fulfill` 或 mock 掉报表/月结服务来“造”已发布态——那证明不了任何事；
2. 不要把 `test_settlement_company_lock_postgres.py` 再加回被删的清单里当作“补上了”，它是独立门禁；
3. 不要在 PG 未迁移的库上跑（`migrate_accounting.py status --strict` 先过）；
4. 不要在 CI 额度紧张时每条改动都推送；本地先跑绿再推。

## 五、已知环境事实

```
本地 PG16  WSL 172.24.221.111:25531（库 p02_chain / n01_gate）
本地 PG17  WSL 172.24.221.111:25532（库 dcp17_test，未迁移，账号 accounting_app 已可登录）
生产       https://kuaiji.zeabur.app，台账 196
Python     ./.venv/Scripts/python.exe（系统 3.14 的 TestClient 不接受 client=）
CI         转 public 后可用；verify 已越过格式检查，P01 卡在集群准备
```

## 六、完成判据

1. `published_fixture` 存在且有自身断言；
2. 上面 1–3 条在 **PG16** 上跑绿（SQLite 只作为快速回路）；
3. 每条失败时能打印「哪一步、哪个对象、什么状态」；
4. `docs/STAGE1_HAPPY_PATHS.md` 记录运行命令与耗时。

## 七、本会话留下的坑（新会话先看）

- 工作区有并行智能体**未提交**的迁移 `197/198/199` 与若干 `app/` 改动——**不要顺手提交**，更不要在生产迁移前推送；
- `.github/workflows/p01-recovery.yml` 已修（amd64 资产 + 真实哈希），但 `joint-recovery` 仍卡在 `Prepare owned isolated clusters`，需要日志尾部才能定位；
- CI 每次推送会跑 11 个 job，成本不低，合并推送。
