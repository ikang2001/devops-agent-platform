# AIOps Evaluation Benchmark

本目录提供 v0.7 Benchmark 的运维入口、确定性合同夹具与黑盒泛化评测。可复用的 Schema、Scorer、
Reporter 与 Runner 位于 `src/devops_agent_platform/evaluation/`，避免把可测试业务
逻辑堆在 CLI 中。

## 当前能力

- 从 MiniShop JSON Manifest 单独加载 Ground Truth；
- 严格校验结构化 RCA Prediction；
- 计算根因、Evidence、Unsupported/Forbidden Claim、因果链、影响面和 Tool 指标；
- 输出机器可读 `results.json`；
- 输出中文 `evaluation-report.md`；
- Public / Private 场景目录隔离，Runtime 不读取 Private Ground Truth；
- Hidden Holdout、Service Rename、Error Paraphrase、Noise、Missing Evidence、Topology Shift、Composite Fault 变体；
- Benchmark Leakage Guard、Black-box Workflow terminal 评分和 `benchmark-provenance.json`；
- Generalization Report 自动生成 `generalization-report.json`、`generalization-report.md`、`generalization-bad-cases.jsonl`、`benchmark-provenance.json`、`resume-metrics.json`；
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

## v0.7 Public / Private 与 Hidden Holdout

```text
MiniShop 电商下单故障演练靶场/scenarios/public/
MiniShop 电商下单故障演练靶场/scenarios/private/
MiniShop 电商下单故障演练靶场/scenarios/holdout/public/
MiniShop 电商下单故障演练靶场/scenarios/holdout/private/
```

公开目录只供 Fault Executor 使用，禁止包含 `ground_truth`、`expected_signals`、
`required_evidence` 等答案字段；Private 目录只在 Workflow terminal 后由 Scorer 加载。
检查目录隔离：

```powershell
uv run python ops/evaluation/check_benchmark_integrity.py `
  --input '.\MiniShop 电商下单故障演练靶场\scenarios\holdout\public\mysql-lock-contention.json'
```

正式黑盒执行器命令（需要运行中的 MiniShop、Agent、Prometheus、Loki、Tempo、
Alertmanager 和真实 Provider）：

```powershell
uv run python ops/minishop-e2e/run_blackbox.py `
  --public '.\MiniShop 电商下单故障演练靶场\scenarios\public' `
  --private '.\MiniShop 电商下单故障演练靶场\scenarios\private' `
  --output 'D:\DevOpsAgentSimulation\v07-known-blackbox-<timestamp>' `
  --git-commit (git rev-parse --short HEAD) `
  --provider dashscope --model qwen-plus `
  --base-url https://dashscope.aliyuncs.com/compatible-mode/v1
```

也可以用宿主侧编排脚本启动/回收 Compose 栈；它不会把 Private Ground Truth
挂载到 Agent 或执行器容器：

```powershell
.\ops\minishop-e2e\run-v07-blackbox.ps1 `
  -RunsPerScenario 5 `
  -OutputRoot 'D:\DevOpsAgentSimulation\v07-known-blackbox-<timestamp>'
```

Hidden Holdout 使用同一黑盒链路和独立的 Public/Private 目录：

```powershell
.\ops\minishop-e2e\run-v07-blackbox.ps1 `
  -ScenarioSet Holdout `
  -RunsPerScenario 5 `
  -OutputRoot 'D:\DevOpsAgentSimulation\v07-holdout-blackbox-<timestamp>'
```

MiniShop 只实现 Holdout 的故障控制与运行时观测，不读取 `holdout/private`；Private
目录仍只由宿主 Scorer 在 Workflow terminal 后加载。

平台 RCA Report 查询接口目前只返回受限 Report/Evidence/ToolInvocation 摘要，
不暴露 Provider usage；因此黑盒适配器对无法从平台终态取得的 token/cost 使用
`0` 并保留该事实，不能把它包装成真实 token/cost 统计。若要满足完整成本验收，
需要平台进一步持久化 LLM usage 字段后再运行正式 Benchmark。

没有真实运行环境时，只能执行目录、合同和单元测试；不得把 `JsonSnapshotExecutor`、
合同夹具或 reference Provider 的结果宣称为真实 E2E/真实模型指标。正式黑盒 Runner
会额外写出不含 Private GT 的 `runtime-snapshot.json`，供后续泛化变体复用。

## v0.7 泛化报告

对已完成的 Known、Hidden Holdout 和变体结果执行：

```powershell
uv run python ops/evaluation/report_generalization.py `
  --known 'D:\DevOpsAgentSimulation\v07-known\results.json' `
  --holdout 'D:\DevOpsAgentSimulation\v07-holdout\results.json' `
  --variant-baseline 'D:\DevOpsAgentSimulation\v07-variants\clean\results.json' `
  --variant service-rename='D:\DevOpsAgentSimulation\v07-service-rename\results.json' `
  --variant noise-30='D:\DevOpsAgentSimulation\v07-noise-30\results.json' `
  --bad-cases 'D:\DevOpsAgentSimulation\v07-known\bad_cases.jsonl' `
  --bad-cases 'D:\DevOpsAgentSimulation\v07-holdout\bad_cases.jsonl' `
  --output 'D:\DevOpsAgentSimulation\v07-report'
```

`--bad-cases` 可重复传入。报告中的所有值均从 `results.json` 自动计算；缺少某个
变体不会被补写为通过。生成器还会校验每个正式结果旁的
`benchmark-provenance.json`，并输出报告级聚合 Provenance。

从真实黑盒 Runtime Snapshot 执行扰动变体：

```powershell
uv run python ops/evaluation/run_generalization_variant.py `
  --runtime-input 'D:\DevOpsAgentSimulation\v07-known-blackbox-<timestamp>\runtime-snapshot.json' `
  --public '.\MiniShop 电商下单故障演练靶场\scenarios\public' `
  --private '.\MiniShop 电商下单故障演练靶场\scenarios\private' `
  --variant noise_30 `
  --output 'D:\DevOpsAgentSimulation\v07-noise-30-<timestamp>' `
  --git-commit (git rev-parse --short HEAD) `
  --provider dashscope --model qwen3.7-plus `
  --base-url https://dashscope.aliyuncs.com/compatible-mode/v1
```

可选变体为 `service_rename`、`error_paraphrase`、`noise_10`、`noise_30`、`noise_50`、
`missing_logs`、`missing_traces`、`missing_knowledge`、`topology_shift` 和
`composite_fault`。变体输入必须来自真实黑盒 Runtime Snapshot；CLI 会拒绝 synthetic/
simulation 快照，并为 Service Rename 在临时目录中同步改写 Private GT，原始 Private
目录不变。

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
