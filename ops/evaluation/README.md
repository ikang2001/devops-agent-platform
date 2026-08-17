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

## 尚未实现

- 生产 `RCAReport` 到 `RCAPrediction` 的运行时 Adapter；
- 12 场景完整 Benchmark；
- Real LLM 多次运行器；
- Baseline/Change/Topology/RAG/Dynamic 消融；
- `bad_cases.jsonl` 自动归类；
- Worker/Kafka/PostgreSQL/Tool Chaos 与 Load Test 报告；
- Offline LLM Gate 的人工评审工作流。

因此当前只能称为“Benchmark 基础评分闭环”，不能称为完整 AIOps Benchmark。
