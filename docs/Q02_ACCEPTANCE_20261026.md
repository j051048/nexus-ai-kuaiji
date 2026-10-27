# Q02 验收：角色化工作台（权限 × 工作阶段 × 对象状态）

- 日期：2026-10-26
- 基线：`3fc1ab6e`（`feat: close C01 accounting and recovery acceptance`）
- 范围：员工录入、会计同屏复核、月度交付三个场景的前端读取生命周期、对象级资格展示与错误态
- 边界：未改后端、未改迁移、未改 C01 契约、未接真实模型、未连生产库、未提交推送

## 0. 结论

| 工作包要求 | 状态 | 证据 |
| --- | --- | --- |
| 先写失败测试（读取生命周期 5 类） | 完成 | `src/scoped-read.test.ts`（6 例）、`src/review-eligibility.test.ts`（17 例）、`src/api-error-presentation.test.ts`（8 例），先红后绿 |
| 修复经测试确认的读取生命周期问题，先用于现有只读场景 | 完成 | 新增 `src/scoped-read.ts`；`src/useScopedQuery.ts` 改为复用同一票据闸门 |
| 用具体资源的服务端操作资格展示：状态 / 阻断原因 / 下一位负责人 / 下一步入口 | 完成 | `src/review-eligibility.ts` + `src/ReviewWorkbench.tsx` 阻断面板 |
| 全局角色只控制粗粒度展示，不替代对象级权限 | 完成 | `reviewEligibility({ authorization })` 由「这本账套读到了没有」决定，不由 `profile.can_*` 决定 |
| 权限未知时不显示高风险操作 | 完成 | `state='unknown'` 时 `canConfirm=false`，且不渲染确认入口 |
| 三个场景统一加载 / 空 / 错误状态 | 部分 | 复核与录入已统一；月度交付仅收敛客户视角文案 |
| 错误使用受控中文 + 请求编号，不直接展示原始异常 | 完成 | `src/api-error-presentation.ts`；`IntakeWizard` 展示标题 + 下一步 + 折叠技术信息 |
| 桌面、手机完成提交→核验→复核→交付 | 完成 | 核心集 preview 106/106、dev 105/106（失败项为既有 dev 时序不稳定，见 §5） |
| 不增加全局 Enter 一键批准 / 过账 / 锁期 | 完成 | 本次未新增任何快捷批准路径 |

一句话：**这次把「能不能点确认」从全局角色判断改成按账套的对象级判定，并把阻断原因翻译成「谁接着办」，同时给读取竞态和错误文案补了可执行的单测。**

## 1. 先写失败测试

三份测试在实现前写成并跑红：

| 测试文件 | 覆盖 | 首次结果 |
| --- | --- | --- |
| `src/scoped-read.test.ts` | reload 后卸载、连续 reload、A→B→A、撤权后的迟到响应 | 模块不存在，导入失败 |
| `src/review-eligibility.test.ts` | 同一账号 A 企业可审 / B 企业只读、本人制单、已锁期、权限未知、版本前移、预览过期 | 模块不存在，导入失败 |
| `src/api-error-presentation.test.ts` | 401、403、MFA、409、413、422、5xx、断网、主动取消 | 模块不存在，导入失败 |

首轮实现后仍有两处红，暴露的是真实语义错误，不是断言写错：

- `409` 原本标 `retryable: true` —— 版本冲突自动重试会覆盖别人的修改，改为 `false`；
- `500` 原本归为「服务暂时不可用」——`500` 是服务端自身缺陷，归为 `unknown` 并保留技术信息，只有 `502/503/504` 才是「稍后重试」。

## 2. 实现

### 2.1 读取票据闸门（`src/scoped-read.ts`，47 行）

`ReadGuard` 只有四个动作：`begin()` 中止上一笔并发放新票据、`accepts(ticket)` 判断这笔响应是否仍是当前、`cancel(ticket?)` 释放作用域、`released()` 查询状态。

关键约束：**迟到的 abort 不得掐掉接替它的新读取**。

```ts
cancel(ticket) {
  if (ticket && live && ticket.stamp !== live.stamp) return;
  live?.controller.abort();
  live = null;
  released = true;
}
```

`src/useScopedQuery.ts` 原来自带 epoch + AbortController 样板，现改为复用该闸门；`MonthlyBriefing` 的既有用例保持通过。

### 2.2 对象级资格投影（`src/review-eligibility.ts`，255 行）

输入是服务端返回的 `issues` 与 `ready`，输出是

```ts
type ReviewEligibility = {
  state: 'ready' | 'blocked' | 'denied' | 'expired' | 'stale' | 'unknown';
  headline: string;
  blockers: { code; reason; owner; entry; source }[];
  canConfirm: boolean;
  nextOwner: string;
  nextEntry: string;
};
```

- 归一只做「归类 + 排序 + 谁接着办」，`issues` 仍是唯一事实来源；
- 服务端原文保留在 `source`，只在「查看原始提示」里展开，不直接作为主文案；
- 判定顺序固定为：资格未知 → 资格被拒 → 无快照 → 预览过期 → 版本前移 → 阻断项 → 服务端 `ready=false` → 可确认。**权限未知永远走不到 `ready`。**

### 2.3 错误呈现（`src/api-error-presentation.ts`，170 行）

`presentApiError(error, requestId)` 返回 `kind / title / detail / nextStep / requestId / retryable / relogin / technical`；`AbortError` 返回 `null`，让调用方不渲染错误态。

- `422` 只截断到 160 字（服务端校验文案可控）；
- 其余状态一律用受控中文，原始异常只进 `technical`，折叠展示。

### 2.4 接线

| 文件 | 改动 |
| --- | --- |
| `src/ReviewWorkbench.tsx` | 批量预览新增阻断面板（`role="status"`，列出阻断原因与下一位负责人）；每张凭证的 `issues` 原文改成折叠；`authorization` 由「列表/预览是否读到」推导 |
| `src/IntakeWizard.tsx` | 提交失败时展示标题 + 下一步 + 请求编号，技术信息折叠；原有引导文案与「读取最新草稿」按钮保留 |
| `src/MonthlyPackagePanel.tsx` | 客户视角（`clientOnly`）把「交付包 V1」改为「本月资料包（第 1 版）」，校验哈希折叠为「供技术核对」 |
| `src/review.css` / `src/intake.css` | 新增阻断面板与纵向错误布局，全部使用 tokens |

## 3. 与工作包逐条对照

| 要求 | 落地 |
| --- | --- |
| 复用现有岗位体系，不重建导航/AppShell | 只改三个既有页面与一个既有钩子 |
| 不新增首页模块 | 未改 `WorkspaceHome`、`App.tsx` |
| 具体资源的操作资格 | `reviewEligibility` 以账套为单位，不读 `profile.can_review` |
| 提交时重检 | 未动服务端；前端 `canConfirm` 仅决定按钮可见性 |
| 单人办理、独立复核语义区分 | 「申请人不能审核本人业务事项的凭证」映射为「独立复核会计」，并给出移交入口 |
| 金额对齐、按钮主次、加载/空/错误 | 复核与录入已统一到 tokens；月度交付仅改客户文案 |
| 不增加一键批准 / 过账 / 锁期 | 未新增 |

## 4. 门禁证据（本地）

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 类型 | `npx tsc -p tsconfig.app.json --noEmit` | 0 错 |
| 单测 | `npx vitest run` | 92 文件 / 933 用例通过 |
| lint | `npm run lint` | 0 错 |
| 格式 | `npx prettier --check`（改动文件） | 通过 |
| 构建 | `npx vite build`（`TMP` 指向仓内） | 成功，主包 605.81 kB |
| e2e（dev） | `E2E_CORE_ACCEPTANCE=1 E2E_FRONTEND_MODE=dev npx playwright test` | 105/106，1 条既有不稳定 |
| e2e（preview） | 同上 `E2E_FRONTEND_MODE=preview`（需 `TMP` 指向仓内） | 106/106 |

## 5. 浏览器验收

核心集共 106 条（`scripts/e2e_core_cases.json`），dev 与 preview 各跑一遍：

| 模式 | 结果 | 失败项 |
| --- | --- | --- |
| dev（18080，`vite dev`） | 105 通过 / 1 失败（6.4 分钟） | `e2e/v09-navigation-history.spec.ts:410` mobile 导航高亮 |
| preview（18080，`vite preview` + 生产构建） | **106 通过 / 0 失败**（3.6 分钟） | — |

dev 的那次失败已确认与本次改动无关，是这台机器上的既有不稳定：

- 用 `git checkout` 还原本次全部前端改动后，同一条用例 `--repeat-each=3` **3 次全失败**，且报错形态变成 `.agent-sidebar` 不可见 —— 说明它在干净 HEAD 上同样不稳；
- 带本次改动复跑时 1 次失败 1 次通过（`--repeat-each=2`）；
- 同一批用例在 preview（生产构建、无 HMR）下 106/106 全绿，指向 dev 服务器的热更新时序，而非产品逻辑；
- 另一个曾失败的 `e2e/business.spec.ts:67`（员工报销全链路）单独复跑 1 次通过（2.0 分钟），最终 dev 全量跑中也通过。

> 冷编译耗时约 118 秒，日志会出现 `[e2e-warmup] dev entry bundle ready in …`；这是 Q01 已记录的不稳定来源。
> preview 模式会先执行 `npm run build`，本机必须把 `TMPDIR/TEMP/TMP` 指到仓内目录（`.tmp_build`），否则 esbuild 清理系统 Temp 时报 `Access is denied` —— 与 Q01 相同。
> `scripts/e2e_reporter.mjs:87` 在缺少报告输出路径时会在 `onEnd` 抛 `ERR_INVALID_ARG_TYPE`，发生在用例统计之后，不影响结果判定（Q01 已记录）。

## 6. 未覆盖 / 未验证

- **服务端「政策版本变化」文案**：仓库内只有期间关闭、本人制单、异常补件、相似交易、证据缺失、分录不完整、不平衡、金额不一致、单侧金额、银行方向、科目失效这些 `issues`，没有「政策版本变化」的稳定文案。为避免猜测，本次不造规则；未知文案统一落到 `unknown` 分类并保留原文。
- **客户自助签收**：现有签收入口仍由获授权会计/联系人代录，Q04 才做移动端自助签收。
- **移动端专用布局**：本次只复用既有响应式断点，未新增移动端专属排版。
- **PG16/17**：与 Q01 相同，本机只有 PG 14.24（WSL），未执行目标版本矩阵。
- **请求编号来源**：`IntakeWizard` 目前没有从响应头读取 `X-Request-Id` 的通道，`requestId` 传入空串；展示位已就绪，待后端在错误响应里带回编号或前端补读取。
- **`App.tsx` 体量**：本次未拆分（1,896 行、34 个 `useState`），属 Q05 之后的独立任务。

## 7. 可复现命令

```bash
cd "C:\Users\Fei\Desktop\AI应用\nexus-ai-kuaiji"
npx vitest run src/scoped-read.test.ts src/review-eligibility.test.ts src/api-error-presentation.test.ts
npx tsc -p tsconfig.app.json --noEmit
npm run lint
npx vitest run
TMPDIR="$PWD/.tmp_build" TEMP="$PWD/.tmp_build" TMP="$PWD/.tmp_build" npx vite build
E2E_CORE_ACCEPTANCE=1 E2E_FRONTEND_MODE=dev npx playwright test
E2E_CORE_ACCEPTANCE=1 E2E_FRONTEND_MODE=preview npx playwright test
```

## 8. 风险与后续

1. `authorization` 目前由「列表是否读到」推导，若将来出现「能读列表但不能复核」的岗位（例如只读审计），需要在 `ReviewList` 上补服务端返回的 `can_review_scope`，否则会把只读岗位误判为 `denied` 而非 `blocked`。
2. 阻断面板按 `code` 去重，同一账套里两张凭证因不同原因阻断时只列一次原因，但「下一位负责人」取第一条；多阻断并存时建议改成按负责人分组。
3. `useScopedQuery` 重构后未加钩子级测试（仓内无 jsdom/testing-library），竞态语义由纯函数用例保证，钩子层只做接线。
