# 事务所运营、批处理与客户开通

## 异步批量任务

- `POST /api/firm/batch-operations/monthly-open` 创建持久父任务，接口只做授权、解析企业范围与入队。
- `GET /api/firm/batch-operations` 和 `/{id}` 读取父任务及逐企业子项。
- `POST /api/firm/batch-operations/{id}/retry` 只重置失败子项，不重新执行已成功客户。
- 子项由统一 Worker 的 `batch` 角色领取；每户独立事务，单户失败不影响其他企业。
- SQLite 本地验证可由 API 嵌入执行；PostgreSQL 生产必须配置统一 Worker。

## 事务所驾驶舱

`GET /api/firm/cockpit?period=YYYY-MM` 仅向事务所管理层开放，一次返回：

- 客户数、已建账、已锁期、逾期、异常和风险客户数；
- 客户 × 五步阶段、待办量、主办会计、风险分；
- 按主办会计聚合的客户数、未结任务、异常和逾期；
- 投影尚未收敛的企业数量，提示任务统计可能短暂回退实时口径。

前端入口为 `/workspace/firm`。该页面不替代会计个人待办，而是用于所长分配、催办和定位风险。

## 客户开通

- `GET /api/firm/onboarding/preview` 复用现有企业开通清单做 dry-run；
- `POST /api/firm/onboarding/runs` 保存可续跑开通记录；
- `POST /api/firm/onboarding/runs/{id}/refresh` 重新检查企业、税号、账套和配置条件；
- `POST /api/firm/onboarding/copy-templates` 从同类客户当前账套复制记账模板，重复模板跳过。

前端入口为 `/workspace/onboarding`，可新增客户档案、选择参考客户、复制模板并进入本月账套。

## 客户端极简壳

不具备事务所账套权限的客户成员自动进入独立四页签壳：`待办 / 提交 / 资料清单 / 本月简报`。
会计工作区、后台任务、模型成本与账套设置不出现在该壳中。服务端权限仍按每次请求重新校验。
