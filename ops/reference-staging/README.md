# Reference Staging

这个目录提供一套可在开发机启动的完整参考环境，组合 PostgreSQL、Redpanda、
OIDC/JWKS、Prometheus、Loki、Tempo、OpenAI-compatible LLM、Ticketing 和只读
MCP Server。

它的目标是验证协议、装配、租户权限和恢复流程，不是伪造真实生产验收。所有本地
Provider 都返回 `synthetic: true`；由本环境产生的成本、延迟和恢复时间只能标记为
reference/synthetic。

```powershell
Copy-Item ops/reference-staging/.env.example ops/reference-staging/.env
docker compose --env-file ops/reference-staging/.env `
  -f ops/reference-staging/docker-compose.yml up -d --build
```

参考 OIDC 使用容器启动时生成的短期 CA，Agent 通过
`DEVOPS_AGENT_ADMIN_OIDC_CA_BUNDLE_PATH=/certs/ca.pem` 显式信任。获取 15 分钟测试
Token：

```powershell
$body = 'client_id=reference-cli&scope=openid%20workspaces%3Aread%20workspaces%3Awrite'
$token = (Invoke-RestMethod -SkipCertificateCheck -Method Post `
  -Uri https://localhost:28443/token `
  -ContentType application/x-www-form-urlencoded -Body $body).access_token
```

只读 MCP Server 使用 JSON-RPC 2.0 Streamable HTTP 形态暴露 `/mcp`，仅发布
`reference.observability.lookup`。默认 Token 只适合本机参考环境。

真实 staging 验收必须替换 OIDC、LLM、Ticketing、MCP 地址和凭据，并由 live
acceptance/benchmark/load/chaos 命令生成带目标标识与时间戳的独立证据目录。

可执行本地参考验收并生成不可覆盖报告：

```powershell
uv run python ops/reference-staging/run_acceptance.py `
  --output ops/reference-staging/artifacts/reference-20260818-r7/results.json
```

报告包含 11 项协议检查（含 Dataset Release 创建、幂等、双审核、发布后不可变和禁止创建人自审），并明确标记 `synthetic=true` 和 `production_acceptance=false`。

### Workspace、MCP 与 Dataset Release

Agent API 同时装配 Workspace 管理、HTTP MCP 和 Dataset Release 管理路由。它们使用
reference OIDC scope、复合租户键、revision/幂等条件写和同事务 Outbox 审计；可用 API
测试与迁移测试验证，不代表线上 Workspace 管理产品或组织级数据集发布签字。

### 容器 Chaos 证据

在全栈健康后运行：

```powershell
uv run python ops/chaos/run_reference_chaos.py `
  --output-directory ops/chaos/artifacts/reference-20260818
```

该命令会真实 kill/stop Agent、Redpanda、PostgreSQL 和 Loki，并记录 Worker lease
reclaim、Outbox drain、状态恢复、partial RCA 与旧 attempt fence。报告仍强制标记
`synthetic=true`、`production_acceptance=false`。
