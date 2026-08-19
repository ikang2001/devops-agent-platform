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

## 历史模型基线 v1（保留）

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

## RCA v3 最终真实复测（当前结果）

2026-08-19 使用 D 盘证据目录
`D:\DevOpsAgentSimulation\rca-v3-final-20260819T131451Z\benchmark`，通过根 `.env`
中的 DashScope `qwen3.7-plus` 完成 12 个场景 × 5 次，共 60 次真实 API 调用。模型只能从后端
候选集选择根因，因果链和影响面由证据约束的有向拓扑确定性构建。

| 指标 | v3 实测值 |
|---|---:|
| RCA Top-1 / Strict RCA | 98.33% / 98.33% |
| Root Service / Type / Resource | 98.33% / 98.33% / 98.33% |
| Candidate Recall@3 / MRR / Ranking | 100.00% / 1.0000 / 100.00% |
| Evidence Precision / Recall / F1 | 100.00% / 100.00% / 100.00% |
| Required Evidence ID Recall | 100.00% |
| Causal Chain F1 / Blast Radius F1 | 90.00% / 95.56% |
| Unsupported / Forbidden / False Positive | 0 / 0 / 0 |
| No Actionable Root Cause Accuracy | 100.00% |
| Tool Selection Accuracy | 91.67% |
| 通过 Runs | 45/60 |
| 平均 Token / 平均估算成本 | 2489.15 / ¥0.015667/次 |
| P50 / P95 延迟 | 32,622ms / 49,112ms |

剩余 15 个未通过 Run 不属于候选根因识别失败：主要是 `cascading-failure` 的运行时证据没有
明确给出 `checkout-service -> payment-service` 传播边，安全策略因此拒绝补造；另有
`misleading-history` 1 次模型结论状态结构不一致。完整指标以证据目录中的
`benchmark/results.json` 和 `benchmark/evaluation-report.md` 为准。

## RCA v2 候选链路复测（历史结果）

2026-08-19 使用本轮 `real-llm-rca-v2-candidate-review` 再次调用同一 DashScope
`qwen3.7-plus`，完成 12 场景 × 5 次，共 60 次。新结果没有覆盖上面的历史基线，
而是保存在独立目录中，便于比较 Candidate Pipeline 前后的变化。

| 指标 | RCA v2 实测值 |
|---|---:|
| RCA Top-1 / Strict RCA | 1.0000 / 1.0000 |
| Root Service / Type / Resource | 1.0000 / 1.0000 / 1.0000 |
| Candidate Recall@3 / MRR / Ranking | 1.0000 / 1.0000 / 1.0000 |
| Evidence Type / Required Evidence ID Recall | 1.0000 / 1.0000 |
| No Actionable Root Cause Accuracy | 1.0000 |
| Unsupported / Forbidden / False Positive | 0 / 0 / 0 |
| 平均 Token / 平均估算成本 | 2499.58 / ¥0.015967 |
| P50 / P95 | 33,678ms / 51,567ms |

该轮严格 Suite 只有 10/60 通过：Causal Chain F1 为 0.4667、Blast Radius F1 为
0.6611、Tool Selection Accuracy 为 0.9167。也就是说，专项的 Root Cause Candidate
目标已经命中，但因果链和影响面仍是明确的后续优化项，不能把 100% RCA Top-1 写成
“整个评测 100% 通过”。

该 v2 仿真在后续 Chaos 阶段遇到外部 TLS
`SSL UNEXPECTED_EOF_WHILE_READING` 并 fail closed；协议 11/11、Ticketing、60 次
真实 Benchmark 已完成，但当时 Load 三档实际到达率仅约 68.61/314.41/630.41 alerts/min，
且错误率为 100%，Chaos 没有形成通过报告。因此该目录是“Benchmark 完成、整体仿真
部分完成”的证据，不能声明容量或 Chaos 通过。

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

## 容量 v3 入口复测（当前结果）

证据位于 `D:\DevOpsAgentSimulation\capacity-v3-fallback-20260819T\load-report.json`。
本次使用固定到达率、有界 Worker 和 HMAC 认证的 Python fallback，持续约 60 秒；它是本地
高保真 HTTP 入口验证，不是 k6 生产签字。

| 目标 alerts/min | 实际到达率 | 错误率 | 丢弃 | P95 | 入口门禁 | 端到端门禁 |
|---:|---:|---:|---:|---:|---|---|
| 100 | 99.989 | 0% | 0 | 36.595ms | 通过 | 未通过 |
| 500 | 499.950 | 0% | 0 | 29.702ms | 通过 | 未通过 |
| 1000 | 999.820 | 0% | 0 | 34.923ms | 通过 | 未通过 |

三档入口门禁均满足到达率误差、错误率、丢弃和延迟阈值，Outbox backlog 与 Kafka lag 均恢复
为 0。端到端门禁仍未通过，因为目标栈没有暴露可计算 RCA completion rate 的指标；报告中的
`completion_rate=null` 是未观测，而不是成功或失败的估算。

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

## v4 生产级本地仿真与 RCA completion（2026-08-19）

本轮使用真实 PostgreSQL、Redpanda/Kafka、OIDC/JWKS、Prometheus、Loki、Tempo、Worker、MCP 和持久化 Ticketing 仿真服务，RCA Benchmark 通过根目录 `.env` 调用 DashScope `qwen3.7-plus`。完整实施过程、数据构建和测评方法见项目根目录的
[`生产级本地仿真环境实施与测评报告.md`](../生产级本地仿真环境实施与测评报告.md)。

最终仿真证据目录为：
`D:\DevOpsAgentSimulation\production-like-v4-20260819T222704Z-rerun`。

| 验收项 | v4 实测结果 | 证据 |
|---|---:|---|
| 协议组件 | 11/11 通过 | `protocol-acceptance.json` |
| Ticketing | 幂等建单通过 | `ticketing-acceptance.json` |
| RCA completion | 1/1，100% | `rca-completion-probe.json` |
| 100 alerts/min | 99.976/min，错误 0%，P95 53.306ms，端到端通过 | `load-report.json` |
| 500 alerts/min | 499.993/min，错误 0%，P95 41.040ms，端到端通过 | `load-report.json` |
| 1000 alerts/min | 999.979/min，错误 0%，P95 43.044ms，端到端通过 | `load-report.json` |

`run_simulation.py` 现在会在 completion probe 结束后，将平台 Prometheus 观测到的完成率写回每个容量 profile，并重新计算 `end_to_end_gate`。因此 `completion_rate=null` 只会出现在尚未执行 probe 的独立入口压测中，不会出现在完整本地仿真结果中。

基础设施目录中的 `benchmark/results.json` 是 v4 初始批次证据；最终指标以本节下方的分片复测目录为准。曾经的 v1/v2/v3 数字仅作为历史对照，不能与 v4 混合计算；所有结果继续保留 `simulation=true`、`synthetic=false`、`production_acceptance=false` 边界。

### v4 最终 RCA 分片复测

完整 60 条评测位于 `D:\DevOpsAgentSimulation\rca-v4-composite-final`。由于一次并发全量请求出现 Provider schema 错误、一次串行全量超过 30 分钟，本轮按场景分片恢复：从已完成主批次保留 50 条不受工具映射修复影响的记录，并对 `known-error-repeat`、`misleading-history` 各重新执行 5 次真实 API 调用。`merge-provenance.json` 保存输入 SHA-256、选中条数和被替换 run_id，合并器校验模型、Prompt、Scenario Hash、配置哈希及每场景 5 条约束。

| 指标 | v4 最终实测值 |
|---|---:|
| 通过 Runs | 60/60 |
| RCA Top-1 / Strict RCA | 100.00% / 100.00% |
| Root Service / Type / Resource | 100.00% / 100.00% / 100.00% |
| Candidate Recall@3 / MRR / Ranking | 100.00% / 1.0000 / 100.00% |
| Evidence Precision / Recall / F1 | 100.00% / 100.00% / 100.00% |
| Causal Chain F1 / Blast Radius F1 | 100.00% / 100.00% |
| Tool Selection Accuracy | 100.00% |
| Unsupported / Forbidden / False Positive | 0 / 0 / 0 |
| P50 / P95 延迟 | 34,478ms / 47,450ms |
| 平均 Token / 平均估算成本 | 2,536.27 / ¥0.016084/次 |

该结果是同配置真实调用的可追溯分片合并，不是单个连续 Provider 会话，也不代表目标生产环境验收。
