# N04 客户签收前端收口

日期：2026-10-29。基线：`d607ab1ed5da6032705ebf542747e0344c05dc9c` 加本轮共享工作区修改。
仅本地隔离数据和合成响应；未连接真实模型或通知、未执行生产操作、未提交推送。

## 范围与既有能力

复用 `MonthlyPackagePanel`、`MonthlyBriefing`、冻结包 API、客户签收及事务所待办。
冻结、下载、客户显式签收、会计不可代签、签收后清除事务所待办均是已有能力；本轮重点补齐前端接纳边界、错误语义和准确版本导航。

本子任务修改：

- `src/MonthlyPackagePanel.tsx`
- `src/monthly-package-read.ts`、对应单测
- `e2e/n04-client-signing.spec.ts`、独立真实组件测试入口 `e2e/fixtures/n04-package-harness.*`
- `e2e/q04-client-signing.spec.ts`：修正 N03 后过时的待办负责人断言，增加准确包链接刷新断言

未修改后端、数据库迁移、C01、生产权限或会计复核规则。共享导航 URL 保留由主任务修改，不包含在本子任务归属中。

## 红灯证据

1. `tmp_test/n04-delivery-unit-red.json`：9 例中 2 失败。同 ID、同摘要但不同版本被接受；登录失效、权限不足和二次验证未区分。
2. `tmp_test/n04-duplicate-unit-red.json`：重复 `package` 参数错误地使用首值。
3. `tmp_test/n04-delivery-browser-red-v2.log`：9 例中 7 失败、2 通过。真实组件接受错误 manifest，错误签收响应被当作成功，401/403/409/503 原始内部错误直接显示。
4. `tmp_test/n04-q04-red.log`：原有真实 API 流程失败在 `firm_manager` 旧断言。N03 对具有有效月度服务联系人的包正确返回 `customer`；只改测试断言，不修改业务行为。

最初开发服务器冷编译和 Windows TEMP 权限问题不是产品红灯。浏览器红灯使用修复前构建的独立 preview；绿灯重新构建当前源码。私有 TEMP/TMP 避免临时追踪文件权限干扰。

## 修复

- 查看和签收响应在更新清单、状态或成功提示前，统一绑定企业、账套、期间、package ID、version、manifest SHA-256。
- 签收成功还要求返回 `acknowledged` 且签收人为当前账号；错误响应不宣称成功。
- 查看成功是签收前提；查看失败可重查，不自动签收。手工刷新清空旧确认与说明。
- 授权撤销清空清单、签收态与确认；企业切换、路由目标变化或旧请求迟到不能恢复旧内容。
- 401、403、MFA、409 各有下一步；服务器内部错误不透出。发送结果不明只允许重读签收状态，不自动重签。
- 显式旧版本只显示对应包；缺失、空值、非法、重复或不在范围内的目标不回落到最新包。
- React Router 的 location 变化重建准确版本视图；刷新、前进后退不携带旧版确认。

## 绿灯证据

```text
vitest monthly-package-read / monthly-package / monthly-package-render /
       monthly-briefing-package: 18 passed
eslint 上述三个源文件: passed
tsc -p tsconfig.app.json --noEmit: passed
独立 Vite preview 构建: passed
Playwright n04-client-signing + q04-client-signing: 17 passed (29.6s)
```

浏览器日志：`tmp_test/n04-delivery-browser-green.log`。

共享测试构建由主任务接入：仅 `E2E_LOCAL_ISOLATION=1` 时，`vite.config.ts` 将真实组件测试入口纳入构建；普通生产构建不包含该入口。沿用仓库默认 Playwright dev/preview 配置即可执行新增用例，不依赖本轮临时独立配置，也不通过跳过测试避免 preview 404。共享配置的红绿证据归主任务报告。

15 个故障/范围用例运行真实 React 组件并拦截本地 API 合成响应；2 个原有流程使用本地 FastAPI、SQLite 和虚构角色，验证 11 项冻结文件清单、下载、显式签收、会计代签 403、事务所签收待办消失。不得把这些结果称为生产认证、PG、真实客户、真实消息通知或线上验收。

桌面 1280×900、手机 390×844 截图已目视核对，无横向溢出；手机签收按钮高度至少 44px：

- `tmp_test/n04-delivery-results/q04-client-signing-客户在桌面上查看资料包内容、留下查看痕迹并签收/q04-client-桌面.png`
- `tmp_test/n04-delivery-results/q04-client-signing-客户在手机上查看资料包内容、留下查看痕迹并签收/q04-client-手机.png`
- `tmp_test/n04-delivery-results/n04-client-signing-N04-桌面拒绝同ID错manifest打开结果，清单与状态原子接纳/n04-桌面-version-error.png`
- `tmp_test/n04-delivery-results/n04-client-signing-N04-手机拒绝同ID错manifest打开结果，清单与状态原子接纳/n04-手机-version-error.png`

尚未验证：真实 Supabase 身份/MFA、真实浏览器跨设备登录恢复、iOS Safari/微信、真实客户签收与通知。生产接入仍受既有发布门槛约束。
