# Evaluation Benchmark 基础评分闭环

## 为什么建设

当前仓库已有 12 份 MiniShop Scenario Manifest、Ground Truth 和本地 Compose E2E，
但评分主要围绕单次报告文本与必要 Evidence 是否出现，无法稳定比较根因服务、根因
类型、因果链、影响面、Tool 选择、Unsupported Claim 和成本。

本轮先建设确定性评分基础，并已把 Change Event 发布回归场景接入同一测量轨道；
Topology、Historical Incident RAG 和 Bounded Dynamic Investigation 仍属于后续阶段。

## 目标与非目标

已实现目标：

- 原地扩展现有三份 Manifest，并新增一份发布回归 Manifest，不复制第二份 Ground Truth；
- 定义严格的结构化 `RCAPrediction`；
- Ground Truth 与 Prediction 分路径加载；
- 提供无 LLM 依赖的纯计算 Scorer；
- 自动生成 `results.json` 和中文 `evaluation-report.md`；
- 用四个场景的 deterministic contract fixture 验证完整评分命令。

本轮非目标：

- 不修改生产 `RCAReport` 数据库表；
- 不从自然语言报告猜测结构化根因；
- 不把 reference Provider 运行包装成真实模型质量；
- 不声称四场景 fixture 是真实准确率；
- 不提前实现 Topology、RAG 或 Dynamic Investigation。

## 数据流与隔离

```text
MiniShop Scenario Manifest
        │
        └── Ground Truth ───────────────┐
                                        │
Agent / Stub ── Structured Prediction ──┼── Benchmark Runner
                                        │          │
                                        │          ├── results.json
                                        │          └── evaluation-report.md
                                        │
                                     Scorer only
```

Ground Truth、Forbidden Claims 和 Expected Tools 只由 Runner/Scorer 读取，不能进入
模型输入。结果文件保存评分，不复制完整 Ground Truth，从接口上减少答案泄漏风险。

## 关键类型

- `ScenarioGroundTruth`：从 Manifest 提取评分所需的根因、Evidence 类型、因果边、
  影响服务和 Tool 约束。
- `RCAPrediction`：记录结构化根因、结论状态、Evidence、关键 Claim、因果边、
  影响服务、Tool Trace、延迟、Token 和成本。
- `BenchmarkScorer`：对单次运行执行确定性评分。
- `RunScore` / `Summary`：分别保存单次和聚合指标。
- `run_benchmark`：绑定输入版本、校验场景覆盖并写出不可静默覆盖的资产。

Evidence 类型采用平台现有枚举语义 `METRIC`，没有另造文档示例中的 `METRICS`，
避免运行时和 Benchmark 出现两种同义值。

当前场景中，`deployment-regression` 要求 `CHANGE + METRIC + LOG + TRACE`，并把
“仅凭部署记录直接确认根因”列为禁止声明；另外三个场景把 `CHANGE` 设为可选 Evidence，
用于验证固定五步计划在无相关变更或存在无关变更时不会误归因。

## 已支持指标

```text
Root Service Accuracy
Root Type Accuracy
RCA Exact / Strict Match
Evidence Precision / Recall / F1
Unsupported Claim Rate
Forbidden Claim Rate
False Positive Root Cause
Causal Chain Precision / Recall / F1
Blast Radius Precision / Recall / F1
Tool Selection Accuracy
Redundant Tool Call Rate
Invalid Tool Proposal Rate
Policy Block Rate
Latency / Tool Calls / LLM Calls / Tokens / Cost
```

Scorer 采用有向 Edge Set 评估因果链，反转边不会被视为正确。无根因场景必须同时
没有因果链和影响面；Agent 强行输出根因会命中 False Positive。

## 失败策略

- Prediction 与 Ground Truth 的 Scenario ID 不一致：拒绝评分；
- 场景缺失或出现未知场景：拒绝整个 Suite；
- 元数据时间不带时区：拒绝；
- Evidence、Affected Services 等唯一集合出现重复：拒绝；
- 输出目录已有结果：拒绝覆盖；
- 模型输出 `CONFIRMED`：结构可记录，但不能通过 Agent 严格门禁；
- 合法结果未通过评分：仍生成资产并返回退出码 `1`；
- 输入、合同或 I/O 错误：返回退出码 `2`。

## 如何验证

```powershell
uv run pytest -q `
  tests/unit/ops/test_benchmark_scorer.py `
  tests/unit/ops/test_benchmark_manifest_adapter.py `
  tests/unit/ops/test_benchmark_runner.py

uv run ruff check `
  src/devops_agent_platform/evaluation `
  ops/evaluation `
  tests/unit/ops/test_benchmark_*.py
```

MiniShop Manifest 独立验证：

```powershell
uv run --project '.\MiniShop 电商下单故障演练靶场' pytest -q `
  '.\MiniShop 电商下单故障演练靶场\tests\test_scenario_manifest.py'
```

本地 Compose E2E 已验证核心场景都能完成 Incident → Workflow → Evidence → RCA Report，
但这是隔离的本地确定性 Stub 验收，不是 Real LLM 准确率，也不是 staging/production 签字。

## 已实现与未实现边界

当前已完成 Benchmark Phase 1/2 的本地可复现闭环：12 个场景 Manifest、Ground Truth
与 Runtime 隔离、结构化 Prediction 合同、确定性 Scorer、消融报告、Bad Case 输出以及
确定性合同夹具。`ops/evaluation/artifacts/minishop-v2-contract-20260818` 记录了 12/12
通过结果，并明确标记为 `contract_fixture=true`。

`RCAReport` → `RCAPrediction` Runtime Adapter 已实现并保持 Ground Truth 隔离；商业/生产
Real LLM 多次运行、真实容量和成本数据仍未完成。reference-compatible Runner 已支持多轮、Token/成本/延迟统计并完成 60 次 synthetic stub
运行（0/60），这些数字不能写成模型准确率。

## 面试可以说什么

可以说：

> 为 MiniShop 十二类故障建立了隔离 Ground Truth 与结构化 RCA 评分闭环，使用确定性
> Scorer 评估根因、Evidence、因果链、影响面、工具选择和幻觉风险，并自动生成
> 机器可读结果、消融报告和 Bad Case 清单。

不能说：

- 已获得真实 LLM 准确率提升；
- 已完成生产 Chaos 或容量验收；
- contract fixture 的 100% 通过率代表模型准确率。
