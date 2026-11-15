# 批次 L 验收（2026-11-12）

## 结论

- 本地工程：**部分通过**。
- PG16/17：**未新增 L 专项**；沿用 I/J/K 相关子集。
- 桌面/手机浏览器：已选样例通过，K 财务交接链已通过；完整 J/L 同候选链未完成。
- 真机/人工无障碍：**未验证**。
- 真实客户/T+：**未授权**。

## 结果

| 项 | 结果 | 证据 |
|---|---|---|
| L01 中文 IME 组合态 | 通过（共享判断 + 2 unit） | `src/keyboard.test.ts` |
| L02 多层弹窗 | 助手内打开复核弹窗，Escape 只关复核，助手仍可见 | `stage15-global-assistant` 1 passed/15.5s |
| L03 错误/unknown/重复点击 | unknown 保持未知、核查后仍未知、无签收、仅 1 次 POST | `dcp-r567-evidence` 2 passed/14.3s |
| L04 骨架/宽度稳定 | 3 行固定高度骨架；600ms 延迟后真实行替换，无塌陷 | `stage17-monthly.spec.ts` 1 passed/17.4s |
| L05 reduced-motion/撤权 | 全局 reduced-motion 生效；员工补件撤权清除原件/原因并禁止改新建，助手 revoked 显式阻断 | `reduced-motion` 1 passed/10.4s；`employee-flow`/`assistant-delivery-pages` |
| L06 助手事实边界 | stale/revoked/wrong-scope/late/mfa/server × 1440/390 全部显式阻断，不伪造事实 | `assistant-delivery-pages` 14 passed/31.7s |
| L07 跨浏览器 | Chromium/Firefox/WebKit 无障碍各 3 passed；shell Firefox/WebKit accountant passed | Playwright |
| L08 构建/预算 | 通过 | budget 6/6 |

## 证据

- `tmp_test/tax-i/*.png`
- `tmp_test/tax05-large-voucher-*.png`
- `tmp_test/tasks0712-20261107/screenshots/monitor-*.png`
- 构建/预算命令见 checkpoint。

## 未完成

- K 财务交接已通过；L3 仍受 J 未收口与 OA/PG/跨浏览器全链缺口影响。
- L02–L06 本地专项通过；仅真机/人工屏幕阅读器未验证。
- 真实 200% zoom、软键盘、iOS/微信、屏幕阅读器未验证。
- J/K 未收口，因此 L 不宣称收口。
