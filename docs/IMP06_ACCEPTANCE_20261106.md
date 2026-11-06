# IMP06 验收记录：关键页面可读性与设计规范

- 日期：2026-11-06
- 状态：**A 阶段基线完成；B 阶段待 IMP02–04 收口后执行**
- 候选：HEAD `143d4aea…`，source 1909 / `7886e47a…`，migration 338 / `2c96acd5…`

## A 阶段基线

- 运行目录：`tmp_test/imp/imp-20261106-a/style-baseline/`
- 采集脚本：`tmp_test/imp/imp-20261106-a/capture-styles.mjs`
- 截图：
  - `desktop-employee-home.png`、`desktop-employee-intake.png`
  - `desktop-finance.png`
  - `desktop-accountant.png`
  - `mobile-employee-home.png`、`mobile-employee-intake.png`
- 计算样式：`computed-styles.json`
- 布局基线：`shell-before/metrics.json` 与 `shell-before/*.png`

## 实测（改前）

| 页面 | 视口 | 测量 |
|---|---|---|
| 员工首页 | 1440×900 | body 14px；h1 28px；small 文本 3 处；无横向溢出 |
| 员工录入 | 1440×900 | h1 28px；small 文本 9 处；金额/附件在折叠线下 |
| 财务核验 | 1440×900 | small 文本 3 处；主操作可见 |
| 会计工作台 | 1440×900 | small 文本 4 处 |
| 员工首页 | 390×844 | h1 24px；small 文本 3 处；无横向溢出 |
| 员工录入 | 390×844 | h1 24px；small 文本 7 处；出现 7px 英文眉题/页脚、9px 说明、10px 导航；首屏被步骤/模板占据 |

## 已发现但不在 A 阶段修复

- 手机录入首屏任务过深（IMP04）。
- `数量暂未汇总` 与 0 项不一致（IMP03）。
- 列表行重复长“下一步”（IMP03）。
- 样式字面量与断点碎片化（IMP06-B 只治理关键页面，不全仓替换）。

## B 阶段待办

- 对 IMP02–04 的最终页面重新采集计算样式和截图。
- 06-T1：过小文字/裁切用计算样式或边界断言复现并修复。
- 06-T2：负金额/长金额/长单位/缩放。
- 06-T3：键盘、焦点返回、错误关联、仅图标按钮名称。
- 06-T4：帮助可点击/键盘展开，状态不只靠颜色。
- 06-T5：token 变更后的懒加载页与弹窗回归。
- 06-T6：隔离本地页面跑 axe，不关闭规则。
- 真机/屏幕阅读器人工仍 pending。
