# DevOps Agent Evaluation Benchmark 实施规范

> 目标仓库：`https://github.com/ikang2001/devops-agent-platform`
>
> 用途：直接交给 Codex / Claude Code / Cursor / 其他编程 Agent，作为 DevOps Agent / AIOps 项目的评测体系建设任务书。
>
> 本文是《DevOps Agent AIOps 项目完善改造任务书》的补充文档，专门解决：
>
> **“项目改完以后，怎么证明真的变好了？”**

---

# 0. 总目标

当前项目已经具备：

```text
MiniShop
→ Fault Injection
→ Prometheus / Loki / Tempo
→ Alertmanager
→ Incident
→ RCA Workflow
→ Evidence
→ RCA Report
```

当前 MiniShop Scenario Manifest 已经具备 Ground Truth 的雏形，因此本次目标不是再造一套 Demo，而是升级成：

> **可重复、可量化、可做消融实验、可做工程故障注入的 AIOps Benchmark。**

最终评测必须回答四类问题：

1. **RCA 是否更准？**
2. **Evidence 是否更完整、幻觉是否更少？**
3. **Agent 调查是否更高效？**
4. **系统异常时是否可靠恢复？**

---

# 1. 评测体系总架构

最终评测分成四层：

```text
┌──────────────────────────────┐
│ Layer 1：Deterministic E2E   │
│ Stub / 固定输出 / 可重复     │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ Layer 2：Real LLM Benchmark  │
│ RCA / Evidence / Tool / Cost │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ Layer 3：Ablation Study      │
│ Fixed / Change / Topology... │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ Layer 4：Load & Chaos        │
│ Worker / Kafka / DB / Tool   │
└──────────────────────────────┘
```

四层不能混在一起。

---

# 2. 评测原则

## 2.1 不允许只看“最终回答像不像”

禁止仅使用：

```text
LLM-as-a-Judge
```

作为唯一判断依据。

应优先使用结构化 Ground Truth 自动比较。

LLM Judge 只能作为补充指标。

---

## 2.2 不允许只跑成功案例

Benchmark 必须同时包含：

```text
正常故障
误导性故障
历史相似但根因不同
无真实故障
部分 Observability 缺失
Tool Timeout
```

---

## 2.3 不允许只跑一次

真实 LLM 存在随机性。

建议：

```text
每个场景至少运行 5 次
```

若 12 个场景：

```text
12 × 5 = 60 runs
```

如成本允许：

```text
10 runs / scenario
```

更好。

---

## 2.4 所有实验必须固定版本

每次 Benchmark 输出必须保存：

```text
git_commit
model_provider
model_name
temperature
prompt_version
investigation_policy
scenario_version
benchmark_version
timestamp
```

否则不同版本结果不可比较。

---

# 3. Baseline 必须冻结

先定义 Baseline：

```text
baseline_fixed_v1
```

能力：

```text
Metrics
→ Logs
→ Traces
→ Runbooks
→ RCA
```

调查策略：

```text
fixed_default
```

必须在新功能开发前或开发完成后通过 Feature Toggle 恢复该能力。

后续版本：

```text
V1 = baseline_fixed_v1

V2 = V1 + Change Event

V3 = V2 + Service Topology / Blast Radius

V4 = V3 + Historical Incident Retrieval

V5 = V4 + Bounded Dynamic Investigation
```

所有新优化必须和 Baseline 做对比。

---

# 4. Benchmark 场景集合

目标至少 12 个场景。

---

## Scenario 01：payment-error

```text
id: payment-error
root_service: payment-service
root_type: application_error
```

主要考察：

```text
Logs
Traces
```

Required Evidence：

```text
LOG
TRACE
```

Forbidden Claims：

```text
inventory-db-timeout
deployment-regression
redis-failure
```

---

## Scenario 02：inventory-db-timeout

```text
id: inventory-db-timeout
root_service: inventory-service
root_resource: postgres
root_type: dependency_timeout
```

Required Evidence：

```text
METRICS
LOG
TRACE
```

Causal Chain：

```text
postgres
→ inventory-service
→ checkout-service
```

---

## Scenario 03：checkout-latency

```text
id: checkout-latency
root_service: checkout-service
root_type: latency
```

Required Evidence：

```text
METRICS
TRACE
```

---

## Scenario 04：deployment-regression

```text
id: deployment-regression
root_service: payment-service
root_type: deployment_regression
root_resource: payment-service:v2
```

Required Evidence：

```text
METRICS
CHANGE
LOG or TRACE
```

必须验证：

```text
没有 Change Tool 时明显更难定位
```

---

## Scenario 05：config-regression

```text
id: config-regression
root_service: checkout-service
root_type: configuration_error
```

Required Evidence：

```text
CHANGE
LOG
```

---

## Scenario 06：redis-latency

```text
id: redis-latency
root_service: inventory-service
root_resource: redis
root_type: dependency_latency
```

主要测试：

```text
Topology
Blast Radius
```

---

## Scenario 07：connection-pool-exhaustion

```text
id: connection-pool-exhaustion
root_service: inventory-service
root_resource: postgres_pool
root_type: resource_exhaustion
```

Required Evidence：

```text
METRICS
LOG
```

---

## Scenario 08：third-party-api-timeout

```text
id: third-party-api-timeout
root_service: payment-service
root_resource: third-party-payment
root_type: external_dependency_timeout
```

主要测试：

```text
Topology
Trace
```

---

## Scenario 09：cascading-failure

示例：

```text
postgres
→ inventory
→ checkout
→ order-api
```

主要测试：

```text
Causal Chain
Blast Radius
Root vs Affected Service
```

必须确保：

```text
checkout 不是根因，只是受影响服务
```

---

## Scenario 10：known-error-repeat

构造历史事故：

```text
历史 Incident：
payment 500
root cause = payment dependency timeout
```

本次故障与历史高度相似。

主要测试：

```text
RAG 是否提高定位效率
```

---

## Scenario 11：misleading-history

历史事故：

```text
payment 500
root cause = Redis
```

本次：

```text
payment 500
root cause = deployment regression
```

主要测试：

```text
RAG 抗误导
现场 Evidence 优先级
```

必须确保：

```text
历史知识不能覆盖当前现场证据
```

---

## Scenario 12：false-positive-alert

生成告警，但实际没有真实业务故障。

期望：

```text
conclusion_status = UNDETERMINED
或
NO_ACTIONABLE_ROOT_CAUSE
```

禁止：

```text
强行编造根因
```

主要测试：

```text
拒绝错误归因能力
```

---

# 5. Scenario Manifest Schema

建议统一升级 Scenario Manifest。

示例：

```json
{
  "scenario_id": "deployment-regression",
  "version": "1.0",
  "description": "payment deployment regression",

  "injection": {
    "type": "deployment_regression",
    "target": "payment-service"
  },

  "trigger": {
    "endpoint": "/checkout",
    "requests": 20
  },

  "cleanup": {
    "required": true
  },

  "expected_signals": {
    "metrics": true,
    "logs": true,
    "traces": true,
    "changes": true,
    "topology": false
  },

  "ground_truth": {
    "root_cause_service": "payment-service",
    "root_cause_type": "deployment_regression",
    "root_cause_resource": "payment-service:v2",

    "required_evidence_types": [
      "METRICS",
      "CHANGE"
    ],

    "optional_evidence_types": [
      "LOG",
      "TRACE"
    ],

    "causal_chain": [
      ["payment-service:v2", "payment-service"],
      ["payment-service", "checkout-service"]
    ],

    "affected_services": [
      "payment-service",
      "checkout-service"
    ],

    "forbidden_claims": [
      "inventory-service is the root cause",
      "redis is unavailable"
    ]
  }
}
```

---

# 6. Agent 输出必须结构化

为了自动评分，RCA Report 不能只有自然语言。

建议正式 Report 包含：

```json
{
  "root_cause": {
    "service": "payment-service",
    "type": "deployment_regression",
    "resource": "payment-service:v2"
  },

  "conclusion_status": "CANDIDATE",

  "confidence": 0.82,

  "evidence_ids": [
    "ev-metric-1",
    "ev-change-1",
    "ev-log-2"
  ],

  "evidence_types": [
    "METRICS",
    "CHANGE",
    "LOG"
  ],

  "causal_chain": [
    {
      "from": "payment-service:v2",
      "to": "payment-service",
      "evidence_ids": ["ev-change-1"]
    },
    {
      "from": "payment-service",
      "to": "checkout-service",
      "evidence_ids": ["ev-trace-1"]
    }
  ],

  "affected_services": [
    "payment-service",
    "checkout-service"
  ]
}
```

---

# 7. 核心 RCA 指标

---

## 7.1 Root Cause Service Accuracy

公式：

```text
正确 root service 数
/
总场景数
```

例如：

```text
10 / 12 = 83.33%
```

---

## 7.2 Root Cause Type Accuracy

比较：

```text
deployment_regression
dependency_timeout
resource_exhaustion
application_error
...
```

---

## 7.3 RCA Exact Match / Top-1 Accuracy

只有：

```text
service
+
type
```

都正确才算 Top-1 正确。

如果 resource 也要求，可增加：

```text
Strict RCA Accuracy
```

---

# 8. Evidence 指标

---

## 8.1 Evidence Recall

公式：

```text
命中的 required evidence 类型数量
/
Ground Truth required evidence 类型数量
```

例如 Ground Truth：

```text
METRICS
LOG
CHANGE
```

Agent：

```text
METRICS
CHANGE
```

则：

```text
Recall = 2 / 3
```

---

## 8.2 Evidence Precision

如果 Agent 调查拿了大量无关 Evidence，也应惩罚。

定义：

```text
与 Ground Truth 相关的 Evidence
/
Agent 收集的全部 Evidence
```

---

## 8.3 Evidence F1

```text
2 * Precision * Recall
/
(Precision + Recall)
```

---

# 9. Unsupported Claim Rate

核心指标。

定义“关键 Claim”：

```text
root cause
recent change
dependency failure
affected service
causal edge
```

每个 Claim 必须引用 Evidence。

公式：

```text
没有有效 Evidence 支撑的关键 Claim 数
/
全部关键 Claim 数
```

目标：

```text
越低越好
```

理想：

```text
0%
```

---

# 10. Forbidden Claim Rate

根据 Manifest：

```text
forbidden_claims
```

统计：

```text
命中 forbidden claim 的运行次数
/
总运行次数
```

这是安全指标。

---

# 11. False Positive Root Cause Rate

用于：

```text
false-positive-alert
```

公式：

```text
无真实根因场景中仍然输出明确根因的次数
/
无真实根因场景总次数
```

越低越好。

---

# 12. Causal Chain 评测

将因果链转换成：

```text
Edge Set
```

Ground Truth：

```text
postgres → inventory
inventory → checkout
```

Prediction：

```text
postgres → inventory
payment → checkout
```

计算：

```text
Precision
Recall
F1
```

---

# 13. Blast Radius 评测

Ground Truth：

```text
checkout
payment
order-api
```

Prediction：

```text
checkout
payment
notification
```

则：

```text
TP = 2
FP = 1
FN = 1
```

计算：

```text
Blast Radius Precision
Blast Radius Recall
Blast Radius F1
```

---

# 14. Tool Selection 指标

Bounded Dynamic Investigation 上线后必须测。

Ground Truth Manifest 可以定义：

```text
expected_tool_types
forbidden_tool_types
```

例如：

```text
deployment-regression
```

Expected：

```text
metrics.query
changes.query
logs.query
```

Forbidden：

```text
remediation.execute
shell
```

---

## 14.1 Tool Selection Accuracy

可以按场景判断：

```text
应该调用的关键 Tool 是否被调用
```

---

## 14.2 Redundant Tool Call Rate

公式：

```text
重复且没有新增 Evidence 的 Tool Call 数
/
全部 Tool Call 数
```

---

## 14.3 Invalid Tool Proposal Rate

模型提出：

```text
不存在 Tool
无权限 Tool
高风险 Tool
```

的比例。

同时统计：

```text
Policy Validator Block Rate
```

---

# 15. 调查效率指标

动态调查不能只追求准确率。

记录：

```text
investigation_steps
tool_calls
llm_calls
total_duration
```

统计：

```text
Average Investigation Steps
Average Tool Calls
P50 Tool Calls
P95 Tool Calls
```

---

# 16. 延迟指标

至少：

```text
Alert Receive P95
Incident Create P95
RCA Queue Delay P95
RCA Execution P50
RCA Execution P95
Tool P95
LLM P95
End-to-End RCA P95
```

---

# 17. Token 与成本指标

每次 LLM 调用保存：

```text
provider
model
prompt_tokens
completion_tokens
total_tokens
estimated_cost
latency_ms
```

RCA 级统计：

```text
LLM Calls / RCA
Tokens / RCA
Cost / RCA
```

Benchmark 汇总：

```text
Avg Tokens
P95 Tokens
Avg Cost
Total Cost
```

---

# 18. Baseline / Ablation 实验

最终至少跑下面 5 组。

---

## Experiment A：Baseline

```text
fixed_default
Metrics
Logs
Traces
Runbooks
```

---

## Experiment B：+ Change

```text
Baseline
+
Change
```

重点看：

```text
deployment-regression
config-regression
```

---

## Experiment C：+ Topology

```text
B
+
Topology
+
Blast Radius
```

重点看：

```text
redis-latency
third-party-timeout
cascading-failure
```

---

## Experiment D：+ RAG

```text
C
+
Historical Incident Retrieval
```

重点：

```text
known-error-repeat
misleading-history
```

---

## Experiment E：+ Dynamic

```text
D
+
bounded_dynamic_v1
```

重点比较：

```text
Accuracy 是否保持
Tool Calls 是否下降
Token 是否下降
Latency 是否下降
```

---

# 19. Change Event A/B Test

必须单独做。

同一批：

```text
deployment-regression
config-regression
```

分别：

```text
Change OFF
Change ON
```

指标：

```text
RCA Accuracy
Evidence Recall
Unsupported Claim Rate
Latency
Tool Calls
```

---

# 20. RAG 三组测试

必须：

## A

```text
无历史案例
```

## B

```text
正确相似历史案例
```

## C

```text
错误但高度相似历史案例
```

目标：

```text
B 提升
C 不被带偏
```

如果 C 明显降低准确率，需要调整：

```text
Knowledge Evidence 权重
现场证据优先级
Rerank
Prompt
Confidence Policy
```

---

# 21. Dynamic Investigation 对比实验

固定流程可能：

```text
Metrics
Logs
Traces
Change
Topology
Runbook
RAG
```

动态流程：

```text
Metrics
→ Logs
→ Change
→ Stop
```

对比：

```text
RCA Accuracy
Evidence Recall
Average Steps
Average Tool Calls
Latency
Tokens
Cost
```

动态版本只有在：

```text
准确率不显著下降
+
效率明显提升
```

时才算优化成功。

---

# 22. Deterministic E2E

继续保留 Stub。

目的不是测模型聪明程度。

测试：

```text
HTTP
Incident
Outbox
Kafka
Consumer
Lease
Tool
Evidence
Report Contract
Policy
Permission
Audit
```

特点：

```text
100% 可重复
```

必须进 CI。

---

# 23. Real LLM Benchmark

Real LLM Benchmark 与 CI 分开。

建议命令：

```bash
uv run python -m ops.evaluation.run_benchmark \
  --suite minishop-v2 \
  --policy bounded_dynamic_v1 \
  --provider openai \
  --runs-per-scenario 5
```

或者项目等价 CLI。

Real Benchmark 默认：

```text
不在普通 PR CI 自动运行
```

避免成本和随机性。

---

# 24. LLM 参数固定

每份报告记录：

```text
model
provider
temperature
top_p
max_tokens
prompt_version
```

推荐评测：

```text
temperature = 0
```

或模型允许的最低随机性参数。

---

# 25. Worker Crash Test

场景：

```text
RCA RUNNING
↓
Worker A claim
↓
执行到一半
↓
kill Worker A
```

观察：

```text
lease expiration
reclaim
new owner
resume
```

记录：

```text
recovery_time_seconds
lost_workflow_count
duplicate_completion_count
fence_rejection_count
```

通过标准：

```text
lost_workflow_count = 0
duplicate_successful_completion = 0
```

---

# 26. Kafka Failure Test

场景：

```text
DB commit
↓
Kafka unavailable
```

期望：

```text
Outbox Event 仍存在
```

Kafka 恢复：

```text
Outbox publish
→ Kafka
→ RCA
```

记录：

```text
lost_event_count
duplicate_workflow_count
outbox_recovery_seconds
```

---

# 27. PostgreSQL Failure Test

测试：

```text
API 写入期间 DB down
Worker 执行期间 DB down
Heartbeat 时 DB down
```

检查：

```text
状态是否损坏
是否错误标记成功
是否出现双完成
恢复后是否能继续
```

---

# 28. Observability Tool Failure Test

例如：

```text
Loki timeout
Tempo 500
Prometheus unavailable
```

验证：

```text
continue_on_step_failure OFF
→ fail fast
```

以及：

```text
continue_on_step_failure ON
→ partial evidence
→ confidence cap
→ summary 标记 partial
→ 不允许 CONFIRMED
```

---

# 29. Load Test

建议 k6 或 Locust。

至少档位：

```text
100 alerts/min
500 alerts/min
1000 alerts/min
```

如机器不足可以调整，但必须记录真实配置。

---

# 30. Load Test 指标

监控：

```text
HTTP P95
Error Rate
DB Pool
Outbox Backlog
Kafka Consumer Lag
RCA Queue Delay
RCA Completion Rate
CPU
Memory
```

目标不是硬写某个数字。

目标是得到真实容量边界。

---

# 31. Benchmark Run 数据模型

建议每次 RCA 运行保存：

```json
{
  "run_id": "bench-xxx",
  "benchmark_version": "2.0",
  "scenario_id": "deployment-regression",
  "scenario_version": "1.0",

  "git_commit": "abc123",

  "policy": "bounded_dynamic_v1",

  "model": {
    "provider": "openai",
    "name": "xxx",
    "temperature": 0
  },

  "result": {
    "root_cause_service": "payment-service",
    "root_cause_type": "deployment_regression",
    "confidence": 0.82
  },

  "ground_truth": {
    "root_cause_service": "payment-service",
    "root_cause_type": "deployment_regression"
  },

  "metrics": {
    "root_service_correct": true,
    "root_type_correct": true,
    "rca_exact_match": true,
    "evidence_precision": 0.80,
    "evidence_recall": 1.0,
    "unsupported_claim_rate": 0.0,
    "forbidden_claim": false,
    "causal_chain_f1": 1.0,
    "blast_radius_f1": 1.0,
    "tool_calls": 4,
    "investigation_steps": 4,
    "llm_calls": 2,
    "latency_ms": 7230,
    "total_tokens": 4812
  }
}
```

---

# 32. results.json

最终统一输出：

```text
ops/evaluation/artifacts/<benchmark-id>/results.json
```

Schema：

```json
{
  "benchmark": {},
  "runs": [],
  "summary": {}
}
```

Summary 至少：

```json
{
  "total_runs": 60,
  "rca_top1_accuracy": 0.85,
  "root_service_accuracy": 0.90,
  "root_type_accuracy": 0.86,
  "evidence_precision": 0.82,
  "evidence_recall": 0.91,
  "unsupported_claim_rate": 0.03,
  "forbidden_claim_rate": 0.01,
  "causal_chain_f1": 0.81,
  "blast_radius_f1": 0.84,
  "avg_tool_calls": 4.2,
  "avg_llm_calls": 2.1,
  "p95_latency_ms": 9100,
  "avg_tokens": 5100
}
```

数字仅示例。

---

# 33. evaluation-report.md

Benchmark 后自动生成：

```text
ops/evaluation/artifacts/<benchmark-id>/evaluation-report.md
```

必须自动包含：

```text
环境
Git Commit
Model
Prompt
Policy
Scenario 数
Runs 数
总体指标
每场景指标
失败案例
Forbidden Claims
Unsupported Claims
Token / Cost
Latency
```

---

# 34. Ablation Report

另外生成：

```text
ablation-report.md
```

表格：

| Version | RCA Acc | Evidence Recall | Unsupported | Avg Tool Calls | P95 | Tokens |
|---|---:|---:|---:|---:|---:|---:|
| Baseline | REAL | REAL | REAL | REAL | REAL | REAL |
| + Change | REAL | REAL | REAL | REAL | REAL | REAL |
| + Topology | REAL | REAL | REAL | REAL | REAL | REAL |
| + RAG | REAL | REAL | REAL | REAL | REAL | REAL |
| + Dynamic | REAL | REAL | REAL | REAL | REAL | REAL |

禁止写假数据。

---

# 35. Bad Case 输出

所有失败必须进入：

```text
bad_cases.jsonl
```

每条：

```json
{
  "scenario_id": "xxx",
  "run_id": "xxx",
  "failure_type": "WRONG_ROOT_CAUSE",
  "expected": {},
  "actual": {},
  "tool_trace": [],
  "evidence_ids": [],
  "prompt_version": "v3"
}
```

---

# 36. Bad Case 分类

至少分类：

```text
TOOL_SELECTION_ERROR
TOOL_PARAMETER_ERROR
TOOL_TIMEOUT
RETRIEVAL_MISS
RERANK_ERROR
MISLEADING_HISTORY
INSUFFICIENT_EVIDENCE
WRONG_ROOT_SERVICE
WRONG_ROOT_TYPE
CAUSAL_CHAIN_ERROR
BLAST_RADIUS_ERROR
UNSUPPORTED_CLAIM
FORBIDDEN_CLAIM
LLM_SCHEMA_ERROR
POLICY_BLOCK
WORKFLOW_RECOVERY_ERROR
```

---

# 37. Bad Case 闭环

每次优化必须回答：

```text
问题属于哪一层？
```

例如：

```text
Prompt
Retrieval
Rerank
Tool
Planner
Policy
Workflow
LLM
Data
```

不能看到错误就直接改 Prompt。

---

# 38. Regression Gate

新增 Benchmark Gate。

但区分：

## CI Gate

只使用：

```text
Deterministic Stub
```

要求：

```text
安全率 100%
契约 100%
fixed policy regression 100%
```

## Offline LLM Gate

例如：

```text
RCA Accuracy 不低于 baseline
Forbidden Claim Rate 不上升
Unsupported Claim Rate 不上升
Token 成本不超过设定预算
```

不要自动发布模型。

只进入人工评审。

---

# 39. 推荐目录结构

建议：

```text
ops/
└── evaluation/
    ├── README.md
    ├── run_benchmark.py
    ├── scorer.py
    ├── schemas.py
    ├── reporter.py
    ├── ablation.py
    ├── bad_cases.py
    │
    ├── suites/
    │   └── minishop_v2.yaml
    │
    └── artifacts/
```

具体按当前仓库结构调整。

---

# 40. Scorer 设计

不要做一个大函数。

建议：

```text
RootCauseScorer
EvidenceScorer
ClaimScorer
CausalChainScorer
BlastRadiusScorer
ToolScorer
EfficiencyScorer
```

最终：

```text
BenchmarkScorer
```

组合。

---

# 41. 测试 Scorer 本身

Scorer 必须有单测。

例如：

```text
完全正确
完全错误
部分 Evidence
重复 Evidence
空 Prediction
无根因场景
Causal Chain 顺序错误
Blast Radius 多报
Forbidden Claim
```

避免评分程序本身出错。

---

# 42. Ground Truth 不能让模型看到

重要。

模型 Runtime 不允许读取：

```text
ground_truth
forbidden_claims
expected_tools
```

这些字段只允许：

```text
Benchmark Runner / Scorer
```

读取。

必须防止：

```text
测试答案泄漏进 Prompt
```

---

# 43. Scenario Isolation

每个 Scenario：

```text
cleanup
→ reset state
→ inject
→ trigger
→ wait
→ RCA
→ score
→ cleanup
```

必须保证：

```text
上一场景数据不会污染下一场景
```

尤其：

```text
ChangeEvent
Knowledge
Alert
Incident
Topology
```

---

# 44. RAG Benchmark 数据隔离

对：

```text
known-error-repeat
misleading-history
```

必须明确 preload 哪些 Knowledge。

测试结束后清理或使用独立 namespace。

---

# 45. 可重复性

Benchmark 启动时保存：

```text
Docker image digest
Scenario hash
Config hash
Prompt hash
Knowledge dataset hash
```

至少能判断：

```text
是不是同一个测试条件
```

---

# 46. Benchmark CLI

建议支持：

```bash
# Stub 全量
uv run python -m ops.evaluation.run_benchmark \
  --suite minishop-v2 \
  --mode deterministic

# Real LLM
uv run python -m ops.evaluation.run_benchmark \
  --suite minishop-v2 \
  --mode live \
  --runs-per-scenario 5

# 单场景
uv run python -m ops.evaluation.run_benchmark \
  --scenario deployment-regression

# 消融
uv run python -m ops.evaluation.ablation \
  --suite minishop-v2
```

---

# 47. Benchmark 完成标准

- [x] 12 个场景；
- [x] Scenario Manifest 结构升级；
- [x] Ground Truth 与 Runtime 隔离；
- [x] RCA structured output；
- [x] Root Service Accuracy；
- [x] Root Type Accuracy；
- [x] RCA Exact Match；
- [x] Evidence Precision / Recall / F1；
- [x] Unsupported Claim Rate；
- [x] Forbidden Claim Rate；
- [x] False Positive Rate；
- [x] Causal Chain F1；
- [x] Blast Radius F1；
- [x] Tool 指标；
- [x] 调查步骤；
- [x] Token；
- [x] Cost；
- [x] Latency；
- [x] Stub Benchmark；
- [ ] Real LLM Benchmark；
- [x] Ablation；
- [x] Bad Cases；
- [x] Worker Crash；
- [x] Kafka Failure；
- [x] Tool Timeout；
- [x] results.json；
- [x] evaluation-report.md；
- [x] ablation-report.md；
- [x] Scorer 单测。

> 说明：`Real LLM Benchmark` 仍需接入真实模型凭据和目标环境，当前仅完成兼容 Runner、确定性 Provider、成本/延迟字段和本地合约验收，不能替代真实模型准确率证据。

---

# 48. 最终简历量化数据怎么来

只有 Benchmark 真实完成后才能写。

例如最终真实跑出：

```text
Baseline RCA Accuracy = A
Final RCA Accuracy = B

Baseline Tool Calls = C
Dynamic Tool Calls = D

Baseline Tokens = E
Final Tokens = F
```

才能计算：

```text
准确率提升：
(B - A) / A

Tool Calls 降低：
(C - D) / C

Token 降低：
(E - F) / E
```

---

# 49. 简历目标句式

等真实结果产生后，可以写：

> 构建覆盖 12 类微服务故障的 Ground Truth AIOps Benchmark，对 RCA 根因、Evidence 覆盖、因果链、影响面、工具调用和幻觉进行自动化评测；通过 Change Event、Service Topology、历史事故检索及有界动态调查，将 RCA Top-1 Accuracy 从 **A% 提升至 B%**，平均 Tool Calls 从 **C 降至 D**，Token 消耗降低 **E%**。

这里：

```text
A/B/C/D/E
```

必须来源于：

```text
results.json
```

禁止手填。

---

# 50. 最终给 AI 的执行指令

不要一次性完成全部 Benchmark。

建议顺序：

```text
Phase 1
Ground Truth Schema
+
Structured RCA Output
+
Scorer

Phase 2
现有 3 场景接入 Scorer

Phase 3
扩 12 场景

Phase 4
Real LLM Runner

Phase 5
Ablation

Phase 6
Bad Case

Phase 7
Load / Chaos

Phase 8
自动 Report
```

每阶段：

```text
先测试
后实现
再运行
最后更新文档
```

---

# 51. 最重要的原则

> **Agent 项目不是“能跑起来”就算完成，而是要能回答：准确率多少、证据够不够、为什么错、改完有没有提升、代价是多少、挂了能不能恢复。**

最终 DevOps Agent 项目应该同时拥有：

```text
业务 Benchmark
+
Agent Evaluation
+
Backend Reliability Test
+
Load / Chaos Test
```

这样它才真正从：

> “学生做的 Agent Demo”

升级为：

> **“有工程评测体系支撑的云上 AIOps / 智能故障诊断平台”。**
