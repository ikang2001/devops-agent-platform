# DevOps Agent 平台最终完善方案
## 面向云上智能运维 / AIOps 故障诊断平台

> 目标仓库：`https://github.com/ikang2001/devops-agent-platform`
>
> 当前版本基线：`v0.4.0`
>
> 文档用途：直接交给 Codex / Claude Code / Cursor / 其他编程 Agent，作为当前项目的**最终完善任务书**。
>
> 当前项目已经具备较完整的 Incident / RCA / Evidence / Outbox / Kafka / Change Event / Topology / Historical Knowledge / Evaluation / Load / Chaos / Workspace / MCP 能力。
>
> **本轮目标不是继续增加模块，而是把“已经实现但没有真正进入主业务链”的能力接实，并用真实 LLM Benchmark 证明效果。**

---

# 0. 最终项目定位

项目最终应定位为：

> **面向云上微服务故障诊断的生产导向 AIOps 平台：统一接入告警、指标、日志、链路、变更事件、服务拓扑与历史事故知识，通过受控的有界调查机制自动收集 Evidence、分析根因、因果链与影响范围，并通过 Transactional Outbox、Kafka、租约、Owner/Attempt Fence、权限、审计和人工审批保证 Agent 在生产系统中的可靠执行。**

目标业务链路：

```text
Alertmanager / Cloud Monitor
            ↓
         Alerts
            ↓
Topology-aware Alert Correlation
            ↓
         Incident
            ↓
Transactional Outbox
            ↓
      Kafka / Redpanda
            ↓
       RCA Consumer
            ↓
Bounded Dynamic Investigation
            ↓
    ┌────────┼────────┬─────────┬─────────┬─────────┐
    ↓        ↓        ↓         ↓         ↓         ↓
 Metrics   Change    Logs     Traces   Topology   Knowledge
                                               ↓
                                           Runbooks
                                               ↓
                                            Evidence
                                               ↓
                           Root Cause / Causal Chain / Blast Radius
                                               ↓
                                         RCA Report
                                               ↓
                          Human Review / Ticket / Remediation
```

---

# 1. 当前项目已经具备的能力

在执行本任务书前，AI 必须先确认以下能力已经存在，**禁止重复造轮子**：

```text
FastAPI
PostgreSQL
SQLAlchemy Async
Alembic

Alert
Incident
WorkflowRun
Evidence
ToolInvocation

Transactional Outbox
Kafka / Redpanda

Claim
Lease
Heartbeat
Owner / Attempt Fence
Idempotency

ToolRegistry
Tool Permission
Risk Level
HMAC
OIDC / JWT / JWKS
Redaction
Audit

Metrics
Change
Logs
Traces
Runbooks

Topology
Blast Radius
Historical Knowledge Retrieval

Workspace
HTTP MCP
Feedback
Ticket
Remediation

MiniShop
Prometheus
Loki
Tempo
Alertmanager

12 Scenario Benchmark
Structured Prediction
Scorer
Bad Cases
Ablation
Load Harness
Chaos Harness
Reference Staging
```

---

# 2. 当前真正存在的 4 个关键缺口

当前项目不缺“功能数量”。

真正需要补的是下面 4 个问题。

---

## Gap 1：Bounded Dynamic Investigation 已实现，但主 RCA Runtime 未真正接入

当前已有：

```text
src/devops_agent_platform/agent/bounded_dynamic.py
```

其中已经具备：

```text
InvestigationState
StepDecision
IntentPlannerPort
InvestigationPolicyValidator
BoundedDynamicInvestigator
Budget
Tool Allowlist
Read-only Gate
Checkpoint
Resume
```

但当前 `rca_runtime.py` 仍然统一通过：

```text
build_plan_for_policy()
→ ControlledAgentWorkflow
```

执行 RCA。

`bounded_dynamic_v1` 目前只是：

```text
策略键存在
+
返回 default fixed plan 作为安全基线
```

并没有真正进入：

```text
BoundedDynamicInvestigator.run()
```

因此当前还不能对外宣称：

> “RCA 已经是运行时动态多步调查。”

---

## Gap 2：Topology / Knowledge 已注册，但默认 RCA 不会真正调用

当前 Runtime 已经存在：

```text
topology.query@v1
knowledge.search@v1
```

并且有：

```text
TopologyService
KnowledgeService
TopologyQueryHandler
KnowledgeSearchHandler
```

但默认 RCA Plan 仍然是：

```text
Metrics
→ Change
→ Logs
→ Traces
→ Runbooks
```

也就是说：

```text
Tool 存在
Runtime 注册
但是默认流程没有使用
```

Topology / Knowledge 目前还没有真正参与核心 RCA 决策。

---

## Gap 3：AlertCorrelationService 已存在，但 Alert → Incident 主链仍主要按“同服务 + 时间窗口”聚合

当前已经有：

```text
AlertCorrelationService
```

能够综合：

```text
tenant
environment
time window
service
topology dependency
severity
alert type
```

进行关联评分。

但当前：

```text
AlertApplicationService
```

依赖的主策略仍然主要是：

```text
IncidentCreationPolicy
```

并要求：

```text
incident.service_name == alert.service_name
```

因此跨服务故障传播：

```text
postgres
→ inventory
→ checkout
```

仍有可能：

```text
inventory Alert → Incident A
checkout Alert  → Incident B
```

不能真正体现 AIOps 的：

> Alert Storm Correlation。

---

## Gap 4：Benchmark 基础设施已经完成，但还没有真实模型效果数据

当前已经具备：

```text
12 Scenario Manifest
Structured Prediction
Ground Truth
Scorer
results.json
evaluation-report.md
ablation-report.md
bad_cases.jsonl
Real-LLM-compatible Runner
Token / Cost / Latency
```

但是现有 Reference：

```text
12 × 5 = 60 runs
```

使用的是：

```text
synthetic reference provider
```

用于验证：

```text
协议
Runner
成本统计
Bad Case
```

不能代表真实 LLM 能力。

因此现在还缺：

```text
真实 Provider
+
真实 60+ RCA Runs
+
真实 Accuracy / Evidence / Token / Latency 数据
```

---

# 3. 本轮总目标

本轮只完成以下 4 件事：

```text
A. Bounded Dynamic Runtime 真正接线
B. Topology / Knowledge 真正进入动态调查
C. Alert Correlation 真正接入 Alert → Incident
D. 真实 LLM Benchmark + Ablation
```

完成后项目停止继续扩 scope。

---

# 4. Phase A：Bounded Dynamic Investigation Runtime 接线

## 4.1 目标

当：

```text
rca_investigation_policy=bounded_dynamic_v1
```

时，RCA Consumer 不再只是：

```text
ControlledAgentWorkflow(fixed plan)
```

而是真正走：

```text
BoundedDynamicInvestigator
```

## 4.2 保留兼容性

以下策略行为绝对不能改变：

```text
fixed_default
fixed_no_traces
fixed_metrics_logs_runbooks
```

必须保持：

```text
原来的 ControlledAgentWorkflow
```

只有：

```text
bounded_dynamic_v1
```

进入新 Runtime。

## 4.3 推荐 Runtime 结构

建议改造：

```text
bootstrap/rca_runtime.py
```

逻辑：

```python
if policy == BOUNDED_DYNAMIC_V1:
    workflow = build_bounded_dynamic_workflow(...)
else:
    workflow = build_fixed_workflow(...)
```

不要在一个函数中塞全部逻辑。

建议拆：

```text
build_fixed_rca_workflow()
build_dynamic_rca_workflow()
```

## 4.4 Dynamic Workflow Adapter

为了兼容现有：

```text
RCAExecutionCoordinator
```

建议新增：

```python
DynamicRCAWorkflowAdapter
```

使其实现和当前 WorkflowExecutor 相同或等价的协议。

目标：

```text
RCAExecutionCoordinator
不需要知道
内部是 Fixed 还是 Dynamic
```

结构：

```text
RCAExecutionCoordinator
        ↓
RCAWorkflowExecutorPort
        ↓
 ┌───────────────┬──────────────────┐
 ↓               ↓
Controlled     Dynamic
Workflow       WorkflowAdapter
```

---

# 5. Dynamic Investigation 状态设计

继续使用现有：

```text
InvestigationState
```

但必须确认以下字段完整：

```text
incident_id
tenant_id

completed_steps
failed_steps

evidence_ids
observed_signals
candidate_root_services

visited_tools
tool_call_counts

remaining_budget
llm_calls

started_at
checkpoint_version
stop_reason
```

如缺少：

```text
current_round
last_decision
last_evidence_summary
```

可以补充，但不要过度设计。

---

# 6. Planner 设计

## 6.1 Planner 的职责

Planner 只输出：

```text
下一步想调查什么
```

而不是：

```text
真实 Tool Payload
```

示例：

```json
{
  "next_tool": "changes.query@v1",
  "target_service": "payment-service",
  "reason_code": "RECENT_CHANGE_SUSPECTED",
  "required_evidence_types": ["CHANGE"],
  "stop": false
}
```

## 6.2 Planner 不允许输出

禁止：

```json
{"query":"SELECT * FROM ..."}
```

禁止：

```json
{"promql":"..."}
```

禁止：

```json
{"shell":"kubectl delete ..."}
```

所有真实参数仍由：

```text
InvestigationPolicyValidator
```

从后端可信状态构造。

---

# 7. Planner 第一版实现策略

不要一开始就纯 LLM。

推荐：

```text
Rule-first
+
LLM fallback
```

例如：

### Rule 1

```text
尚未查询 Metrics
→ metrics.query
```

### Rule 2

```text
Metrics 显示 error_rate / latency 异常
且 Logs 未查询
→ logs.query
```

### Rule 3

```text
Incident 前 30min 有 change
或日志出现 version/deployment/config
→ changes.query
```

### Rule 4

```text
Logs / Traces 出现 downstream / timeout / dependency
→ topology.query
```

### Rule 5

```text
出现稳定 error fingerprint
→ knowledge.search
```

### Rule 6

```text
服务依赖复杂
或 root service 不明确
→ traces.query
```

### Rule 7

```text
已有现场 Evidence
但缺少处置建议
→ runbooks.retrieve
```

### Rule 8

```text
required evidence coverage 达阈值
或 budget 即将耗尽
→ stop
```

---

# 8. Dynamic Investigation Budget

必须配置：

```text
max_steps
max_total_duration_ms
max_tool_calls_per_type
max_evidence_count
max_llm_calls
```

推荐默认：

```text
max_steps = 7
max_tool_calls_per_type = 1~2
max_llm_calls = 3
```

不要无限循环。

---

# 9. Tool Policy

动态调查仍只能使用：

```text
metrics.query@v1
changes.query@v1
logs.query@v1
traces.query@v1
topology.query@v1
knowledge.search@v1
runbooks.retrieve@v1
```

禁止：

```text
Shell
Remediation
Ticket write
MCP write tool
DB write tool
```

---

# 10. Checkpoint / Resume

继续复用现有：

```text
WorkflowRun
Lease
Heartbeat
Owner / Attempt Fence
```

Dynamic State 每一步成功后：

```text
save checkpoint
```

Worker crash：

```text
Worker A
 ↓ crash
Lease expire
 ↓
Worker B claim
 ↓
load InvestigationState
 ↓
继续未完成调查
```

不能从头随便重跑全部 Tool。

---

# 11. Dynamic Runtime 验收

必须测试：

```text
fixed_default 不受影响
fixed_no_traces 不受影响
fixed_metrics_logs_runbooks 不受影响
```

Dynamic：

```text
bounded_dynamic_v1
→ 真的调用 BoundedDynamicInvestigator
```

至少测试：

- 最大步数；
- 重复 Tool；
- Tool 不在 allowlist；
- Planner 提出 write tool；
- Planner 提出未知 service；
- Tool timeout；
- 0 Evidence；
- partial Evidence；
- checkpoint；
- resume；
- Worker crash；
- Fence；
- policy block。

---

# 12. Phase B：Topology 真正进入 RCA

## 12.1 目标

当前：

```text
topology.query
```

只是注册工具。

改造后：

```text
Dynamic Investigation
```

要根据现场信号真正决定是否调用。

## 12.2 典型场景

例如：

```text
checkout error_rate ↑
Logs:
"inventory request timeout"
```

Planner：

```text
target_service=checkout
next_tool=topology.query
```

Topology：

```text
checkout
 ├─ inventory
 │   └─ postgres
 └─ payment
```

下一步：

```text
traces.query target=inventory
```

最终：

```text
postgres
→ inventory
→ checkout
```

---

# 13. Blast Radius 主链接入

当前 Blast Radius 已实现。

需要确保最终 RCA Report 真正包含：

```text
suspected_root_node
causal_chain
affected_services
blast_radius
```

不能只存在一个独立 Service API。

---

# 14. Topology Evidence

Topology 查询结果必须保存为：

```text
Evidence
```

或者建立：

```text
Topology Evidence Reference
```

任何：

```text
causal_chain
blast_radius
```

必须能追踪：

```text
Evidence ID
```

---

# 15. Topology 验收场景

至少：

```text
redis-latency
third-party-api-timeout
cascading-failure
```

必须验证：

```text
root cause service
≠
affected service
```

例如：

```text
inventory DB fault

root:
inventory / postgres

affected:
checkout
```

不能把 checkout 误认为根因。

---

# 16. Phase C：Historical Knowledge 真正进入 RCA

## 16.1 当前边界

当前已有：

```text
lexical
service
fingerprint
```

Historical Retrieval。

暂时不要求：

```text
Vector DB
Embedding
```

不要为了简历强行加 pgvector。

---

# 17. Knowledge 调用时机

只在以下条件之一满足时调用：

```text
稳定 error fingerprint
重复告警 pattern
历史 Incident signature
Logs 中出现 known error code
```

不要每次 RCA 都强制调用。

---

# 18. Knowledge Evidence 权重

历史事故只能作为：

```text
REFERENCE
```

不能作为：

```text
CURRENT FACT
```

报告语言必须区分：

```text
现场 Evidence：
本次 Trace 显示 payment timeout

历史参考：
过去 incident-123 曾出现类似 timeout
```

---

# 19. 防止 RAG 带偏

必须保留：

```text
misleading-history
```

场景。

例如：

```text
历史：
payment 500
root = Redis

当前：
payment 500
root = deployment regression
```

最终模型必须优先：

```text
Change
Metrics
Logs
Traces
```

而不是历史案例。

---

# 20. Knowledge 验收

至少：

```text
known-error-repeat
misleading-history
```

分别测试：

```text
No Knowledge
Correct History
Misleading History
```

---

# 21. Phase D：Alert Correlation 真正进入主链

## 21.1 当前问题

当前主线：

```text
AlertApplicationService
→ IncidentCreationPolicy
```

主要依据：

```text
tenant
same service
time window
```

这不足以表达跨服务故障传播。

---

# 22. 改造原则

不要直接删除：

```text
IncidentCreationPolicy
```

推荐把它降级为：

```text
基础候选过滤
```

再使用：

```text
AlertCorrelationService
```

进行评分。

---

# 23. 推荐流程

```text
Alert
 ↓
severity threshold
 ↓
find candidate incidents
 ↓
load topology
 ↓
AlertCorrelationService.correlate()
 ↓
score
 ↓
if score >= threshold:
    ATTACH
else:
    CREATE
```

---

# 24. Correlation Candidate 查询

不能只查询：

```text
same service
```

需要允许：

```text
tenant
environment
active status
time window
```

找候选。

然后服务相关性由：

```text
AlertCorrelationService
```

自己判断。

---

# 25. Correlation Score

推荐继续使用已有评分框架：

```text
Time Window
+
Same Service
或
Topology Related
+
Alert Type
+
Severity
```

可以配置：

```text
correlation_threshold
```

例如：

```text
0.6
```

具体阈值用测试确定。

---

# 26. Primary Alert

Incident 建议真正持久化：

```text
primary_alert_id
correlated_alert_count
correlation_reason
correlation_score
```

如已有字段，直接复用。

Primary Alert 评分：

```text
severity
+
root service proximity
+
earliest timestamp
```

---

# 27. 告警风暴 E2E

新增：

```text
alert-storm-cascading-failure
```

模拟：

```text
postgres timeout
↓
inventory timeout alert
↓
checkout latency alert
↓
checkout error_rate alert
↓
order-api alert
```

例如产生：

```text
20 Alerts
```

期望：

```text
1 Incident
```

并正确：

```text
primary alert
root service
affected services
```

---

# 28. Alert Correlation 安全边界

必须防止：

```text
不同 tenant
不同 environment
无 topology 关系
时间跨度很远
```

的 Alert 被错误聚合。

---

# 29. Phase E：真实 LLM Benchmark

这是本项目当前**最重要的量化收口步骤**。

---

# 30. Benchmark Provider

至少选择一个真实 Provider。

建议：

```text
Qwen / DashScope
```

或者：

```text
OpenAI-compatible Provider
```

理由：

```text
已有客户端支持
成本相对可控
容易重复跑
```

---

# 31. Benchmark 规模

至少：

```text
12 scenarios
×
5 runs
=
60 RCA runs
```

如预算允许：

```text
10 runs/scenario
```

---

# 32. Benchmark 必须固定

保存：

```text
git_commit
model_provider
model_name
temperature
prompt_version
policy
scenario_version
benchmark_version
timestamp
```

---

# 33. Baseline / Ablation

至少对比：

```text
V1 Fixed Baseline
Metrics + Logs + Traces + Runbooks

V2 + Change

V3 + Topology

V4 + Knowledge

V5 Bounded Dynamic
```

注意：

如果当前 default fixed 已包含 Change，

需要单独保留：

```text
fixed_metrics_logs_runbooks
```

作为无 Change Baseline。

---

# 34. 核心指标

必须输出：

```text
Root Cause Service Accuracy
Root Cause Type Accuracy
RCA Top-1 Accuracy

Evidence Precision
Evidence Recall
Evidence F1

Unsupported Claim Rate
Forbidden Claim Rate

Causal Chain F1
Blast Radius F1

Tool Selection Accuracy
Redundant Tool Call Rate

Average Investigation Steps
Average Tool Calls
LLM Calls / RCA

P50 RCA Latency
P95 RCA Latency

Average Tokens
P95 Tokens
Average Cost
```

---

# 35. Dynamic 是否算成功

Dynamic 不是 Accuracy 越高就够。

成功条件应该是：

```text
Accuracy 不低于 Fixed Baseline
+
Evidence Recall 不下降
+
Unsupported Claim 不升高
+
Tool Calls 明显减少
或
Latency / Token 明显下降
```

---

# 36. Bad Case 分析

继续使用：

```text
bad_cases.jsonl
```

必须分类：

```text
TOOL_SELECTION_ERROR
TOOL_PARAMETER_ERROR
TOOL_TIMEOUT
RETRIEVAL_MISS
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

# 37. 不要看到错误就改 Prompt

每个 Bad Case 必须定位：

```text
Data
Tool
Retrieval
Planner
Policy
Prompt
LLM
Workflow
```

例如：

```text
根因没找到
```

不能直接：

```text
修改 Prompt
```

要先判断：

```text
是不是根本没召回 Change Evidence
```

---

# 38. Load / Chaos 不需要大改

当前已有：

```text
100
500
1000 alerts/min
```

Reference Load。

以及：

```text
Worker
Kafka
PostgreSQL
Loki
```

Chaos。

本轮只需：

```text
在 Dynamic Runtime 接线后重新回归
```

重点验证：

```text
Worker crash
→ Dynamic checkpoint resume

Kafka down
→ Outbox 不丢任务

Loki timeout
→ partial evidence

Topology / Knowledge timeout
→ Dynamic Policy 正确降级
```

---

# 39. 推荐新增 Chaos

动态 RCA 后补：

```text
Planner timeout
Planner invalid schema
Knowledge timeout
Topology unavailable
Checkpoint write failure
```

---

# 40. 测试矩阵

最终建议：

| 层 | 测什么 |
|---|---|
| Domain | State / Decision / Correlation |
| Unit | Planner / Validator / Scorer |
| Repository | Checkpoint / Topology / Knowledge |
| Integration | Dynamic Tool 调用 |
| E2E | MiniShop Incident → Dynamic RCA |
| Benchmark | 12 Scenario × Real LLM |
| Chaos | Worker / Kafka / Tool / Checkpoint |
| Load | Alert Intake / Outbox / Consumer |

---

# 41. AI 修改流程

每个 Phase 必须按：

```text
代码考古
↓
设计
↓
测试
↓
实现
↓
验证
↓
文档
```

禁止：

```text
先改 README 宣称完成
```

---

# 42. 每个 Phase 的 Definition of Done

必须同时满足：

- [ ] Runtime 真正接线；
- [ ] Feature / Policy 可配置；
- [ ] 默认行为兼容；
- [ ] 单测；
- [ ] Integration Test；
- [ ] E2E；
- [ ] 失败路径；
- [ ] 权限；
- [ ] Audit；
- [ ] Redaction；
- [ ] Metrics；
- [ ] 文档；
- [ ] `缺少内容.md` 更新；
- [ ] 面试可说 / 不可说更新。

---

# 43. 本轮推荐实现顺序

严格建议：

```text
Phase 1
Dynamic Runtime Adapter

Phase 2
Topology + Knowledge → Dynamic

Phase 3
AlertCorrelation → Alert Main Flow

Phase 4
MiniShop Alert Storm / Dynamic E2E

Phase 5
Real LLM Benchmark

Phase 6
Ablation

Phase 7
Dynamic Chaos Regression

Phase 8
Docs + Resume
```

---

# 44. 不需要继续做的事情

本轮明确禁止扩 scope：

```text
Multi-Agent
自由 ReAct
任意 Shell Agent
新的消息队列
新的数据库
新的 Agent Framework
向量数据库迁移
自动 Prompt 灰度
自动模型训练
完全自动 Remediation
```

这些都不是当前最缺的。

---

# 45. 项目最终架构目标

```text
                       ┌──────── Metrics
                       ├──────── Change
                       ├──────── Logs
                       ├──────── Traces
Alert                   ├──────── Topology
  ↓                     ├──────── Knowledge
Correlation             └──────── Runbook
  ↓                              ↑
Incident                         │
  ↓                              │
Outbox → Kafka → RCA Consumer    │
                  ↓              │
         Bounded Dynamic Planner
                  ↓
         Backend Policy Validator
                  ↓
            Tool Registry
                  ↓
              Evidence
                  ↓
        Root Cause / Causal Chain
        / Blast Radius
                  ↓
             RCA Report
                  ↓
         Human Review / Action
```

---

# 46. 最终项目故事

完成本轮后，项目可以这样讲：

## 业务背景

线上故障排查依赖：

```text
指标
日志
链路
变更
服务拓扑
历史事故
Runbook
```

这些信息分散在不同系统。

## 第一版

使用固定 RCA：

```text
Metrics
→ Logs
→ Traces
→ Runbook
```

问题：

```text
每个事故都查一样的 Tool
不能根据现场变化
工具调用冗余
无法利用服务拓扑和历史事故
```

## 第二版

增加：

```text
Change
Topology
Historical Knowledge
Alert Correlation
```

## 第三版

进一步实现：

```text
Bounded Dynamic Investigation
```

但不是自由 Agent。

---

# 47. 为什么不用自由 ReAct

因为真实运维 Agent 连接生产系统。

自由 Agent 容易：

```text
Tool 误选
参数幻觉
重复查询
无限循环
权限越界
成本失控
写操作重复
```

因此：

```text
LLM / Planner
只提交 Intent
↓
Backend Policy Validator
↓
安全构造 Payload
↓
ToolRegistry
↓
Evidence
```

---

# 48. 为什么这是 AIOps，而不是普通 Chatbot

因为核心不是聊天。

核心是：

```text
Alert
Incident
Correlation
Observability
Change
Topology
Evidence
RCA
Workflow Reliability
Evaluation
```

LLM 只是其中：

```text
Planner
+
Report Generator
```

而不是整个系统。

---

# 49. 最终简历可写版本

**只有真实完成后再使用下面内容。**

> **云上智能运维 / AIOps 故障诊断平台**
>
> 面向云上微服务异常诊断场景，设计并实现生产导向的 Incident/RCA 平台，统一接入 Metrics、Logs、Traces、Change Event、Service Topology 与历史事故知识，通过受控有界调查机制动态选择只读证据工具，生成可追溯 RCA、因果链和 Blast Radius。

亮点建议：

### 1. 动态 RCA

> 设计 Bounded Dynamic Investigation，由 Planner 输出调查意图，后端 Policy Validator 统一完成 Tool 白名单、权限、预算、拓扑范围和参数构造，实现动态调查同时避免自由 ReAct 的不可控工具调用。

### 2. 分布式可靠性

> 基于 PostgreSQL Transactional Outbox + Kafka 解耦 RCA 长任务，通过 Claim / Lease / Heartbeat / Owner-Attempt Fence 实现 Worker 崩溃恢复并阻止迟到 Worker 覆盖新任务结果。

### 3. AIOps 多源 Evidence

> 融合 Metrics / Logs / Traces / Change / Topology / Historical Knowledge，构建 Evidence 驱动的根因、因果链和故障影响面分析，所有关键结论绑定 Evidence ID。

### 4. 告警收敛

> 基于时间窗口、服务关系、Topology、Severity 和 Alert Type 对跨服务告警进行关联，将级联故障产生的 Alert Storm 收敛为统一 Incident。

### 5. Evaluation

> 构建 12 类微服务故障 Ground Truth Benchmark，自动评测 RCA Top-1、Evidence Recall、Unsupported Claim、Causal Chain、Blast Radius、Tool Call、Token、Latency 和 Cost，并通过消融实验验证 Change / Topology / Knowledge / Dynamic Investigation 的实际收益。

---

# 50. 简历量化结果必须来自真实 Benchmark

最终可以写：

```text
RCA Top-1：
A% → B%

Evidence Recall：
C% → D%

Average Tool Calls：
E → F

Token：
降低 G%

P95 RCA Latency：
H → I
```

其中：

```text
A~I
```

全部必须从：

```text
ops/evaluation/artifacts/.../results.json
```

自动读取。

禁止手写。

---

# 51. 当前阶段不能说的话

直到本轮全部完成，仍不能说：

```text
已经生产上线
已承载腾讯真实业务
腾讯 Cloud Mate 项目
已实现完全自适应多轮 Agent
已实现向量 RAG
已实现自动学习闭环
已实现自动 Prompt 灰度
已实现无人值守自动修复
支持任意 Shell / K8s 修复
生产准确率 XX%
```

---

# 52. 完成本轮后仍然不能说的话

即使本轮完成，也只能说：

```text
生产导向设计
本地 Reference Staging
真实 LLM Benchmark
可重复故障演练
受控动态调查
```

不能说：

```text
生产上线
真实客户流量
腾讯内部正式产品
```

除非真的有对应外部证据。

---

# 53. 最终收敛标准

完成以下 7 项后，停止继续加功能：

- [x] `bounded_dynamic_v1` 真正进入 RCA Runtime（本地高保真仿真已验证）；
- [x] Topology Tool 被动态 RCA 实际调用（topology/dynamic 变体各 60 次）；
- [x] Knowledge Tool 被动态 RCA 实际调用（knowledge/dynamic 变体各 60 次）；
- [x] AlertCorrelationService 真正进入 Alert → Incident 主链；
- [x] Alert Storm E2E 通过；
- [x] 本地仿真使用真实千问 API 完成 12×5 Benchmark；企业 Real Provider 门禁仍需独立 staging 凭据；
- [x] Fixed vs Dynamic Ablation 有真实结果，Dynamic 门禁 PASS（五变体共 300 次）。

做到这里，这个项目已经足够完整。

---

# 54. 最终判断标准

项目最终不应该追求：

> “功能最多”。

而应该达到：

```text
业务逻辑完整
+
主链路真实接线
+
失败路径可解释
+
后端可靠性有设计
+
Agent 安全边界明确
+
效果可以量化
+
代码与简历完全一致
```

最终目标是让面试官继续追问：

```text
为什么这么设计？
怎么失败？
怎么恢复？
怎么证明 Agent 有效？
为什么不用自由 Agent？
```

都能直接从当前源码、测试和 Benchmark 中找到答案。

这才是本项目最有竞争力的状态。
