# 生产级本地仿真验收

本目录把 Reference staging 扩展成一套高保真本地仿真环境：使用真实的
PostgreSQL、Redpanda/Kafka、OIDC、Prometheus、Loki、Tempo、平台 Worker、MCP
和持久化 Ticketing 仿真服务；RCA Benchmark 直接调用项目根 `.env` 中的千问
兼容 API。仿真不部署 Ollama，也不使用 `provider-mock` 产生模型结果。

每一份结果都必须包含以下标记：

```text
simulation=true
synthetic=false
production_acceptance=false
```

这表示“真实组件 + 真实 API 的本地仿真”，不表示企业生产环境已经签字验收。

## 前置条件

项目根 `.env` 至少需要提供：

```text
DEVOPS_AGENT_LLM_DASHSCOPE_API_KEY=由密钥管理系统注入
DEVOPS_AGENT_LLM_DASHSCOPE_MODEL=qwen3.7-plus
```

脚本会自动使用 DashScope OpenAI-compatible 地址；也可以在仿真 `.env` 中用
`SIM_LLM_*` 覆盖。API Key 只在进程环境中传递，不会写入报告、Manifest 或 Git。

## 启动完整仿真

```powershell
python -m ops.simulation.run_simulation `
  --env-file ops/simulation/.env.example `
  --output-root D:\DevOpsAgentSimulation\run-<timestamp>
```

脚本会依次执行协议验收、Ticketing 幂等建单、12 场景 × 5 次 RCA、HTTP 负载、
Worker/Kafka/PostgreSQL/Loki 故障注入，并将所有证据写入 D 盘。默认结束后会
停止并删除仿真 Compose 容器与卷；需要留存栈排查时可加 `--keep-stack`。

## 五变体消融

```powershell
python -m ops.simulation.run_ablation `
  --env-file ops/simulation/.env.example `
  --output-root D:\DevOpsAgentSimulation\ablation-<timestamp>
```

如果 Provider 在某个变体中发生瞬时网络错误，可在同一个输出目录加
`--resume` 重启；脚本只复用通过 12×5、仿真标记校验的完整变体，部分结果会被重新执行。

消融包含 baseline、change、topology、knowledge、dynamic 五个变体，每个变体
都是 12 场景 × 5 次，并报告 RCA Top-1、Evidence Recall、Unsupported Claim、
Tool Calls、Token、P95 延迟和动态策略门禁。

## 清理

```powershell
docker compose --project-name devops-agent-simulation `
  --env-file ops/simulation/.env.example `
  -f ops/reference-staging/docker-compose.yml `
  -f ops/simulation/docker-compose.yml `
  down -v --remove-orphans
```

仿真报告可以作为本地工程验收证据，但不能替代真实企业 staging、生产凭据、
线上容量数据或组织级签字。
