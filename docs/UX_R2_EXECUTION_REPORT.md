# UX-R2 执行报告

## 状态

- UX-R2A 角色入口/上下文：核心契约通过。
- UX-R2B 批量、开户、助手、错误矩阵：未完成。

## 修复

1. 默认落地只在裸根 `/` 生效；显式 `/workspace/home` 刷新保留。
2. `selectBriefing` 按当前企业/账套/期间选择，不再 `briefings[0]`。
3. 报告多匹配/无匹配不猜测，提示先明确范围。

## 证据

```text
ux-r1a.spec.ts: 3 passed
workspace-presentation: 10 passed
frontend: 44 files / 355 passed
tsc/lint/build: pass
```

U01/U02：根入口、显式 home、cases 深链接。
U05–U07：报告按范围匹配、无匹配/多匹配不选第一份。
U03/U04/U08–U12：未新增专项浏览器证据。

## 未完成

- 批量部分成功逐项结果；
- 指定企业开户/A-B 隔离；
- 助手实际 mock 对话与 A→B 切范围；
- 401/403/MFA/撤权完整矩阵；
- 手机键盘焦点、错误关联；
- 请求数量对比。

未提交、未推送、未部署。
