# OPT08：前端统一金额模块与凭证录入效率

先读 `OPT00_MASTER.md`。无前置。

前端约束：React 18、TypeScript 严格模式、无界面库、手写 CSS。新代码不引入新依赖。颜色和字号用 `src/tokens.css` 里的变量，不新写十六进制颜色和像素字号。`vitest` 运行在 node 环境，只能测纯函数；交互靠 Playwright。

## OPT08-A  统一金额模块

**问题。**

- `src/JournalEntryGrid.tsx` 用 `Number.parseFloat` 加 `reduce` 算合计，父级抽屉用 BigInt，两边可能不一致。
- `src/TaxFilingRecords.tsx` 用 `Math.round(Number(amount) * 100)` 得到分再提交。
- 全仓约 28 个各自实现的金额格式化函数，多处是 `(cents/100).toFixed(2)`，没有千分位。正确的 BigInt 格式化在 `src/csv.ts` 和 `src/formal-books-types.ts` 各有一份。

**做法。**

1. 新建 `src/money.ts`，纯函数、无依赖：
   - `parseYuanToCents(text): bigint | null`：只接受最多两位小数的十进制文本，允许千分位逗号和首尾空白；拒绝指数、多于两位小数、空串。规则与后端 `money_to_cents`（`nexus_backend/app/accounting/invariants.py`）一致，先读它。
   - `formatCents(cents, options)`：千分位、两位小数；选项控制是否带货币符号、零值显示方式、负数表示（负号或括号）。
   - `sumCents(values)`：BigInt 求和。
   - `centsToApiString(cents)`：生成接口要的十进制字符串。
2. 把现有两份 BigInt 格式化实现改为从 `src/money.ts` 导出，原位置保留再导出，避免大面积改导入。
3. 替换以下用法。分批进行，每批跑一次类型检查和单元测试：
   - `src/JournalEntryGrid.tsx` 的 `amount`、`sum`、`money`。
   - `src/TaxFilingRecords.tsx` 的分转换。
   - 其余本地金额函数：用搜索找出（关键词 `toFixed(2)`、`/ 100`、`* 100`、`parseFloat`、`const money`、`function money`、`formatMoney`、`formatAmount`）。逐个确认是金额才替换；百分比、时长、数量不动。
4. 不改接口字段和后端。接口返回的是字符串金额就按字符串解析，返回的是整数分就按整数处理，逐个确认。

**先失败测试（`src/money.test.ts`）。**

- `0.1 + 0.2`、`1.005`、`19.99 × 100` 这类浮点陷阱输入得到精确结果。
- 超过 `Number.MAX_SAFE_INTEGER` 的分值能正确格式化和求和。
- 非法输入返回 `null`：`1e3`、`1.234`、`abc`、空串、`1,23.4`。
- 负数和零的格式化符合选项。
- 200 行随机金额的求和与 BigInt 参考实现一致。

再加一条守卫测试：读取 `src/**/*.ts(x)` 源码文本，断言除 `src/money.ts` 和白名单外，不出现 `parseFloat(` 以及 `Number(` 紧接 `* 100` 的金额转换写法。白名单逐条写明理由。仓库已有读源码文本的测试，照其写法。

**验收。**

```
npm test
npm run typecheck
npm run lint
npm run format:check
```

状态文件列出：被替换的函数清单、保留未改的清单及理由。

## OPT08-B  金额显示样式统一

**问题。** 负数没有视觉区分；金额列是否右对齐、是否等宽数字不统一。

**做法。**

1. 在全局样式中定义一个金额单元格类：右对齐、`font-variant-numeric: tabular-nums`、负数用独立的类着色。颜色用 `src/tokens.css` 中已有的语义色，没有合适的就在 `tokens.css` 增加一个语义变量。
2. 新建一个小组件 `MoneyCell`（或同名工具函数），内部调用 `formatCents` 并套用上述类。
3. 先接入三处：正式账簿余额表（`src/FormalBooksWorkbench.tsx`）、凭证录入表格合计行（`src/JournalEntryGrid.tsx`）、复核抽屉分录（`src/InlineReviewDrawer.tsx`）。其余页面不在本模块范围。
4. 余额表增加"方向"列显示"借"或"贷"，金额显示绝对值。现有"借方余额为正，贷方余额为负"的说明文字相应调整。先确认接口返回的数据足以判断方向。

**验收。** 类型检查、单元测试、格式检查通过。涉及的 Playwright 用例本机能跑则跑；若用例断言了旧的负号文本，按新显示更新断言并在状态文件逐条列出。

## OPT08-C  可搜索的科目选择器

**问题。** `src/JournalEntryGrid.tsx` 的科目选择是原生 `<select>`，列出整张科目表，无法搜索。

**做法。**

1. 新建 `AccountPicker` 组件，遵循组合框的无障碍模式：输入框 `role="combobox"`，下拉 `role="listbox"`，选项 `role="option"`，`aria-expanded`、`aria-activedescendant` 正确维护。
2. 匹配规则写成纯函数放在单独文件便于测试：
   - 按科目代码前缀匹配。
   - 按科目名称包含匹配。
   - 按拼音首字母匹配：不引入拼音库。先查科目数据里是否已有助记码字段；没有就只做代码和名称匹配，把"拼音首字母"列入状态文件"未做"，不要自己内嵌大字典。
   - 只能选末级科目（后端要求分录用末级科目）；非末级显示为不可选的分组标题。
3. 键盘：上下键移动，回车选中，Esc 关闭并还原，Tab 选中当前高亮项并移到下一格。
4. 科目很多时只渲染匹配结果的前若干条并提示继续输入。
5. `JournalEntryGrid` 两种模式（`inline` 和另一种）都替换。保留原有的 `aria-label` 文案规则，现有 e2e 用例依赖这些标签。

**先失败测试。** 匹配函数的单元测试：代码前缀、名称包含、非末级不可选、空查询、无结果。

**验收。** 类型检查、单元测试通过。用到 `JournalEntryGrid` 的 Playwright 用例若通过 `selectOption` 操作科目，需要改为输入并选择：逐个更新并列出。

## OPT08-D  表格键盘连续录入

**问题。** 金额格没有回车和方向键导航，无法只用键盘录完一张凭证。

**做法。**

1. 在 `JournalEntryGrid` 中实现单元格导航：
   - 回车：移到本行下一个可编辑格；在行末移到下一行第一格；在最后一行行末自动新增一行。
   - 上下方向键：在金额格中移到上一行或下一行的同一列（科目格内上下键归选择器使用）。
   - 借方格输入金额后，同一行贷方自动清空，反之亦然（后端要求每行只有一方）。
   - 提供一个快捷键把当前行金额填为"使借贷平衡所需的差额"，在界面上有可见提示。
2. 导航逻辑抽成纯函数（输入当前位置和按键，输出下一位置）并做单元测试。
3. 合计行实时显示借方合计、贷方合计、差额，差额不为零时有明显提示，用 OPT08-A 的 BigInt 求和。
4. 不改保存和提交逻辑。

**先失败测试。** 导航纯函数的单元测试覆盖上述每条规则和边界（第一行向上、最后一行向下、只有一行）。

**验收。** 类型检查、单元测试、格式检查通过；新增一条 Playwright 用例：只用键盘录入一张两行平衡凭证并保存成功（本机能跑则跑）。

## OPT08-E  分录行摘要（先核实再决定）

**核实。** 先确认后端分录行是否有行级摘要字段（查 `voucher_lines`、`draft_lines` 表结构和相关接口模型）。

- 后端已有字段而前端没显示：在表格中加摘要列，支持"与上一行相同"的快捷填充。
- 后端没有该字段：**不做**，在状态文件写明"需后端模型变更，超出本模块范围"。不要用前端拼接到凭证摘要里的方式假装实现。
