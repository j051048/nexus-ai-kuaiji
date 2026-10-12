# Supabase 真实登录与试点账号

第二阶段采用 Supabase Email / Password 登录。Supabase 只确认“当前用户是谁”；事务所角色、客户企业范围和账套权限由会计应用数据库管理。用户更改姓名或 JWT metadata，不能因此获得更多账务权限。

## 本地模式和真实模式

- `ACCOUNTING_AUTH_MODE=local`：保留本地 opaque token 和虚构验证身份，方便现有导入、审核、导出测试。只能用于开发回环地址，不能部署为生产登录。
- `ACCOUNTING_AUTH_MODE=supabase`：前端通过 Supabase 登录并刷新 access token；每个会计 API 请求携带 `Authorization: Bearer <access_token>`。服务端必须验证身份，再查询应用授权。
- 生产启动同时要求真实登录和 PostgreSQL。数据库运行连接使用 `ACCOUNTING_DATABASE_URL`，迁移管理连接使用 `ACCOUNTING_ADMIN_DATABASE_URL`；账号与最小权限配置见 [PostgreSQL 配置](POSTGRES_SETUP.md)。

## 使用独立 Supabase 项目

先为少数试点企业新建独立项目；不要接原 AI 中控平台数据库或沿用旧账号。开启 Email / Password 登录。试点阶段可以关闭公开注册，手动准备少量测试用户；忘记密码、邀请和邮箱确认所需邮件能力由后续正式开户流程配置。[官方密码登录说明](https://supabase.com/docs/guides/auth/passwords)

从项目的 API Keys 配置取得 **publishable key**；旧项目的 **anon key** 也可兼容。不要填入 `sb_secret_...` 或 `service_role`，配置校验会拒绝这两类密钥。publishable / anon 是可以给前端的公开配置；数据库密码始终只放后端环境。[官方 API Keys 说明](https://supabase.com/docs/guides/getting-started/api-keys)

在启动后端的 PowerShell 窗口设置：

```powershell
$env:ACCOUNTING_AUTH_MODE = 'supabase'
$env:ACCOUNTING_SUPABASE_URL = 'https://YOUR_PROJECT_REF.supabase.co'
$env:ACCOUNTING_SUPABASE_PUBLISHABLE_KEY = 'sb_publishable_YOUR_PUBLIC_KEY'
$env:ACCOUNTING_AUTH_AUDIENCE = 'authenticated'
$env:ACCOUNTING_SEED = '0'
```

项目 URL 必须是固定 HTTPS origin，不含 `/auth/v1`、账号密码、查询参数或 fragment。支持 Supabase 自定义 HTTPS 域名。服务端自行从该 origin 构造 issuer、JWKS 和 GetUser 地址；请求者不能指定密钥地址。

`GET /api/auth/config` 仅返回登录模式与上述公开 Supabase 配置。`GET /api/me` 在登录后同步已验证的应用身份、返回事务所成员关系和当前权限；没有事务所时仍返回 `200` 和空 `firms`。此时进入开户或等待授权流程，不能自动加入示例事务所或其他事务所。

## 少数试点用户的开通顺序

1. 管理员在 Supabase Dashboard 的 **Authentication → Users** 手动创建 Email / Password 测试账号，并确认其邮箱状态。采用“创建用户”，避免使用“发送邀请邮件”；本轮代码不会代建远端账号或发邀请。请为每个人使用独立账号。[官方用户管理说明](https://supabase.com/docs/guides/auth/managing-user-data)
2. 首个事务所负责人登录会计工作台，在应用中初始化自己的事务所。这个操作只能为当前已验证用户建立新事务所及负责人关系，不能领取已有事务所。
3. 其他会计和企业人员先登录一次，使应用记录其已验证的用户 ID / 邮箱。已有负责人随后在应用授权界面分配成员角色、客户企业或具体账套范围。
4. 录入人员只获得所需客户 / 账套的录入范围；会计复核者取得单独审核权限。不要为方便试点把所有人设为负责人。
5. 先用虚构流水走通导入 → 建议 → 审核 → 导出。再用两个试点账号交叉验证：非授权账套不可读取、录入者不可审核、撤权后不可继续访问。

Supabase user metadata 只可用于显示姓名。`user_metadata.role`、`app_metadata.firm_id` 或 JWT 中自定义的部门字段，都不替代应用的成员、企业和账套授权。邮箱是授权查找线索，实际关系绑定经过认证的用户 ID。

应用授权 API：

| 操作 | 接口 | 约束 |
| --- | --- | --- |
| 初始化新事务所 | `POST /api/firms`，`{name}` | 当前已验证用户成为新事务所负责人 |
| 查看事务所成员 | `GET /api/firm/members` | 必须有事务所管理权限 |
| 授予事务所成员 | `PUT /api/firm/members`，`{email,role}` | 目标先登录一次；角色为 `admin/operator/reviewer/viewer` |
| 授予账套范围 | `PUT /api/books/{book_id}/members/{user_id}`，`{role}` | 角色为 `editor/reviewer/viewer`，授予者须具备该范围的管理权 |
| 授予企业范围 | `PUT /api/companies/{company_id}/members`，`{email,role,department_id?}` | 企业角色为 `owner/admin/finance/employee`，部门关系限制在该企业 |

事务所负责人可管理本事务所账套。一般成员的事务所角色并不自动授予所有账套；具体账套角色决定可读取、录入、审核的范围。企业成员关系也不能把普通企业员工提升成事务所会计。

## 服务端校验和故障处理

此 MVP 的邮箱授权要求在线用户记录存在有效的 `email_confirmed_at`。未确认邮箱不能成为应用授权身份，避免按未核实的邮箱授予别人账号的职责。

非对称签名仅允许 **ES256 / RS256**。PyJWT 验证签名与固定项目 issuer、配置 audience、`exp`、`iat`、`sub`；五项声明必须存在，用户 ID 必须是 UUID。拒绝 `none`、其他算法、未知 `kid`、篡改签名、跨项目令牌和过期令牌。JWKS 缓存五分钟，未知 `kid` 刷新一次，支持项目密钥轮换。[官方 JWT / JWKS 说明](https://supabase.com/docs/guides/auth/jwts)

每次请求还向固定项目 `GET /auth/v1/user` 在线核验。legacy HS256 项目完全依靠这次在线验证，不配置或复制 Supabase JWT 共享 secret；在线成功后才对同一个完整令牌检查 issuer、audience、过期等声明。最终用户 ID / 邮箱来自在线响应，并须与令牌 subject 一致。

非法令牌或用户被在线服务拒绝返回 `401`，前端需要重新登录。网络超时、限流、JWKS 故障或异常服务响应返回 `503`，应用不会把服务故障当作匿名身份或放行会计操作。网络 I/O 由同步认证依赖在线程池执行，不能把此同步认证器直接放入异步事件循环。

**注销的边界**：Supabase 注销会撤销刷新会话，但已发出的 access token 按官方设计可能仍有效到 `exp`。在线 GetUser 不等于逐次读取 `auth.sessions`；此 MVP 不宣称 Supabase 签出后旧令牌立即吊销。应用成员 / 账套授权每次从数据库校验，业务撤权即刻生效。后续若敏感操作要求会话即时吊销，应增加已验证 `session_id` 对 `auth.sessions` 的最小权限存在性检查，并单独验证该部署能力。[官方签出说明](https://supabase.com/docs/guides/auth/signout)、[官方会话与即时吊销说明](https://supabase.com/docs/guides/auth/sessions#how-to-ensure-an-access-token-jwt-cannot-be-used-after-a-user-signs-out)

## 离线验证

```powershell
python -m pytest nexus_backend/tests/test_auth_supabase.py -q
```

测试本地生成 RSA / EC 测试密钥，使用 `httpx.MockTransport` 模拟官方响应，覆盖合法身份、算法与签名、声明、密钥轮换、跨项目令牌、秘密密钥误配、服务不可用、匿名与停用用户。测试不会连接、创建或修改远端 Supabase 账号；真实项目连通性与真实登录验收需要另行使用试点环境进行。
