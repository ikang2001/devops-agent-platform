# 真实 Provider Benchmark 与消融

`live_runner` 支持 OpenAI-compatible Chat Completions / Responses 协议。真实验收必须使用非
synthetic 输入和有效凭据，并显式传入 `--require-real`；该门禁要求完整 12 场景，默认每场景
运行 5 次，共 60 次 RCA。

```powershell
$env:DEVOPS_AGENT_BENCHMARK_API_KEY = '<从密钥管理器注入>'
uv run python -m ops.evaluation.run_live_benchmark `
  --scenarios '.\MiniShop 电商下单故障演练靶场\scenarios' `
  --input '.\ops\evaluation\artifacts\real-input.json' `
  --output '.\ops\evaluation\artifacts\real-<timestamp>' `
  --git-commit (git rev-parse --short HEAD) `
  --provider dashscope --model qwen3.5-plus `
  --base-url 'https://dashscope.aliyuncs.com/compatible-mode/v1' `
  --api-key-env DEVOPS_AGENT_BENCHMARK_API_KEY `
  --input-cost-per-million 0.0 --output-cost-per-million 0.0 `
  --runs-per-scenario 5 --require-real
```

五变体消融入口是 `python -m ops.evaluation.run_real_ablation`，变体固定为：

```text
baseline / change / topology / knowledge / dynamic
```

入口会把变体名称写入每份结果的 `investigation_policy` 元数据，并提供
`--max-concurrency` 参数；默认串行，只有确认 Provider 配额后才提高并发，避免把
限流、排队和模型效果混在同一份延迟数据里。

每个变体都必须是 12×5 的真实结果。脚本拒绝 synthetic/reference 结果，并校验 RCA、Evidence、
Unsupported Claim、Forbidden Claim、因果链、影响面、Tool、Token、Cost、P50/P95 延迟。Dynamic
只有在准确率不低于 Baseline、Evidence Recall 不下降、Unsupported Claim 不增加，且 Tool Calls、
Token 或 P95 至少一项下降时才通过门禁。

没有真实凭据时只能运行 contract/reference，不能把合成结果写成真实模型效果或生产准确率。

如果目标是高保真本地验证，请使用 [`ops/simulation/README.md`](../simulation/README.md)。
仿真入口允许真实本地 LLM，但会明确写入 `simulation=true`，并拒绝进入 Real Provider 消融门禁。
