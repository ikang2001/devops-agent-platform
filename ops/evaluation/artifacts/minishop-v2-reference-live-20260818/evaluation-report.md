# AIOps Benchmark 评测报告

> 本报告由确定性 Scorer 根据结构化 Prediction 与隔离的 Ground Truth 自动生成。
> 当前结果不等同于真实生产环境或真实 LLM 验收。

## 实验环境

- Benchmark 版本：`2.0-live`
- Git Commit：`42665bd`
- 模型：`reference-staging/reference-synthetic-model`
- Temperature：`0.0`
- Prompt 版本：`real-llm-rca-v1`
- 调查策略：`bounded_dynamic_v1`
- Scenario 版本：`1.0`
- 运行时间：`2026-08-18T08:41:44.555309+00:00`

## 汇总指标

| 指标 | 结果 |
|---|---:|
| Runs | 60 |
| 通过 Runs | 0 |
| RCA Top-1 Accuracy | 0.00% |
| Root Service Accuracy | 0.00% |
| Root Type Accuracy | 0.00% |
| Evidence Precision | 0.00% |
| Evidence Recall | 0.00% |
| Evidence F1 | 0.00% |
| Unsupported Claim Rate | 0.00% |
| Forbidden Claim Rate | 0.00% |
| False Positive Rate | 0.00% |
| Causal Chain F1 | 0.00% |
| Blast Radius F1 | 0.00% |
| Tool Selection Accuracy | 91.67% |
| 平均 Tool Calls | 4.33 |
| 平均 Investigation Steps | 4.33 |
| Redundant Tool Call Rate | 0.00% |
| Invalid Tool Proposal Rate | 0.00% |
| Policy Block Rate | 0.00% |
| 平均 LLM Calls | 1.00 |
| P50 Latency | 7 ms |
| P95 Latency | 9 ms |
| 平均 Tokens | 112.00 |
| 平均成本 | 0.000051 |

## 每次运行

| Scenario | Run | 通过 | RCA | Evidence F1 | Causal F1 | Blast F1 |
|---|---|---:|---:|---:|---:|---:|
| cascading-failure | live-cascading-failure-01 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| cascading-failure | live-cascading-failure-02 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| cascading-failure | live-cascading-failure-03 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| cascading-failure | live-cascading-failure-04 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| cascading-failure | live-cascading-failure-05 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| checkout-latency | live-checkout-latency-01 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| checkout-latency | live-checkout-latency-02 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| checkout-latency | live-checkout-latency-03 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| checkout-latency | live-checkout-latency-04 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| checkout-latency | live-checkout-latency-05 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| config-regression | live-config-regression-01 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| config-regression | live-config-regression-02 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| config-regression | live-config-regression-03 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| config-regression | live-config-regression-04 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| config-regression | live-config-regression-05 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| connection-pool-exhaustion | live-connection-pool-exhaustion-01 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| connection-pool-exhaustion | live-connection-pool-exhaustion-02 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| connection-pool-exhaustion | live-connection-pool-exhaustion-03 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| connection-pool-exhaustion | live-connection-pool-exhaustion-04 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| connection-pool-exhaustion | live-connection-pool-exhaustion-05 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| deployment-regression | live-deployment-regression-01 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| deployment-regression | live-deployment-regression-02 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| deployment-regression | live-deployment-regression-03 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| deployment-regression | live-deployment-regression-04 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| deployment-regression | live-deployment-regression-05 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| false-positive-alert | live-false-positive-alert-01 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| false-positive-alert | live-false-positive-alert-02 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| false-positive-alert | live-false-positive-alert-03 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| false-positive-alert | live-false-positive-alert-04 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| false-positive-alert | live-false-positive-alert-05 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| inventory-db-timeout | live-inventory-db-timeout-01 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| inventory-db-timeout | live-inventory-db-timeout-02 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| inventory-db-timeout | live-inventory-db-timeout-03 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| inventory-db-timeout | live-inventory-db-timeout-04 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| inventory-db-timeout | live-inventory-db-timeout-05 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| known-error-repeat | live-known-error-repeat-01 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| known-error-repeat | live-known-error-repeat-02 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| known-error-repeat | live-known-error-repeat-03 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| known-error-repeat | live-known-error-repeat-04 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| known-error-repeat | live-known-error-repeat-05 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| misleading-history | live-misleading-history-01 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| misleading-history | live-misleading-history-02 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| misleading-history | live-misleading-history-03 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| misleading-history | live-misleading-history-04 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| misleading-history | live-misleading-history-05 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| payment-error | live-payment-error-01 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| payment-error | live-payment-error-02 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| payment-error | live-payment-error-03 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| payment-error | live-payment-error-04 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| payment-error | live-payment-error-05 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| redis-latency | live-redis-latency-01 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| redis-latency | live-redis-latency-02 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| redis-latency | live-redis-latency-03 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| redis-latency | live-redis-latency-04 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| redis-latency | live-redis-latency-05 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| third-party-api-timeout | live-third-party-api-timeout-01 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| third-party-api-timeout | live-third-party-api-timeout-02 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| third-party-api-timeout | live-third-party-api-timeout-03 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| third-party-api-timeout | live-third-party-api-timeout-04 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| third-party-api-timeout | live-third-party-api-timeout-05 | 否 | 0.00% | 0.00% | 0.00% | 0.00% |

## 失败案例

- `cascading-failure` / `live-cascading-failure-01`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `cascading-failure` / `live-cascading-failure-02`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `cascading-failure` / `live-cascading-failure-03`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `cascading-failure` / `live-cascading-failure-04`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `cascading-failure` / `live-cascading-failure-05`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `checkout-latency` / `live-checkout-latency-01`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `checkout-latency` / `live-checkout-latency-02`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `checkout-latency` / `live-checkout-latency-03`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `checkout-latency` / `live-checkout-latency-04`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `checkout-latency` / `live-checkout-latency-05`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `config-regression` / `live-config-regression-01`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `config-regression` / `live-config-regression-02`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `config-regression` / `live-config-regression-03`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `config-regression` / `live-config-regression-04`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `config-regression` / `live-config-regression-05`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `connection-pool-exhaustion` / `live-connection-pool-exhaustion-01`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `connection-pool-exhaustion` / `live-connection-pool-exhaustion-02`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `connection-pool-exhaustion` / `live-connection-pool-exhaustion-03`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `connection-pool-exhaustion` / `live-connection-pool-exhaustion-04`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `connection-pool-exhaustion` / `live-connection-pool-exhaustion-05`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `deployment-regression` / `live-deployment-regression-01`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `deployment-regression` / `live-deployment-regression-02`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `deployment-regression` / `live-deployment-regression-03`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `deployment-regression` / `live-deployment-regression-04`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `deployment-regression` / `live-deployment-regression-05`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `false-positive-alert` / `live-false-positive-alert-01`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `false-positive-alert` / `live-false-positive-alert-02`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `false-positive-alert` / `live-false-positive-alert-03`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `false-positive-alert` / `live-false-positive-alert-04`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `false-positive-alert` / `live-false-positive-alert-05`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `inventory-db-timeout` / `live-inventory-db-timeout-01`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `inventory-db-timeout` / `live-inventory-db-timeout-02`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `inventory-db-timeout` / `live-inventory-db-timeout-03`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `inventory-db-timeout` / `live-inventory-db-timeout-04`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `inventory-db-timeout` / `live-inventory-db-timeout-05`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `known-error-repeat` / `live-known-error-repeat-01`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `known-error-repeat` / `live-known-error-repeat-02`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `known-error-repeat` / `live-known-error-repeat-03`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `known-error-repeat` / `live-known-error-repeat-04`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `known-error-repeat` / `live-known-error-repeat-05`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `misleading-history` / `live-misleading-history-01`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `misleading-history` / `live-misleading-history-02`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `misleading-history` / `live-misleading-history-03`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `misleading-history` / `live-misleading-history-04`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `misleading-history` / `live-misleading-history-05`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `payment-error` / `live-payment-error-01`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `payment-error` / `live-payment-error-02`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `payment-error` / `live-payment-error-03`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `payment-error` / `live-payment-error-04`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `payment-error` / `live-payment-error-05`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `redis-latency` / `live-redis-latency-01`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `redis-latency` / `live-redis-latency-02`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `redis-latency` / `live-redis-latency-03`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `redis-latency` / `live-redis-latency-04`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `redis-latency` / `live-redis-latency-05`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `third-party-api-timeout` / `live-third-party-api-timeout-01`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `third-party-api-timeout` / `live-third-party-api-timeout-02`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `third-party-api-timeout` / `live-third-party-api-timeout-03`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `third-party-api-timeout` / `live-third-party-api-timeout-04`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
- `third-party-api-timeout` / `live-third-party-api-timeout-05`：根因不匹配、必需 Evidence 不完整、因果链不匹配、影响面不匹配。
