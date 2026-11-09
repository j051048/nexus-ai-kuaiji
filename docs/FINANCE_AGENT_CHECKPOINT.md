# 财务 Agent 改良执行检查点

2026-11-07；本轮目录 `tmp_test/finance-agent/20261107-kernel-reuse/`。

## 采用范围

原任务书 13 包已逐项核对，决定见 `FINANCE_AGENT_EXECUTION_STATUS.md`。优先修补现有核算内核及可办理入口；不凭空引入企业费标、平行差旅审批或未经供应商证实的验真状态。

- 往来账龄：接通按企业、账套、期间查看的入口，四类往来分开汇总，拒绝非法截止日期和错范围响应。
- 资产及期末草稿：复用既有接口，完成建卡、生成、另一人复核、正式记账；修复最后一期尾差、科目有效期、创建与复核理由审计。
- 办理后刷新：正式记账后同步刷新当前账簿余额；失败不自动重发，撤权清空页面。
- 无新迁移；历史 SQL、checksum、C01 产品实现均未编辑。

## 当前证据

- 候选源摘要 `36e164d9bc43e4d0807b0ea8782969a49753071da5b855d3a50ae3f4e09cea26`，1984 文件。
- 迁移摘要 `5e02beb380596ea28c17248d00b417060e5100365d27127e2941c085b29da7f7`，340 文件，与起点一致。
- `baseline.json` / `ownership-final.json`：保存既有修改与本轮 11 个源码路径增量。
- `red.xml`、`account-red.xml`、`refresh-red.xml`：保留原缺陷失败证据。
- `pg16-acceptance.xml`、`pg17-acceptance.xml`：各 14 项通过，无跳过。
- `frontend-acceptance.json`：1322 项通过；类型和本轮 lint 通过。
- `browser-acceptance.xml`：开发模式桌面/手机 4 项通过；真实本地 API 与 SQLite，错范围和撤权为受控注入。
- `build-isolated.log`：使用任务独立临时目录构建通过。首次系统临时目录文件占用错误保留在 `build-acceptance.log`。

## 当时保留的失败与后续

时效说明：下述旧迁移夹具与体积红项已在 2026-11-08 的 [收口报告](FINANCE_CLOSURE_ACCEPTANCE_20261108.md) 修复；新的往来核销、跨期计提与同候选验收见 [续批报告](FINANCE_CONTINUATION_ACCEPTANCE_20261108.md)。原始失败记录保留，不改写为旧候选已通过。

续批最终候选 `c337ee9c…` 已完成本地、PG16/17、两模式桌面手机、完整恢复和持续容量验收；本轮四个 PG 集群及身份桥已停止。历史 SQL 不变，新增 PG219/220、SQLite135/136 仅在本地应用。未提交、未推送、未部署。

扩展回归发现 `test_c01_closure.py::test_legacy_unresolved_adjustments_cannot_cancel_each_other` 的旧数据库夹具失败：只装 `<96` 的 SQLite 迁移，当前开户服务查询不存在的 `effective_book_access`。尚未进入银行勾稽断言。相关测试、开户及权限文件本轮均未改变，未添加 skip/xfail，也未为旧夹具降低产品权限。

C01 全新隔离库的双版本固定用例各 34 项通过；构建预览浏览器 4 项通过。复用旧库的超时仍保留，未提高门槛时限。首屏体积预算另有既有 95 字节超额，临时只读基线构建证明本轮前后相同。最终证据汇总和环境关闭结果见 `FINANCE_AGENT_FINAL_ACCEPTANCE_20261107_kernel-reuse.md`。

未提交、未推送、未部署；未接真实模型、通知、税局或客户。
