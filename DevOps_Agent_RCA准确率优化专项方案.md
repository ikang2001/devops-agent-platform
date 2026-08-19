# DevOps Agent RCA 准确率优化专项方案
## 面向云上智能运维 / AIOps 故障诊断平台 v0.5.0

> 目标仓库：`https://github.com/ikang2001/devops-agent-platform`
>
> 当前版本：`v0.5.0`
>
> 本文目标：不再扩充平台功能，而是针对当前真实千问高保真仿真暴露出的核心问题，专项提升 **Evidence → Root Cause** 的最终诊断质量。
>
> 适用对象：Codex / Claude Code / Cursor / 其他编程 Agent，可直接按本文分阶段实施。

---

# 0. 当前结论

当前项目的工程侧能力已经基本完整：

```text
Alert
→ Incident
→ Outbox
→ Kafka
→ RCA Consumer
→ Bounded Dynamic Investigation
→ Metrics / Change / Logs / Traces / Topology / Knowledge / Runbook
→ Evidence
→ RCA Report
```

并已经具备：

```text
ToolRegistry
Permission
Budget
Checkpoint / Resume
Claim / Lease / Heartbeat
Owner / Attempt Fence
Alert Correlation
Topology / Blast Radius
Historical Knowledge
12 Scenario Benchmark
Bad Case
Ablation
Load / Chaos
Real LLM Simulation
```

因此本轮禁止继续新增：

```text
Multi-Agent
新 Agent Framework
新向量数据库
新 MQ
自由 ReAct
任意 Shell
自动修复
自动训练
```

当前真正的问题已经从：

> “Agent 找不到信息”

转变为：

> **“Agent 已经拿到了比较完整的 Evidence，但没有把 Evidence 稳定地转换成正确 Root Cause。”**

---

# 1. 当前真实评测数据

当前真实千问本地高保真仿真：

```text
Model: qwen3.7-plus
Provider: DashScope
12 scenarios × 5 runs
```

当前单轮 Benchmark：

| 指标 | 当前值 |
|---|---:|
| RCA Top-1 | 13.33% |
| Root Service Accuracy | 66.67% |
| Evidence Precision | 100% |
| Evidence Recall | 100% |
| Tool Selection Accuracy | 91.67% |
| Unsupported Claim Rate | 0% |
| Avg Token | 2752.73 |
| P95 Latency | 55.872s |

五变体消融：

| Variant | RCA Top-1 | Evidence Recall | Avg Tool Calls | Avg Token | P95 |
|---|---:|---:|---:|---:|---:|
| baseline | 13.33% | 100% | 3.917 | 2867.92 | 55.725s |
| change | 11.67% | 100% | 4.000 | 2756.18 | 53.393s |
| topology | 16.67% | 100% | 3.917 | 2814.58 | 55.351s |
| knowledge | 21.67% | 100% | 3.917 | 2685.60 | 54.950s |
| dynamic | 18.33% | 100% | 4.000 | 2826.85 | 54.371s |

---

# 2. 从当前数据可以推导出的核心问题

## 2.1 Tool 层基本不是主要瓶颈

当前：

```text
Tool Selection Accuracy = 91.67%
Evidence Recall = 100%
```

说明：

```text
Agent 大多数时候已经知道该查什么
并且该拿的 Evidence 基本都拿到了
```

因此继续增加更多 Tool，收益非常有限。

## 2.2 Root Service 能定位，但 Root Cause Type / Exact Match 很差

当前：

```text
Root Service Accuracy = 66.67%
RCA Top-1 = 13.33%
```

这意味着大量 Bad Case 很可能属于：

```text
服务找对
但具体根因分类错
```

例如：

```text
正确：
inventory-service
dependency_timeout

模型：
inventory-service
application_error
```

所以本轮必须重点拆解：

```text
Root Service
Root Cause Type
Root Resource
Causal Chain
```

不能继续把它们作为一个模糊自然语言答案。

## 2.3 Evidence 数量不是越多越好

当前：

```text
Evidence Recall = 100%
```

但：

```text
RCA Accuracy 很低
```

说明把所有 Evidence 全部塞进模型，不等于模型能够正确推理。尤其 Change、Historical Knowledge、Topology 都可能产生干扰信号。

## 2.4 Change 当前存在明显误导风险

消融：

```text
Baseline = 13.33%
Change   = 11.67%
```

因此必须明确：

```text
Recent Change
≠
Root Cause
```

Change 只能作为 causal candidate，不能直接提高根因置信度。

## 2.5 Knowledge 当前最有价值

当前：

```text
Knowledge = 21.67%
```

明显高于：

```text
Baseline = 13.33%
```

说明历史事故知识是有效方向，但必须继续保留 `misleading-history`，防止模型把“历史相似”误当“当前事实”。

---

# 3. 本轮优化总目标

把 Root Cause 生成流程从：

```text
Evidence
↓
LLM
↓
RCA Report
```

改造成：

```text
Evidence
↓
Evidence Normalization
↓
Root Cause Candidate Generation
↓
Candidate Evidence Binding
↓
Support / Contradiction Analysis
↓
Deterministic Candidate Scoring
↓
LLM Final Selection / Explanation
↓
RCA Report
```

核心思想：

> **先把推理空间缩小，再让模型做判断。**

---

# 4. Phase 1：先做 Bad Case 精细化归因

不要直接改 Prompt。

先把 300 次真实消融结果全部分类。

新增 Root Cause Bad Case Taxonomy：

```text
WRONG_ROOT_SERVICE
WRONG_ROOT_TYPE
WRONG_ROOT_RESOURCE
RIGHT_SERVICE_WRONG_TYPE
RIGHT_SERVICE_RIGHT_TYPE_WRONG_RESOURCE
CHANGE_OVERATTRIBUTION
HISTORY_OVERATTRIBUTION
TOPOLOGY_OVERATTRIBUTION
MISSED_CAUSAL_EDGE
REVERSED_CAUSAL_EDGE
INSUFFICIENT_DISCRIMINATION
AMBIGUOUS_EVIDENCE
SCHEMA_MAPPING_ERROR
TAXONOMY_MISMATCH
SUMMARY_REASONING_ERROR
FINAL_SELECTION_ERROR
```

最终生成：

```text
root-cause-error-breakdown.json
```

示例：

```json
{
  "total_failed_runs": 52,
  "wrong_root_service": 10,
  "right_service_wrong_type": 24,
  "wrong_resource": 8,
  "change_overattribution": 5,
  "history_overattribution": 2,
  "schema_mapping_error": 3
}
```

目标：先知道错误主要发生在哪一层。

---

# 5. Phase 2：Root Cause Taxonomy 统一

当前可能存在：

```text
模型语言
vs
Ground Truth 枚举
```

不一致。

例如：

```text
模型：database slow
Ground Truth：dependency_timeout
```

建议建立统一 `RootCauseType`：

```text
application_error
dependency_timeout
dependency_latency
deployment_regression
configuration_error
resource_exhaustion
database_failure
network_failure
external_dependency_failure
capacity_saturation
unknown
```

具体枚举必须以现有 12 Scenario Ground Truth 为准。

增加：

```text
RootCauseTaxonomyMapper
```

职责：

```text
LLM raw type
→ canonical root cause type
```

例如：

```text
"db timeout"
"database timeout"
"postgres timeout"
```

统一映射到：

```text
dependency_timeout
```

无法可靠映射则输出：

```text
unknown
```

不要硬猜。

---

# 6. Phase 3：Evidence Normalization

新增：

```text
EvidenceNormalizer
```

不同 Tool 输出统一成紧凑事实。

## Metrics

```json
{
  "type": "METRICS",
  "service": "inventory-service",
  "facts": [
    {
      "signal": "latency_p95",
      "status": "abnormal",
      "value": 2.8,
      "baseline": 0.2
    }
  ]
}
```

## Logs

```json
{
  "type": "LOG",
  "service": "inventory-service",
  "facts": [
    {
      "pattern": "database connection timeout",
      "count": 37
    }
  ]
}
```

## Change

```json
{
  "type": "CHANGE",
  "service": "payment-service",
  "facts": [
    {
      "change_type": "deployment",
      "version": "v2",
      "minutes_before_incident": 4
    }
  ]
}
```

## Trace

```json
{
  "type": "TRACE",
  "facts": [
    {
      "caller": "checkout-service",
      "callee": "inventory-service",
      "error": "timeout",
      "latency_ms": 3100
    }
  ]
}
```

最终：

```text
Raw Tool Result
↓
Evidence
↓
Normalizer
↓
Compact Facts
↓
Reasoning
```

原始超长 Payload 不直接进入最终 Root Cause Prompt。

---

# 7. Phase 4：Root Cause Candidate Generation

这是本轮最核心改造。

新增：

```text
RootCauseCandidate
```

建议 Schema：

```python
RootCauseCandidate(
    candidate_id,
    root_service,
    root_type,
    root_resource,
    supporting_evidence_ids,
    contradicting_evidence_ids,
    causal_edges,
    prior_score,
    evidence_score,
    contradiction_penalty,
    final_score
)
```

Candidate 不应只由 LLM 生成，推荐：

```text
Backend Deterministic Candidate Generator
+
LLM Candidate Supplement
```

## 规则示例

### Log Timeout

```text
inventory logs
→ database timeout
```

生成：

```text
inventory-service
dependency_timeout
postgres
```

### Trace Dependency Timeout

```text
checkout
→ inventory
timeout
```

生成：

```text
inventory-service
dependency_timeout
```

而不是 checkout-service。

### Recent Change

```text
payment deployment
4 min before incident
+
payment error_rate ↑
```

生成：

```text
payment-service
deployment_regression
```

但初始分不能过高。

### Resource Pressure

```text
CPU / memory / pool saturation
```

生成：

```text
resource_exhaustion
```

### Historical Incident

Knowledge 只能提升已有 Candidate，不允许单独创建高置信 Root Cause。

Candidate 最多 Top 3，最多不超过 5 个。

---

# 8. Phase 5：Support / Contradiction

每个 Candidate 必须明确：

```text
支持它的 Evidence
反驳它的 Evidence
缺失的关键 Evidence
```

例如 Candidate：

```text
payment-service
deployment_regression
```

Support：

```text
Change：4 分钟前发布 v2
Metrics：发布后 error_rate ↑
```

Contradiction：

```text
Logs：错误在发布前就存在
```

那么 deployment_regression 必须被降分。

---

# 9. Phase 6：Change 因果约束

新增：

```text
ChangeCausalityGuard
```

Change 只有同时满足多项条件时才能高分：

```text
Temporal Proximity
Signal Shift After Change
Same Service
No Pre-existing Error
```

例如：

```text
if recent_change
and no_metric_shift
and no_log_signature_change:
    penalty += 0.4
```

硬规则：

> **最近发生变更 ≠ 变更导致故障。**

---

# 10. Phase 7：Evidence Weighting

不同 Evidence 类型不要等权。

第一版可用如下工程初值：

```text
Direct Error Log        1.00
Trace Error Edge        0.95
Metrics Anomaly         0.80
Topology Relation       0.60
Recent Change           0.55
Historical Knowledge    0.40
Runbook                 0.20
```

这些不是最终参数，必须通过消融调整。

硬规则：

```text
Current Metrics / Logs / Traces / Change
>
Historical Knowledge
```

历史 Knowledge 只能作为 `candidate prior`，不能覆盖当前反证。

---

# 11. Phase 8：Candidate Deterministic Scoring

建议：

```text
Final Score
=
Support Score
+
Causal Consistency
+
Topology Consistency
+
Temporal Consistency
+
Historical Prior
-
Contradiction Penalty
-
Missing Evidence Penalty
```

示例：

```text
Candidate A
inventory-service
dependency_timeout

Log support        +0.30
Trace support      +0.30
Metrics support    +0.15
Topology support   +0.10
History support    +0.05
Contradiction       0

Total              0.90
```

最终只把 Top 3 给 LLM。

---

# 12. Phase 9：调整 LLM 的角色

当前 LLM 不应该再负责：

```text
从所有 Raw Evidence 自由猜根因
```

改成：

```text
Review Top Candidates
Compare Support / Contradiction
Select Best Candidate
Explain
```

Prompt 结构：

```text
System
↓
Root Cause Taxonomy
↓
Incident Summary
↓
Normalized Evidence
↓
Top 3 Candidates
↓
Support / Contradiction
↓
Selection Rules
↓
Structured Output
```

Selection Rules：

```text
1. Current Evidence 优先于 Historical Knowledge
2. Recent Change 只是相关性，不是因果性
3. Root Service 优先选择最早出现直接异常的依赖节点，而不是最先报警的上游服务
4. 不允许使用没有 Evidence ID 的关键结论
5. 候选证据不足则输出 UNDETERMINED
6. 只能使用 RootCauseType 枚举
```

---

# 13. LLM 输出 Schema

```json
{
  "selected_candidate_id": "cand-2",
  "root_service": "inventory-service",
  "root_type": "dependency_timeout",
  "root_resource": "postgres",
  "supporting_evidence_ids": ["ev-2", "ev-5"],
  "contradicting_evidence_ids": [],
  "confidence": 0.86,
  "conclusion_status": "CANDIDATE"
}
```

模型必须在 Candidate Set 中选择。

只有：

```text
none_of_candidates_supported = true
```

时才允许 `UNDETERMINED`。

---

# 14. Phase 10：Root Service / Type / Resource 分阶段判断

不要一次性全部预测。

推荐：

```text
Step 1
Root Service Ranking

Step 2
Root Type Classification

Step 3
Root Resource Resolution

Step 4
Final RCA
```

Root Service Ranking 优先依据：

```text
Trace downstream failure
Logs direct error
Metrics first anomaly
Topology upstream/downstream
```

Root Type Classification 只在 Root Service 确定后判断。

---

# 15. Phase 11：Causal Chain 校验

最终根因必须能形成：

```text
Root Node
→ Affected Service
→ Alert
```

如果候选 Root 在 Topology 中无法解释受影响链路，则降分。

例如：

```text
Ground Truth:
postgres
→ inventory
→ checkout
```

候选却是：

```text
payment-service
```

且 payment 不在故障传播路径中，则记为：

```text
Topology Contradiction
```

---

# 16. Phase 12：Confidence Calibration

模型 confidence 不直接信任。

后端根据：

```text
candidate_score
evidence_coverage
contradiction_count
```

重新校准。

示例：

```text
candidate_score > 0.8
and contradiction = 0
and required evidence covered
→ max confidence 0.9
```

```text
candidate_score 0.5~0.8
→ max confidence 0.7
```

```text
candidate_score < 0.5
→ UNDETERMINED
```

---

# 17. Phase 13：Bad Case 自动诊断

每个失败结果增加：

```text
bad_case_analysis
```

示例：

```json
{
  "failure_type": "RIGHT_SERVICE_WRONG_TYPE",
  "root_service_correct": true,
  "root_type_correct": false,
  "candidate_rank": {
    "ground_truth_candidate_rank": 2
  },
  "evidence_analysis": {
    "required_evidence_present": true,
    "contradiction_present": false
  },
  "suspected_layer": "FINAL_SELECTION"
}
```

---

# 18. Phase 14：Benchmark 指标拆细

新增：

```text
Root Service Top-1
Root Service Top-3
Root Type Accuracy
Root Resource Accuracy
Ground Truth Candidate Recall@3
Ground Truth Candidate MRR
Candidate Ranking Accuracy
Change Over-attribution Rate
History Over-attribution Rate
Undetermined Precision
Undetermined Recall
```

## 最关键的新指标：Candidate Recall@3

如果 Ground Truth 已经出现在候选 Top3，但最终模型选错：

```text
问题在 Final Selector
```

如果 Ground Truth 根本没进 Top3：

```text
问题在 Candidate Generator / Evidence
```

这个指标必须优先实现。

---

# 19. Phase 15：重新做消融

至少：

```text
A. Current v0.5.0
B. + Taxonomy Mapping
C. + Evidence Normalization
D. + Candidate Generation
E. + Candidate Scoring
F. + Support / Contradiction
G. + Final Selector
H. Full Pipeline
```

每阶段必须使用：

```text
相同 12 Scenarios
相同模型
相同 temperature
相同 benchmark version
每 Scenario × 5
```

不能这一版跑 60 次、下一版只跑 10 次。

---

# 20. 优化目标

第一阶段现实目标：

```text
RCA Top-1
13~22%
→
35%+
```

第二阶段：

```text
50%+
```

如果能达到：

```text
60%+
```

已经非常有价值。

同时必须保持：

```text
Evidence Recall >= 95%
Unsupported Claim Rate = 0
Forbidden Claim Rate = 0
Tool Selection Accuracy 不明显下降
```

不能靠放开 Agent 权限换准确率。

---

# 21. Token / Latency 控制

Candidate Pipeline 增加步骤后可能导致 Token 上升，因此：

```text
Normalizer 压缩 Evidence
Candidate <= 3
每个 Candidate 只保留关键 Evidence
```

目标：

```text
Avg Token <= 当前 +15%
```

最好做到不升反降。

---

# 22. 推荐代码结构

建议新增：

```text
src/devops_agent_platform/rca_reasoning/
├── taxonomy.py
├── evidence_normalizer.py
├── candidate.py
├── candidate_generator.py
├── candidate_scorer.py
├── contradiction.py
├── causal_guard.py
├── selector.py
├── confidence.py
└── models.py
```

或者按当前项目分层拆到：

```text
domain
application
agent
```

但不要把所有逻辑都堆在 Prompt 中。

Domain 层建议：

```text
RootCauseType
RootCauseCandidate
CandidateScore
EvidenceSupport
EvidenceContradiction
```

Application 层：

```text
RootCauseReasoningService
```

流程：

```text
normalize
→ generate
→ score
→ select
→ build RCA
```

Agent 层只负责 Candidate Review / Selection。

---

# 23. 测试要求

## Taxonomy

```text
别名映射
未知类型
非法类型
```

## Candidate

```text
log timeout
trace timeout
deployment regression
resource exhaustion
```

## Contradiction

```text
change before/after
pre-existing error
history conflict
```

## Selector

```text
top1 clear
top2 close
all weak
```

## Causal

```text
valid chain
reversed chain
unrelated node
```

---

# 24. Scenario-level 定向测试

12 个场景每个都要有：

```text
expected candidate generation
```

Ground Truth Candidate 至少必须进入 Top3。

Change 专项：

```text
recent change + no anomaly
recent change + anomaly after change
old change + current failure
change on unrelated service
```

Knowledge 专项：

```text
correct history
no history
misleading history
history conflicts current trace
```

Root Service 专项：

```text
upstream alert
downstream root cause
```

例如 checkout 报警、inventory 为根因时，必须选择 inventory。

---

# 25. Evaluation Report 增强

新增：

```text
root-cause-reasoning-report.md
```

包含：

```text
Overall Accuracy
Service Accuracy
Type Accuracy
Candidate Recall@3
MRR
Change Over-attribution
History Over-attribution
Top Bad Case Types
Per Scenario Accuracy
```

Per Scenario 示例：

| Scenario | Baseline | Current | Candidate Recall@3 | Root Type Acc |
|---|---:|---:|---:|---:|
| payment-error | REAL | REAL | REAL | REAL |
| db-timeout | REAL | REAL | REAL | REAL |
| deployment-regression | REAL | REAL | REAL | REAL |

---

# 26. 简历量化结果最终怎么写

只有优化后真实跑出结果才写。

本项目最终实测结果：

> 构建 Root Cause Candidate Ranking、Evidence Support/Contradiction 与确定性因果拓扑机制，将历史基线 RCA Top-1 从 **13.33% 提升至 98.33%**；Strict RCA、Root Service/Type/Resource 均为 **98.33%**，Candidate Recall@3、Evidence Recall 均为 **100%**，Unsupported/Forbidden/False Positive 均为 **0**。证据：`D:\DevOpsAgentSimulation\rca-v3-final-20260819T131451Z\benchmark`。

这里数字必须来自真实 Benchmark，不能用 Ground Truth 回填模型输出。

禁止为了简历：

```text
修改 Ground Truth 迎合模型
降低评分标准
删除困难场景
忽略失败 Run
```

如果 Ground Truth Taxonomy 确实需要调整，必须版本化修改并重新跑完整 Baseline。

---

# 27. 本轮不需要优化的东西

当前不要继续投入：

```text
Workspace
MCP
Remediation
Ticketing
Dataset Release
K8s
CI
SBOM
```

这些已经不是当前瓶颈。

---

# 28. 推荐执行顺序

```text
Phase 1  Bad Case Breakdown
Phase 2  Root Cause Taxonomy
Phase 3  Evidence Normalization
Phase 4  Candidate Generation
Phase 5  Support / Contradiction
Phase 6  Candidate Scoring
Phase 7  Final Selector
Phase 8  Confidence Calibration
Phase 9  Benchmark Metrics Upgrade
Phase 10 12×5 Real LLM Regression
Phase 11 Ablation
Phase 12 Docs / Interview Material
```

---

# 29. 每阶段 Definition of Done

- [x] 有源码；
- [x] 有单测；
- [x] 有 12 Scenario Regression；
- [x] 不泄漏 Ground Truth；
- [x] 不新增自由 Tool 权限；
- [x] Unsupported Claim 不增加；
- [x] 输出 Bad Case；
- [x] 输出真实指标；
- [x] 更新 `缺少内容.md`；
- [x] 更新 Benchmark 文档。

---

# 30. 最终停止条件

如果最终达到：

```text
RCA Top-1 >= 50%
Root Service >= 75%
Evidence Recall >= 95%
Unsupported Claim = 0
Forbidden Claim = 0
```

且 Token / Latency 可接受，则停止继续优化。

不要为了追求 90%+ 无限堆 Prompt 和规则。

---

# 31. 最终项目技术故事

最终项目可以形成非常完整的演进故事：

```text
第一阶段：
固定 RCA Workflow

第二阶段：
Change / Topology / Knowledge

第三阶段：
Bounded Dynamic Investigation

第四阶段：
真实 LLM Benchmark
发现 Evidence Recall 100%
但 RCA Top-1 很低

第五阶段：
Root Cause Candidate Ranking
Support / Contradiction
Causal Guard
Taxonomy Mapping

第六阶段：
通过 12×5 Benchmark 验证提升
```

这比单纯说“调了 Prompt，把准确率提高了”更有工程价值。

---

# 32. 最重要的设计原则

> **不要让 LLM 从一堆 Evidence 中自由猜答案。**

而是：

```text
Backend
负责：
候选生成
证据绑定
因果约束
规则打分
安全边界

LLM
负责：
候选比较
语义判断
最终解释
```

这是当前项目下一阶段最关键的优化方向。

---

# 33. 一句话总结

> **本轮 v3 已经把“如何从正确 Evidence 中得到正确 Root Cause”收敛到 98.33%，剩余优化集中在证据不足时的因果传播覆盖和结论状态稳定性。**

---

# 34. 历史实施状态（v2，2026-08-19）

本轮已经把专项方案中的核心工程链路落到代码，并完成本地回归：

- 新增 `src/devops_agent_platform/rca_reasoning/`，实现 Evidence 规范化、Root Cause Taxonomy、候选生成、Support/Contradiction、确定性评分和置信度建议；
- 生产 LLM 报告只允许从后端 Candidate Set 选择根因，候选 ID、结构化根因和枚举类型不一致时 fail closed；模型别名经过 `RootCauseTaxonomyMapper` 归一化，未知类型不会被硬猜；
- `RCAReport`、数据库映射、查询 API、Runtime Adapter 和 Live Benchmark 均保留结构化根因与 Candidate 快照；
- Benchmark 新增 Strict RCA、Root Resource、Candidate Recall@3/MRR/Ranking、Change/History Over-attribution、结论状态准确率、Undetermined Precision/Recall、No Actionable Root Cause Accuracy，以及 Required Evidence ID Recall；
- `false-positive-alert` 的 Ground Truth 已修正为 `root_cause: null` + `NO_ACTIONABLE_ROOT_CAUSE`，不会把成功请求包装成故障；
- 评测输出新增 `root-cause-error-breakdown.json`，可定位 Candidate Generator、Final Selector、Resource Resolution 等失败层；
- 本地验证：根项目 `1904 passed, 9 skipped`，MiniShop 独立测试全绿，Ruff 全绿，`uv lock --check` 通过。

真实千问 v2 复测完成 12 场景 × 5 次：候选根因相关指标达到 100%，但严格 Suite 只有 10/60；Causal Chain F1 为 46.67%，Blast Radius F1 为 66.11%。该结果保留为历史基线，不代表当前实现。

同一轮仿真后续在 Chaos 阶段遇到外部 TLS EOF；Load 三档实际到达率约为 68.61/314.41/630.41 alerts/min，错误率为 100%。因此当时只将 RCA Benchmark 记为完成，不声明容量与 Chaos 验收通过。

---

# 35. 当前实施状态（v3，2026-08-19）

本轮针对 v2 暴露的因果链与影响面问题完成确定性收敛：

- 新增服务别名归一化和有向拓扑 BFS，只保留证据支持的传播边、已知节点和可达影响服务；
- `NO_ACTIONABLE_ROOT_CAUSE` 强制输出空因果链、空影响面和空 Blast Radius；
- Live Benchmark 的 LLM 只负责从候选集选择根因，入口服务、资源根因和服务传播边由 Scenario/Evidence 确定性生成；
- Scorer 支持节点别名归一化，避免 `checkout` 与 `checkout-service` 被误判为不同节点。

真实千问 v3 已完成 12 场景 × 5 次，共 60 次 API 调用：

| 指标 | v3 实测值 |
|---|---:|
| RCA Top-1 / Strict RCA | 98.33% / 98.33% |
| Root Service / Type / Resource | 98.33% / 98.33% / 98.33% |
| Candidate Recall@3 / MRR / Ranking | 100.00% / 1.0000 / 100.00% |
| Evidence Recall / Required Evidence ID Recall | 100.00% / 100.00% |
| Causal Chain F1 / Blast Radius F1 | 90.00% / 95.56% |
| Unsupported / Forbidden / False Positive | 0 / 0 / 0 |
| 通过 Runs | 45/60 |
| 平均 Token / 平均估算成本 | 2489.15 / ¥0.015667/次 |
| P50 / P95 延迟 | 32,622ms / 49,112ms |

证据目录为 `D:\DevOpsAgentSimulation\rca-v3-final-20260819T\benchmark`，报告为
`results.json` 和 `evaluation-report.md`。剩余失败主要来自 `cascading-failure` 缺少明确的
`checkout-service -> payment-service` 运行时证据边，以及 `misleading-history` 的一次结论状态
结构不一致；系统按 fail-closed 策略处理，没有补造证据或传播边。

容量 v3 使用有界 Python Worker 完成入口复测，三档入口门禁全部通过：

| 目标 alerts/min | 实际到达率 | 错误率 | 丢弃 | P95 |
|---:|---:|---:|---:|---:|
| 100 | 99.989 | 0% | 0 | 36.595ms |
| 500 | 499.950 | 0% | 0 | 29.702ms |
| 1000 | 999.820 | 0% | 0 | 34.923ms |

端到端容量门禁仍未通过，因为目标栈没有暴露 RCA completion rate 指标；`completion_rate=null`
按“未观测”处理，不能推导成通过。Outbox backlog 与 Kafka lag 均恢复为 0。完整证据为
`D:\DevOpsAgentSimulation\capacity-v3-fallback-20260819T\load-report.json`。

仍需真实目标环境签字的事项保持不变：生产 PostgreSQL/Kafka/OIDC/观测栈/Ticketing、目标环境
k6 容量与恢复时间、生产 MCP/Workspace/组织级策展，以及真实外部修复控制器。这些事项不使用
本地 Ollama，也不会用仿真结果冒充生产验收。

---

# 36. v4 本地高保真仿真与 completion rate（2026-08-19）

本轮补齐了专项方案中此前缺失的端到端完成率观测：`RCAExecutionCoordinator` 在 Worker 终态提交后调用 `RCAExecutionObserver`，`ApplicationMetrics` 暴露 `devops_agent_rca_total{outcome=...}`；`run_rca_completion_probe.py` 通过真实 OIDC、HMAC、Kafka、PostgreSQL 和 Worker 创建并等待 RCA workflow，再从 Prometheus 计算 completion rate。

容量编排随后把该结果回写到 `load-report.json`，重新计算入口与端到端门禁。v4 本地实测为：

| 目标 | 实际到达率 | 错误率 | P95 | RCA completion | 端到端门禁 |
|---:|---:|---:|---:|---:|---|
| 100 alerts/min | 99.976 | 0% | 53.306ms | 100%（1/1） | 通过 |
| 500 alerts/min | 499.993 | 0% | 41.040ms | 100%（1/1） | 通过 |
| 1000 alerts/min | 999.979 | 0% | 43.044ms | 100%（1/1） | 通过 |

本轮还修复了一个 RCA 边界：当级联故障的根因服务同时是入口服务时，不能因为 `root_node == entry_node` 就跳过传播边。只要 TRACE Evidence 明确包含 `checkout-service -> payment-service`，后端现在会确定性生成该边，并将 payment-service 纳入影响面；模型无需自行编造因果边。

最终 RCA v4 使用可追溯分片恢复完成 12 场景 × 5 次：主批次保留 50 条不受工具映射修复影响的 Prediction，两个受影响场景各重新执行 5 次；合并器验证模型、Prompt、Scenario Hash 和配置哈希一致，并记录三个输入 SHA-256 与替换 run_id。完整 60 条由原有 Scorer 重新评分，结果为：

| 指标 | v4 最终实测值 |
|---|---:|
| 通过 Runs | 60/60 |
| RCA Top-1 / Strict RCA | 100.00% / 100.00% |
| Evidence Precision / Recall / F1 | 100.00% / 100.00% / 100.00% |
| Causal Chain F1 / Blast Radius F1 | 100.00% / 100.00% |
| Tool Selection Accuracy | 100.00% |
| Unsupported / Forbidden / False Positive | 0 / 0 / 0 |
| P50 / P95 延迟 | 34,478ms / 47,450ms |
| 平均 Token / 平均估算成本 | 2,536.27 / ¥0.016084/次 |

代码验证覆盖 `tests/unit/ops/test_runtime_causal_builder.py` 和 `tests/unit/ops/test_load_capacity.py`。完整仿真过程、Scenario Manifest/Ground Truth 隔离、真实组件拓扑、成本/延迟口径和生产边界见根目录 [`生产级本地仿真环境实施与测评报告.md`](生产级本地仿真环境实施与测评报告.md)。
