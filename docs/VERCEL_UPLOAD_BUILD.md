# Vercel 上传文件与构建依赖

V10 增加了 `src/e2e-runtime.test.ts`，完整 TypeScript 检查通过它引用
`playwright.config.ts` 和 `scripts/e2e_runtime.ts`。原 `.vercelignore` 排除了整个
`scripts/`，所以完整仓库能构建，Vercel 删除文件后却产生三条 TS2307。

保留完整类型检查，只将忽略规则改成 `scripts/*` 并明确放行
`scripts/e2e_runtime.ts`。该文件只依赖 `node:crypto`，不读取凭据、连接数据库或调用模型。
其余运维、迁移脚本及后端、私有文件仍排除，不修改 Vercel 路由与服务配置。

## 本地与 CI 门槛

```text
npm run test:vercel-build
```

门禁使用 Git 的忽略语义解析 `.vercelignore`，将受版本控制及未忽略的新文件过滤后复制到独立目录；
复用已安装的依赖，不下载包、不连接云服务。清除继承的应用配置并禁用本机环境文件加载，执行
实际 `npm run build`，而不是只检查规则文本。随后从隔离副本删去 helper，必须再次得到
TS2307，证明没有绕过类型检查。原仓库文件不被删改。

旧规则首先实测失败：helper 缺失和三条 TS2307；窄放行后隔离上传构建通过。
CI 的 verify 增加同一命令，原单测、完整构建及开发/预览双模式门禁保留。
这验证上传文件闭包，不替代真实 Vercel 服务部署、生产环境变量或 API 联调验收。
