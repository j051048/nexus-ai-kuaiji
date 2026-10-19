# UX-R1 收口与验收报告

日期：2026-10-19  
基线 HEAD：`36cc09c1`（本轮未提交）

## 1. 总结

| 阶段 | 状态 |
|---|---|
| UX-R1A 明确缺陷 | **通过**（3 个浏览器用例 + 9 个展示契约单测） |
| UX-R1B UX01/UX02 实际体验 | **核心通过；统一待办聚合未完成** |
| UX-R2 UX03 同屏复核 | **核心路径通过；批量部分失败未专项** |
| UX-R3 UX04 客户协作/开户 | **核心路径通过；开户浏览器未重跑** |
| UX-R4 UX05 助手/手机/错误矩阵 | **部分完成** |
| 五批完整体验验收 | **未通过** |

不能把本轮报告为 UX02–UX05 全部完成。

## 2. R1A 缺陷修复

### R1A-01 管理员主要入口

- `primaryNav` 仅对 admin 展示身份 + `can_manage` 返回管理后台；
- `AgentNavigation` 新增 `onAdmin`，不再永久禁用；
- App 在未锁定（无未保存）时调用现有 `/admin` 路由，保留 MFA；
- 未保存时 `navigationLocked` 阻止跳转。

浏览器未建立独立管理员 persona 入口测试；这是证据缺口。

### R1A-02 能力过滤

- `primaryNav(role, profile)` 按服务端能力过滤 review/monthly/business/operations；
- accountant `can_review=false` 不再显示复核；
- 快捷命令/首页沿用原能力过滤，未新增前端赋权。

单测覆盖；浏览器复核入口用有权限 persona 验证。

### R1A-03 菜单名称与目标

- 事务所负责人“客户账套”指向 `accounting`；
- “费用与运行”指向 `operations`，不再把费用监控叫“团队与效率”；
- 独立团队效率页当前不存在，已在矩阵/报告列为产品缺口，不换标题冒充。

### R1A-04 唯一高亮

- 主契约按钮补 `aria-current`；
- `workActive` 只对 `home` 生效；
- E2E 验证 review 和 cases 深链接各只有一个 `aria-current=page`。

### R1A-05 默认首页

- `roleHome` 增加 `defaultView`；
- App 仅在裸 home（无 company/book/draft/case/search 等）应用默认落地；
- 负责人根入口落到 `/workspace/firm`；
- 显式 `/workspace/cases` 保持不被吞掉；
- 客户员工保持门户首页；
- 管理员/复核员默认落地未建立浏览器 persona 证据。

## 3. 测试

```text
前端 Vitest: 44 files / 354 tests passed
展示契约: 9 passed
typecheck: pass
lint: pass
build: pass（本地 TMP）
```

浏览器：

```text
ux-r1a.spec.ts: 3 passed
stage12-inline-review.spec.ts: 1 passed
stage12-intake.spec.ts: 2 passed（第二次重跑通过；首次出现一次导航点击 detached 的抖动）
b06-role-home.spec.ts: 1 passed
ux02-ux05.spec.ts: 1 passed
```

后端：

```text
test_stage12_workspace.py test_stage12_workspace_api.py: 27 passed
```

截图：

- `docs/ux-screenshots/accounting-desktop.png`
- `docs/ux-screenshots/accounting-mobile.png`
- `docs/ux-screenshots/client-mobile.png`

## 4. UX-R1B 完成与缺口

完成：
- 角色—首页—导航矩阵；
- 客户门户 tabs 过滤；
- 一级导航 4–6 项，旧入口保留 details；
- 默认落地与深链接保护；
- 单企业/多企业/全所/系统上下文纯函数。

未完成：
- 同一账号真实工作视角切换（“全所管理 / 我的会计工作”）；
- 统一待办的稳定业务 ID 聚合/去重；
- 首页“当前身份/当前可操作”大块说明未进一步收至账号/帮助；
- 多企业切换慢请求串数据浏览器证据；
- 报告空状态替代静默回首页。

## 5. UX03/UX04/UX05

UX03：
- 同屏复核、手机原件、返回进度通过；
- 批量部分成功/失败、逐项结果、未知结果核验、审核后真实导出未专项跑。

UX04：
- 员工上传/核对/提交、草稿恢复、部门审批、手机录入通过；
- 财务核验/移交、退回补件直达、管理员开户/新账号登录未重跑。

UX05：
- 助手入口可见，新增当前企业/期间范围小字；
- 未实际发起 mock 助手对话，未验证 A→B 范围切换；
- 错误矩阵、权限撤销、MFA 返回、键盘/焦点、批量筛选未完整验收。

## 6. 候选哈希

```text
workspace-presentation.ts
B810236197662B32CE2469292090AE0B21FFD01B7AE7C987821FA37B509D4074

AgentNavigation.tsx
8D531820E83E632728E275E8DB59F3A7EE16A12DEDCD0153F6EB1340D9E1F9ED

App.tsx
91A13248F8C61F27EB1E6B77C67601FD95E95B3FE8530BBFF6BD7CD27ABD67BD

ClientPortalShell.tsx
9A7D2E0827AD3F7576947FC8EE40823DB2214B7A6916A0294825C026B234ED3D

workspace-types.ts
C351D3C64813ED1D5633DF06E0D42E15A9B7D8A3111FDF979989E1E9F43CA0FD

workspace/service.py
DC77263C586A94A7AD806A42A317E856EA9E1BEBA83BEFBDC1AFD93B1E21B6D4
```

## 7. 并行与边界

- C01/DL 未提交修改保留；
- 未修改 C01、账务内核、迁移、生产权限、复核/导出；
- 未提交、未推送、未部署；
- 浏览器使用本地临时数据库，并非生产 RLS/真实模型验证。

## 8. 结论

R1A 五项明确缺陷已修复并有浏览器/单测证据；UX01/UX02 核心接入通过。  
但 UX-R1B 的统一待办、工作视角、上下文串数据证据，以及 UX-R2–R4 的批量失败、开户、助手范围、错误矩阵仍未完成，因此本轮不能宣布五批完整体验验收通过。
