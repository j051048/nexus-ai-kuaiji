# 批次 K 基线（2026-11-12）

## 依赖与事实

- 依赖 I/J：单壳、岗位说明后移、员工/财务/会计入口与复核抽屉已存在。
- 复用 `WorkspaceHome`、`IntakeWizard`、`BusinessWorkspace`、`ReturnedBusinessNotice`、`BusinessPreparation`、`BusinessOriginalPreview`、`useBusinessTaskMemory`；未另造员工首页/OCR/审批引擎。
- 不接真实 OCR/模型/OA；使用本地 FakeProvider 与合成票据。

## 已有且通过

- 员工首屏提交、票据预填核对、退回原因直达修改、补件撤权清空、版本冲突只读、自动保存一次：`e2e/employee-flow.spec.ts` 9 passed。
- 角色首页服务端授权能力、员工切换后不保留会计入口：`e2e/b06-role-home.spec.ts` 1 passed。
- 岗位首页与身份条：`e2e/tax-i-shell.spec.ts` 5 passed。
- 财务核验对象→原件→核验入口已有；`BusinessPreparation` 的查询键包含企业+日期，换日期重新检查。

## 已有但验收不足

- 企业财务“核验通过→可选账套→生成草稿→会计复核→导出”完整链最初失败；定位为测试 `getByRole('status')` 同时匹配两个 status 的夹具严格定位缺陷，改用 `.business-notice` 后 3 passed/43.3s。产品未改。
- OA 已审事项只补材料链无专项夹具。
- K 的 PG16/17 授权/附件/版本/复核子集未单独跑。
- 真实 OCR 质量、软键盘/微信/iOS 真机未验证。

## 本轮未改业务代码

- 仅记录既有能力与失败；未为通过测试放宽状态、超时或权限。
