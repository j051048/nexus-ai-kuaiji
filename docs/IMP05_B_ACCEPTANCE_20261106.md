# IMP05-B 验收记录：协同链与版本边界

- 日期：2026-11-06
- 工程状态：**本地协同链通过；真人试用 pending**
- 候选：HEAD `143d4aea7154b1905b96ab757ecd14865354b641`，source `1916` / `b00de1db0b17b41c493c2ac2d7f343cdc27a9c346a0a9acfc27c4efa0ad8f7d0`，migration `338` / `2c96acd52de191347a2365962e879340918fad013786faa260b4b13140646116`
- 运行目录：`tmp_test/imp/imp-20261106-a/`
- 证据：`tmp_test/imp/imp-20261106-a/imp05b-browser.json`

## 目的

用现有真实 UI/API/权限链回放多企业协同，而不是只看接口或静态页面。重点验证：

1. 员工提交与附件、退回补件仍能在原事项恢复；
2. 财务核验、会计制单、独立复核、客户签收的职责边界；
3. 多人/多企业切换不串数据；
4. 旧交付包链接不会静默升级到新版本；
5. 无效接手与结果未知不自动重发。

## 执行

```bash
npx playwright test \
  e2e/review-focus.spec.ts \
  e2e/employee-flow.spec.ts \
  e2e/role3-guidance.spec.ts \
  e2e/next-p1-handoff.spec.ts \
  e2e/n04-client-signing.spec.ts \
  --config=playwright.config.ts --reporter=json \
  > tmp_test/imp/imp-20261106-a/imp05b-browser.json
```

结果：**36 expected / 36 passed / 0 skipped / 0 unexpected / 0 flaky，85.16s，exit 0**。

| 覆盖 | 证据 | 结论 |
|---|---|---|
| 员工提交、附件、退回补件、版本冲突 | `employee-flow.spec.ts`（9） | 真实登录/API；退回后回原事项；冲突只读，不自动重提 |
| 部门/财务/会计职责与独立复核 | `review-focus.spec.ts`（6） | 财务核验≠凭证审核；会计确认后才审核；不代签 |
| 多企业显式选择与手机可读 | `role3-guidance.spec.ts`（6） | 多企业不默认第一户；引导随原件/确认变化；未确认不提交 |
| 接手原记录、改期、未知通知 | `next-p1-handoff.spec.ts`（2，桌面+手机） | 无效批量项不可指派；未知通知显示“不自动重发”；原记录深链保留 |
| 旧包/错版本/撤权/迟到响应 | `n04-client-signing.spec.ts`（13） | 409 只重读；无效或缺失准确版本不回落最新；切企业不复活旧数据 |
| 客户签收与 MFA | 同上 | 签收需准确包 ID/版本/摘要；MFA 不误报失效 |

## 覆盖边界

- `n04-client-signing.spec.ts` 使用页面级 API 夹具验证前端契约，不是真实对象存储/真实客户。
- `employee-flow`、`review-focus`、`role3-guidance`、`next-p1-handoff` 走本地真实 API/身份/SQLite；没有用 `route.fulfill` 伪造业务成功。
- 真实企业、真实员工、真人计时、真实通知/签收仍未执行；`docs/IMP05_TRIAL_PACKAGE.md` 的记录表保持空，`summary.json` 为 `incomplete/no_records`，不补零。
- `role3-guidance` 的夹具修正为：在录入弹窗内用“事项客户企业”下拉显式选第一户，原因是 IMP04 的产品契约要求多企业必须选择；这不是放宽产品断言。

## 与验收项对照

- 05-T1 两企业员工提交：通过（真实 API）。
- 05-T2 部门/财务/会计/复核/签收：通过（真实 API + 页面契约）。
- 05-T3 无效指派、未知不重发：通过；浏览器验证 + 既有后端测试。
- 05-T4 旧包链接/错版本：通过（页面契约）。
- 05-T5 独立复核：通过；制单人与复核人分离。
- 05-T6 真人试用：**pending**，本轮没有真实用户。

## 剩余问题

1. 真人试用、真实企业、真实通知/签收未执行。
2. `n04` 的包页面为受控夹具，不能替代真实对象存储与真实客户网络。
3. 36 项是协同链回归，不等于 200–500 家规模验证。
4. 本地候选未提交/推送/部署；外部 CI 未在本轮触发。
