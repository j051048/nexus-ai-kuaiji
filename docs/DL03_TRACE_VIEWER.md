# DL03 离线执行记录与进度界面

状态：离线静态原型；未接生产 API/认证。

## 事件

`run_created`、`extraction_started/completed`、`validation_completed`、`review_requested/started/completed`、`blocked`、`failed`、`unknown`、`run_finished`。

校验重复 seq、乱序、终态后非法转换；事件含 run/样本/企业/账套、reason、说明、证据、可选诊断。

## 授权投影

`trace.project(events, Principal)` 在服务端纯函数内按可信测试上下文过滤：

- 员工：仅本人 sample；
- 企业负责人：本企业；
- 会计/管理员：本企业；`can_diagnose` 才输出诊断；
- 未授权字段在投影数据中移除，不传全量到浏览器再隐藏。

## 界面

`tools/ai_inference_lab/viewer/index.html` 独立静态 HTML，无 CDN/外部字体/外部请求；显示业务状态、原因、证据和可选诊断；不提供审核/记账/导出按钮。

## 测试

- `test_ai_execution_trace.py` 5 passed：正常投影、员工/企业隔离、重复/乱序/终态、诊断权限、HTML 脚本注入与离线。
- 浏览器（Playwright，file://，375px 手机）：显示“等待会计复核”“金额冲突”，外部请求 0。
- 桌面 1280px 同页面结构，未单独截图。

未验证：真实登录/RLS、生产前端导航、真实模型调用。
