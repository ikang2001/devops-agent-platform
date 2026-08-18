# AIOps Benchmark 评测报告

> 本报告由确定性 Scorer 根据结构化 Prediction 与隔离的 Ground Truth 自动生成。
> 当前结果不等同于真实生产环境或真实 LLM 验收。

## 实验环境

- Benchmark 版本：`2.0-contract`
- Git Commit：`42665bd`
- 模型：`stub/deterministic-contract`
- Temperature：`0.0`
- Prompt 版本：`no-llm-contract`
- 调查策略：`bounded_dynamic_v1`
- Scenario 版本：`1.0`
- 运行时间：`2026-08-18T05:08:36.389788+00:00`

## 汇总指标

| 指标 | 结果 |
|---|---:|
| Runs | 12 |
| 通过 Runs | 12 |
| RCA Top-1 Accuracy | 100.00% |
| Root Service Accuracy | 100.00% |
| Root Type Accuracy | 100.00% |
| Evidence Precision | 100.00% |
| Evidence Recall | 100.00% |
| Evidence F1 | 100.00% |
| Unsupported Claim Rate | 0.00% |
| Forbidden Claim Rate | 0.00% |
| False Positive Rate | 0.00% |
| Causal Chain F1 | 100.00% |
| Blast Radius F1 | 100.00% |
| Tool Selection Accuracy | 100.00% |
| 平均 Tool Calls | 3.00 |
| 平均 Investigation Steps | 3.00 |
| Redundant Tool Call Rate | 0.00% |
| Invalid Tool Proposal Rate | 0.00% |
| Policy Block Rate | 0.00% |
| 平均 LLM Calls | 0.00 |
| P50 Latency | 0 ms |
| P95 Latency | 0 ms |
| 平均 Tokens | 0.00 |
| 平均成本 | 0.000000 |

## 每次运行

| Scenario | Run | 通过 | RCA | Evidence F1 | Causal F1 | Blast F1 |
|---|---|---:|---:|---:|---:|---:|
| cascading-failure | contract-cascading-failure | 是 | 100.00% | 100.00% | 100.00% | 100.00% |
| checkout-latency | contract-checkout-latency | 是 | 100.00% | 100.00% | 100.00% | 100.00% |
| config-regression | contract-config-regression | 是 | 100.00% | 100.00% | 100.00% | 100.00% |
| connection-pool-exhaustion | contract-connection-pool-exhaustion | 是 | 100.00% | 100.00% | 100.00% | 100.00% |
| deployment-regression | contract-deployment-regression | 是 | 100.00% | 100.00% | 100.00% | 100.00% |
| false-positive-alert | contract-false-positive-alert | 是 | 100.00% | 100.00% | 100.00% | 100.00% |
| inventory-db-timeout | contract-inventory-db-timeout | 是 | 100.00% | 100.00% | 100.00% | 100.00% |
| known-error-repeat | contract-known-error-repeat | 是 | 100.00% | 100.00% | 100.00% | 100.00% |
| misleading-history | contract-misleading-history | 是 | 100.00% | 100.00% | 100.00% | 100.00% |
| payment-error | contract-payment-error | 是 | 100.00% | 100.00% | 100.00% | 100.00% |
| redis-latency | contract-redis-latency | 是 | 100.00% | 100.00% | 100.00% | 100.00% |
| third-party-api-timeout | contract-third-party-api-timeout | 是 | 100.00% | 100.00% | 100.00% | 100.00% |

## 失败案例

本次没有失败案例。
