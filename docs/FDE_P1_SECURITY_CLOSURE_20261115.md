# P1 安全闭环（本地回归矩阵）

状态：**本地回归通过；不是独立渗透测试**。真实 T+、真实身份服务、真机和生产边界仍未验证。

## 1. 本轮新增能力的边界

| 能力 | 安全断言 | 证据 |
|---|---|---|
| AI 路由判定 | 只存问题 SHA-256，不存原文；写操作在创建 turn 前阻断 | `test_assistant_routing.py` 3 passed/7.71s |
| FDE 适配器目录 | 只读；manager 200、employee 403/404；`live_external=false` | `test_fde_adapters.py` 1 passed/6.41s |
| 偏差归因 | 只同企业/同事务所两账簿；`causal_claim=false` | `test_profit_variance_api.py` 1 passed；PG 7 passed |
| 事务所级撤员 | 撤员后原复核人不能读/审大凭证，制单人仍可读 | `test_large_voucher.py` 7 passed/10.06s |
| 价值基线 | 系统耗时 `labour_time_verified=false`，不能冒充人工工时 | `test_value_baseline.py` 1 passed |

## 2. 既有安全回归

| 套件 | 结果 | 说明 |
|---|---|---|
| `test_admin_security.py` | **34 passed/0.35s** | 管理员权限、越权、输入边界 |
| `test_access_scope_policy.py` + `test_auth_supabase.py` | **125 passed/18.95s** | 范围策略、认证边界 |
| `test_upload_binding_http.py` | **6 errors，0 product failures** | 本机 Starlette 0.35 `TestClient(client=...)` 不兼容；不计通过、不改产品断言 |

## 3. 明确未完成

- 独立动态扫描/渗透测试：未执行；
- 真实身份服务、真实 T+、真实通知/模型：未连接；
- 多节点/生产网络边界：未验证；
- 上传绑定 HTTP 套件需在锁定 Starlette>=1.0 的环境重跑；
- P0-5 真实 T+ 模板认证仍为外部门槛。

## 4. 结论

本地可复现的安全回归证据已补齐，但不能表述为“已通过安全认证”或“生产安全验收完成”。

## 上传绑定 HTTP 复核（2026-11-16）

- `test_upload_binding_http.py` 6 passed/16.84s：事件、intake、case、material、银行导入、发票导入六类上传均绑定父记录并恰好消费一次。
- 本机 Starlette 兼容层已由 `test_business_api.py::client` 的 TypeError 回退处理，不再记为环境阻断。
