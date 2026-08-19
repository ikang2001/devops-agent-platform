# AIOps Evaluation Benchmark

本目录提供 Benchmark 的运维入口和确定性合同夹具。可复用的 Schema、Scorer、
Reporter 与 Runner 位于 `src/devops_agent_platform/evaluation/`，避免把可测试业务
逻辑堆在 CLI 中。

## 当前能力

- 从 MiniShop JSON Manifest 单独加载 Ground Truth；
- 严格校验结构化 RCA Prediction；
- 计算根因、Evidence、Unsupported/Forbidden Claim、因果链、影响面和 Tool 指标；
- 输出机器可读 `results.json`；
- 输出中文 `evaluation-report.md`；
- 已有输出默认禁止覆盖；
- 合法但未通过严格门禁的实验返回退出码 `1`，输入或合同错误返回 `2`。

## 运行合同夹具

```powershell
uv run python -m ops.evaluation.run_benchmark `
  --scenarios '.\MiniShop 电商下单故障演练靶场\scenarios' `
  --input '.\ops\evaluation\fixtures\minishop-v1-predictions.json' `
  --output '.\ops\evaluation\artifacts\contract-fixture-001'
```

`fixtures/minishop-v1-predictions.json` 是 `deterministic-contract-fixture`，只验证
Manifest、结构化输出、Scorer、CLI 和报告合同。它不是实时 MiniShop E2E 结果，
没有调用真实 LLM，也不能作为项目准确率或生产性能数据。

真实实验必须使用新的输出目录，并在输入中记录真实的 Git Commit、Provider、
Model、Temperature、Prompt、Policy、Scenario 版本和带时区时间。

## MiniShop-v2 与四类输出

场景目录现在包含 12 个 Manifest。默认 Runner 为兼容旧合同只加载原四场景；明确传入
`--include-extended` 才会加载完整集合：

```powershell
$commit = git rev-parse --short HEAD
uv run python -m ops.evaluation.run_benchmark `
  --scenarios '.\MiniShop 电商下单故障演练靶场\scenarios' `
  --include-extended `
  --mode deterministic --git-commit $commit `
  --output '.\ops\evaluation\artifacts\minishop-v2-run'
```

这条命令生成 12 场景确定性合同夹具，并显式标记 `contract_fixture=true`。
如需评测真实 Agent/LLM，请改用 `--input <真实预测文件>`；仓库不提供伪造的
`minishop-v2-predictions.json`。

没有真实 LLM 时可运行只读的合同夹具模式（会显式写 `contract_fixture=true`，不可当作模型准确率）：

```powershell
uv run python -m ops.evaluation.run_benchmark `
  --scenarios '.\MiniShop 电商下单故障演练靶场\scenarios' `
  --mode deterministic --git-commit 42665bd `
  --output '.\ops\evaluation\artifacts\minishop-v2-contract'
```

每次运行生成 `results.json`、`evaluation-report.md`、`ablation-report.md` 和
`bad_cases.jsonl`。消融报告只接受各变体真实 `results.json`，不会填充假数字。

## Real-LLM-compatible Runner

`src/devops_agent_platform/evaluation/live_runner.py` 提供 Chat Completions 和
Responses 两种 OpenAI-compatible 协议、多轮运行、Token/成本/延迟统计、输入哈希和
递归 Ground Truth 泄漏检查。真实 Provider 缺少 usage 时会 fail-closed。

`RCAReportPredictionAdapter` 可把生产运行时的 `RCAReport`、Evidence 和
`ToolInvocation` 转成 `RCAPrediction`。它不读取 Ground Truth；缺少结构化根因类型时
会将候选/确认结论降级为 `UNDETERMINED`，不会从报告摘要猜测根因。

reference staging 的可重复命令如下（Provider 是故意保守的 synthetic stub）：

```powershell
uv run python ops/evaluation/build_reference_inputs.py `
  --scenarios '.\MiniShop 电商下单故障演练靶场\scenarios' `
  --output '.\ops\evaluation\artifacts\minishop-v2-reference-inputs.json'
$env:DEVOPS_AGENT_BENCHMARK_API_KEY = 'reference-llm-key'
uv run python ops/evaluation/run_live_benchmark.py `
  --scenarios '.\MiniShop 电商下单故障演练靶场\scenarios' `
  --input '.\ops\evaluation\artifacts\minishop-v2-reference-inputs.json' `
  --output '.\ops\evaluation\artifacts\minishop-v2-reference-live-<timestamp>' `
  --git-commit (git rev-parse --short HEAD) `
  --provider reference-provider --model reference-model `
  --base-url http://localhost:28081/v1 `
  --input-cost-per-million 0.25 --output-cost-per-million 1.00 `
  --runs-per-scenario 5 --allow-insecure-http
```

已生成的 reference 结果是 12×5=60 次 synthetic stub 运行，0/60 通过；它只证明
Runner、协议、成本/延迟统计和 Bad Case 结构，不代表真实模型质量。真实验收必须替换
Provider、凭据和目标标签，并保留独立输出目录。

## 输入与隔离

输入 JSON 只包含：

```text
benchmark metadata
+
structured predictions
```

Ground Truth 从 `--scenarios` 指定的独立目录读取。Runner 不会把
`ground_truth`、`forbidden_claims` 或期望工具写入 Prediction，也不会把它们提供给
Agent/LLM。当前结果文件只保存评分结果，不复制完整 Ground Truth。

## 外部环境边界

- 生产 `RCAReport` 到 `RCAPrediction` 的运行时 Adapter；
- 真实 staging 的 PostgreSQL/Kafka/OIDC/Prometheus/Loki/Tempo/LLM/Ticketing 签字；
- 商业/生产 Real LLM 多次运行与真实成本、准确率数据；reference-compatible Runner 已实现，但 stub 结果不能代替模型质量；
- 真实 staging 的 Load/Chaos 性能数字；
- Offline LLM Gate 的人工评审工作流。

本地 12 场景合同夹具、评分器和四类输出已经可重复运行，但仍不能包装成生产验收。

## 真实 Provider 与消融门禁

真实模型必须使用 [`REAL_PROVIDER.md`](REAL_PROVIDER.md) 中的入口。`run_live_benchmark`
的 `--require-real` 会拒绝 synthetic/reference Provider，并要求完整 12 场景 × 5 次运行；
`run_real_ablation` 要求 baseline、change、topology、knowledge、dynamic 五个变体各自提供
独立的 12×5 `results.json`，缺少真实输入时会 fail-closed，不生成假指标。
