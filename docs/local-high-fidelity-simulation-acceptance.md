# 本地高保真仿真验收记录

本文记录本项目在本地搭建的“真实开源组件 + 真实千问 API”仿真结果。它用于把任务书要求的协议、RCA、负载和故障恢复闭环跑通，不把本地结果包装成企业生产签字。

## 证据边界

每份仿真产物都必须同时满足：

```text
simulation=true
synthetic=false
production_acceptance=false
```

含义是：组件和模型调用是真实的，但环境仍是隔离的本地演练环境；没有目标企业的凭据、组织审批或线上流量，因此不能替代生产验收。

## 已完成的本地闭环

| 验收面 | 结果 | 证据 |
|---|---|---|
| PostgreSQL、Redpanda/Kafka、OIDC/JWKS、Prometheus、Loki、Tempo | 通过 | `protocol-acceptance.json` |
| Ticketing 创建幂等与持久化 | 通过 | `ticketing-acceptance.json` |
| RCA Benchmark | 12 场景 × 5 次，共 60 次真实千问调用 | `benchmark/results.json`、`benchmark/evaluation-report.md` |
| HTTP 负载 | 100/500/1000 alerts/min 三档 Python fallback 实测（非 k6） | `load-report.json` |
| Worker 崩溃、Kafka 不可用、PostgreSQL 不可用、观测超时 | 通过；观测超时保留 Partial Report 并封顶置信度 | `chaos-final/chaos-report.json` |

本轮真实千问运行使用模型 `qwen3.7-plus` 和 DashScope OpenAI-compatible Chat Completions。报告只记录 provider、模型、价格口径和 endpoint 哈希，不记录 API Key。

## 已测得的模型指标

以下数字来自独立仿真目录中的 `benchmark/results.json`，没有用 Ground Truth 回填模型输出：

| 指标 | 实测值 |
|---|---:|
| RCA Top-1 | 0.1333 |
| Evidence Precision / Recall / F1 | 1.0000 / 1.0000 / 1.0000 |
| Root Service Accuracy | 0.6667 |
| Tool Selection Accuracy | 0.9167 |
| Unsupported Claim Rate | 0 |
| 平均 Token | 2752.73 |
| 平均估算成本 | ¥0.018907/次 |
| P50 / P95 延迟 | 36,731ms / 55,872ms |

成本按报告中的 DashScope 北京公开价估算，实际账单可能受缓存、优惠和账户计费影响；延迟包含网络和服务端排队，不代表目标生产环境 SLO。

## 五变体真实消融

同一批 12 个场景又分别执行 baseline、change、topology、knowledge、dynamic 五个变体，
每个变体 12×5，共 300 次真实千问调用。Dynamic 门禁条件为 RCA Top-1 不低于 baseline、
Evidence Recall 不下降、Unsupported Claim 不增加，并且 Tool Calls、Token 或 P95 至少一项下降；
本轮实际结果为 PASS。

| 变体 | RCA Top-1 | Evidence Recall | Unsupported Claim | 平均 Tool Calls | 平均 Token | P95(ms) |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 0.1333 | 1.0000 | 0 | 3.917 | 2867.92 | 55725 |
| change | 0.1167 | 1.0000 | 0 | 4.000 | 2756.18 | 53393 |
| topology | 0.1667 | 1.0000 | 0 | 3.917 | 2814.58 | 55351 |
| knowledge | 0.2167 | 1.0000 | 0 | 3.917 | 2685.60 | 54950 |
| dynamic | 0.1833 | 1.0000 | 0 | 4.000 | 2826.85 | 54371 |

完整证据位于仿真输出目录的 `simulation-ablation-summary.json`、`ablation-report.md`，
每个变体目录还包含独立的 `results.json`、`predictions.json`、`evaluation-report.md` 和
`bad_cases.jsonl`。中途出现的瞬时 Provider 网络错误通过 `--resume` 和有上限的连接重试恢复，
失败变体没有使用已有结果或 Ground Truth 补写；重试只针对连接/传输错误，且重试耗时计入
对应请求延迟，模型输出 schema 错误不会被自动重试。

## 运行方式

```powershell
python -m ops.simulation.run_simulation `
  --env-file ops/simulation/.env.example `
  --output-root D:\DevOpsAgentSimulation\run-<timestamp>

python -m ops.simulation.run_ablation `
  --env-file ops/simulation/.env.example `
  --output-root D:\DevOpsAgentSimulation\ablation-<timestamp>
```

命令只从项目根 `.env` 读取千问凭据，不部署 Ollama/vLLM，也不会把凭据写入报告或 Git。临时目录、容器卷和日志应统一放在 D 盘，验收结束后按 `ops/simulation/README.md` 清理。

## 尚不能由本地仿真替代的事项

- 企业真实 PostgreSQL/Kafka/OIDC/观测栈/Ticketing staging 签字；
- 目标环境的 k6 峰值容量、CPU/内存、Consumer Lag、恢复时间和数据丢失证据；
- 真实生产 MCP、Workspace、组织级多审核人策展和发布审批；
- 目标环境的外部修复控制器、凭据、网络策略与回滚演练。

这些事项需要实际目标环境和授权凭据，不能通过本地数据或文档推断完成。
