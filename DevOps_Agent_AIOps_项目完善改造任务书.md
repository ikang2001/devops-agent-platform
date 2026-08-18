# DevOps Agent 平台完善方案：面向云智研发 / 云厂商 AIOps 场景

> 目标仓库：`https://github.com/ikang2001/devops-agent-platform`
>
> 文档用途：本文件直接交给 Codex / Claude Code / Cursor / 其他编程 Agent 作为项目改造任务书。
>
> 核心目标：在**不破坏现有生产导向可靠性设计**的前提下，把当前“受控 Incident / RCA 作业编排后端”进一步完善为更像云厂商内部真实业务的 **智能运维 / AIOps / 云上故障诊断平台**。
>
> **重要：不要把项目改造成自由式 ReAct Demo。** 本项目的竞争力不是“模型可以随便调工具”，而是“LLM 能参与判断，但流程、权限、状态、一致性和最终执行权仍由后端控制”。

---

# 0. AI 执行总指令

在修改任何代码前，必须先阅读并理解：

- `README.md`
- `缺少内容.md`
- `docs/remediation-roadmap-master-plan.md`
- `docs/minishop-e2e-rca-code-tour.md`
- `STEP4_ACCEPTANCE.md`
- `STEP5_PRODUCTION.md`
- `STEP6_PRODUCTIZATION.md`
- `项目面试文档.md`（若存在）
- 当前 `src/devops_agent_platform/` 代码分层
- 当前 Alembic migrations
- 当前 `tests/`
- 当前 `ops/minishop-e2e/`
- 当前 MiniShop 故障演练靶场

其中：

> **`缺少内容.md` 仍然是完成度与对外话术的单一真相源。**

任何新增功能只有在：

1. 运行时代码真实接线；
2. 单元测试通过；
3. E2E 或集成测试有对应验证；
4. 文档更新；
5. `缺少内容.md` 从“未实现”移动到“已实现”；

之后，才允许写入 README 和简历亮点。

禁止“先改文档包装，后补实现”。

---

# 1. 当前项目基线：不要重复造已有能力

当前项目已经具备的核心能力包括：

```text
Alertmanager / Webhook
        ↓
     Alert
        ↓
    Incident
        ↓
PostgreSQL Transactional Outbox
        ↓
Kafka / Redpanda
        ↓
RCA Consumer
        ↓
Claim / Lease / Heartbeat / Owner-Attempt Fence
        ↓
受控 RCA Workflow
        ↓
Metrics / Logs / Traces / Runbooks
        ↓
Evidence
        ↓
RCA Report
        ↓
人工反馈 / 工单 / 审批式 Remediation
```

已有工程能力包括但不限于：

- FastAPI 接入层；
- PostgreSQL + SQLAlchemy Async；
- Transactional Outbox；
- Kafka / Redpanda；
- RCA Claim / Lease / Heartbeat；
- Owner / Attempt Fence；
- 幂等；
- Tool Registry；
- Tool 权限与风险控制；
- HMAC Webhook；
- OIDC / JWT / JWKS；
- Evidence；
- ToolInvocation；
- 固定 RCA 调查计划；
- 部分只读工具失败降级；
- 置信度护栏；
- OpenAI / DashScope 等 LLM Provider；
- LLM 失败时确定性报告回退；
- 工单；
- 人工反馈；
- Remediation 审批与受控执行；
- MiniShop；
- Prometheus；
- Loki；
- Tempo；
- Alertmanager；
- E2E；
- Ground Truth；
- CI / Docker / K8s 骨架 / SBOM / 安全扫描。

## 1.1 绝对不能做的错误重构

不要：

- 删除 Transactional Outbox，改成 API 直接发 Kafka；
- 删除 Lease / Fence；
- 让 LLM 直接执行任意 Shell；
- 让 LLM 自由拼接任意 PromQL / LogQL / TraceQL；
- 将固定流程直接替换为无限 `while` Agent Loop；
- 允许模型直接标记根因为 `CONFIRMED`；
- 让 Remediation 自动无限重试外部写；
- 用本地 E2E 冒充真实生产环境上线；
- 为了“多 Agent”而拆成大量没有业务价值的 Agent。

---

# 2. 改造后的项目定位

最终项目应定位为：

> **面向云上微服务故障诊断的生产导向 AIOps 平台：统一接入告警、指标、日志、链路、变更事件、服务拓扑与历史故障知识，通过受控的有界调查策略自动收集 Evidence、分析根因与影响范围，并通过 Outbox、Kafka、租约、权限、审计和人工审批保证 Agent 在生产系统中的可靠执行。**

最终核心链路：

```text
                               ┌── Metrics
                               ├── Logs
Alert / Alert Storm            ├── Traces
        ↓                      ├── Change Events
Alert Correlation              ├── Service Topology / CMDB
        ↓                      ├── Runbooks
     Incident ──→ RCA Orchestrator
                               └── Historical Incidents / Knowledge
                                         ↓
                                  Evidence Set / Graph
                                         ↓
                             RCA + Causal Chain + Blast Radius
                                         ↓
                           Human Review / Ticket / Remediation
```

---

# 3. 改造优先级

必须严格按照下面的优先级推进。

## P0：最优先，直接决定项目是否像真实云厂商 AIOps

1. **Change Event / 变更事件**
2. **Service Topology / CMDB / Blast Radius**
3. **Bounded Dynamic Investigation / 有界动态调查**
4. **Historical Incident RAG / 历史事故知识检索**

## P1：把项目从“功能完整”提升到“工程可信”

5. **Alert Correlation / 告警收敛**
6. **MiniShop Evaluation Benchmark 扩展**
7. **压测 + 故障注入 + 恢复证据**
8. **Agent 自身可观测性 / 成本指标**

## P2：产品化加分项

9. **MCP Tool Adapter**
10. **Workspace / 多租户配置中心**
11. **更完整的 Ops Console**

---

# 4. P0-1：新增 Change Event / 变更事件能力

## 4.1 为什么必须做

真实线上故障 RCA 极其依赖：

> “故障发生前到底改了什么？”

例如：

```text
14:02 payment-service 发布 v2.3.1
14:04 payment error_rate 开始上升
14:05 checkout 500 告警
14:06 Incident 创建
```

如果 Agent 只有指标、日志、Trace，却不知道刚刚发布了新版本，那么 RCA 会缺少非常关键的时间因果信息。

因此必须新增：

```text
changes.query@v1
```

并使 Change Evidence 成为正式 RCA Evidence 类型。

---

## 4.2 领域模型

新增或等价实现：

```python
ChangeEvent
```

建议字段：

```text
change_event_id
tenant_id
service_name
resource_type
resource_id
change_type
source
version_before
version_after
operator_id
summary
metadata
started_at
completed_at
status
created_at
```

`change_type` 建议支持：

```text
DEPLOYMENT
CONFIG
FEATURE_FLAG
DEPENDENCY
SCHEMA
INFRASTRUCTURE
MANUAL_OPERATION
```

注意：

- metadata 必须有大小限制；
- 禁止存 Secret；
- summary 必须经过脱敏；
- 时间必须带时区；
- tenant_id / service_name 等继续遵守现有输入安全规范。

---

## 4.3 Port 与 Repository

遵循当前 Hexagonal / Ports & Adapters 分层，不允许 Agent 层直接访问 ORM。

新增类似：

```text
ChangeEventRepositoryPort
ChangeEventQueryPort
```

Repository 至少支持：

```text
save()
get_by_id()
list_for_service()
list_in_time_window()
```

重点查询：

```text
tenant + service + incident 时间窗口
```

例如：

```text
incident.starts_at - 30min
~
incident.starts_at + 10min
```

必须有合理索引。

---

## 4.4 API

增加受控 Change Event 接入接口。

例如：

```text
POST /api/v1/change-events
```

生产路径必须有鉴权。

需要：

- DTO；
- Pydantic 校验；
- 幂等；
- external_event_id 或等价唯一键；
- 审计；
- 数据脱敏；
- Repository；
- Migration；
- 单测。

---

## 4.5 Tool

新增：

```text
changes.query@v1
```

工具只允许由服务端根据：

```text
tenant
service
incident time window
```

生成查询参数。

LLM 不允许：

```text
任意 SQL
任意数据库查询
任意时间跨度
```

输出必须转成：

```text
EvidenceType.CHANGE
```

或当前项目等价 Evidence 类型。

Evidence 中至少能够表达：

```text
什么服务
什么时候
改了什么
旧版本
新版本
变更状态
与 Incident 相差多少分钟
```

---

## 4.6 MiniShop 场景

新增至少一个：

```text
deployment-regression
```

场景：

```text
payment-service v1
       ↓
模拟 deployment v2
       ↓
写入 ChangeEvent
       ↓
打开 payment regression fault
       ↓
error_rate 上升
       ↓
Alertmanager
       ↓
Incident
       ↓
RCA
```

Ground Truth 必须要求：

```text
Change Evidence
+
Metrics Evidence
+
Logs 或 Trace Evidence
```

才能认为证据充分。

禁止只看 change 就直接下结论。

---

## 4.7 验收

必须至少测试：

- 重复 Change Event 幂等；
- 时间窗口过滤；
- tenant 隔离；
- sensitive metadata 脱敏；
- changes.query 权限；
- Evidence 正确生成；
- deployment-regression E2E；
- 没有 Change 时正常返回空结果而不是整个 RCA 崩溃；
- Change 存在但与故障无关时不能错误归因。

---

# 5. P0-2：Service Topology / CMDB / Blast Radius

## 5.1 目标

当前系统不能只知道：

```text
service_name = payment
```

而需要知道：

```text
checkout
 ├── inventory
 │     └── postgres
 ├── payment
 │     └── third-party-payment
 └── notification
```

这样 RCA 才能回答：

- 根因节点是谁；
- 上游受谁影响；
- 下游有哪些；
- 哪些服务只是被波及；
- 故障影响面有多大。

---

## 5.2 领域模型

建议：

```text
ServiceNode
ResourceNode
DependencyEdge
```

`ServiceNode`：

```text
node_id
tenant_id
service_name
environment
owner_team
criticality
metadata
```

`ResourceNode` 可表示：

```text
PostgreSQL
Redis
Kafka
External API
Object Storage
Kubernetes workload
```

`DependencyEdge`：

```text
edge_id
tenant_id
source_node_id
target_node_id
dependency_type
source
confidence
first_seen_at
last_seen_at
```

dependency_type：

```text
HTTP
RPC
DATABASE
CACHE
MESSAGE_QUEUE
EXTERNAL_API
```

---

## 5.3 拓扑数据来源

第一版不要做复杂 CMDB。

支持两类来源即可：

### A. 静态声明

MiniShop Manifest / 管理接口注册。

### B. Trace 推导

从 Tempo Trace 中提取：

```text
parent service
child service
span kind
destination
```

生成运行时 dependency edge。

必须：

- bounded；
- tenant isolated；
- 不能因为单条异常 Trace 永久污染拓扑；
- 有 `source` 和 `confidence`；
- 可以设置 TTL 或更新时间。

---

## 5.4 新增工具

```text
topology.query@v1
```

支持：

```text
incident service
upstream depth <= N
downstream depth <= N
```

默认最大深度建议：

```text
2 或 3
```

禁止无界图遍历。

---

## 5.5 Blast Radius

新增一个应用层服务：

```text
BlastRadiusService
```

输入：

```text
suspected_root_service
topology
incident scope
```

输出：

```text
directly_affected_services
indirectly_affected_services
critical_dependencies
impact_summary
```

它不一定要调用 LLM。

优先使用确定性图算法。

---

## 5.6 RCA Report 扩展

结构化报告新增：

```text
suspected_root_node
causal_chain
affected_services
blast_radius
```

例如：

```text
payment-service deployment
        ↓
payment 500
        ↓
checkout downstream failure
        ↓
checkout error_rate > threshold
```

所有节点必须可以关联 Evidence ID。

---

## 5.7 验收

至少覆盖：

- topology 多租户隔离；
- 环检测；
- 最大深度；
- 重复边去重；
- Trace 派生拓扑；
- 静态拓扑；
- payment fault → checkout 被影响；
- notification 不被错误标记为根因；
- Blast Radius 有 deterministic test。

---

# 6. P0-3：Bounded Dynamic Investigation / 有界动态 RCA

## 6.1 核心原则

**不要实现自由式 ReAct。**

不要：

```python
while True:
    ask_llm_what_tool_to_call()
```

正确目标：

> LLM 可以提出“下一步调查建议”，但后端 Policy Engine 决定是否允许执行。

---

## 6.2 保留现有固定策略

当前：

```text
fixed_default
fixed_no_traces
fixed_metrics_logs_runbooks
```

全部保留。

新增：

```text
bounded_dynamic_v1
```

并且：

```text
默认仍然 fixed_default
```

不能破坏已有行为。

---

## 6.3 Investigation State

新增类似：

```python
InvestigationState
```

至少包含：

```text
incident
completed_steps
failed_steps
evidence
remaining_budget
observed_signals
candidate_root_services
visited_tools
```

---

## 6.4 Step Decision

每轮产生结构化：

```python
StepDecision
```

例如：

```json
{
  "next_tool": "changes.query@v1",
  "reason_code": "RECENT_CHANGE_SUSPECTED",
  "target_service": "payment-service",
  "required_evidence_types": ["METRICS", "CHANGE"],
  "stop": false
}
```

---

## 6.5 后端必须验证

任何 LLM / Planner 候选都必须经过：

```text
Policy Validator
```

验证：

- Tool 是否白名单；
- Tool 是否只读；
- operator 是否有权限；
- 最大步骤是否超限；
- 同工具是否重复；
- 是否已有等价 Evidence；
- service 是否属于当前 Incident / Topology 范围；
- 时间窗口是否合法；
- Token / Tool Cost 预算；
- Tool 参数是否能由后端安全构造。

模型不能直接传最终 Tool Payload。

推荐：

```text
LLM 输出意图
后端生成真实参数
```

---

## 6.6 建议规则

第一版可以大量使用确定性规则。

例如：

### Rule 1

```text
Metrics error_rate ↑
→ Logs
```

### Rule 2

```text
Logs 包含 timeout / connection / db
→ Traces + Topology
```

### Rule 3

```text
故障前 30 分钟存在 deployment/config change
→ Change
```

### Rule 4

```text
出现已知错误 fingerprint
→ Historical Incident Retrieval
```

### Rule 5

```text
Evidence 已达到最低覆盖
或 remaining_budget == 0
→ Stop
```

---

## 6.7 Budget

必须支持：

```text
max_steps
max_total_duration_ms
max_tool_calls_per_type
max_evidence_count
max_llm_calls
```

全部配置化。

任何动态调查都必须可终止。

---

## 6.8 Checkpoint / Resume

不要重新发明一个新的工作流持久化系统。

优先复用现有：

```text
WorkflowRun
ExecutionCoordinator
Lease
Heartbeat
Fence
```

新增 Investigation State 持久化时，要确保：

```text
Worker 崩溃
        ↓
新 Worker claim
        ↓
读取上次 state
        ↓
从安全节点继续
```

只读 Tool 可以按照现有幂等策略安全重查。

有副作用动作仍然不进入 RCA 动态调查。

---

## 6.9 验收

必须验证：

- 固定 policy 行为完全不变；
- bounded_dynamic 有最大步数；
- 模型提出未授权工具时拒绝；
- 模型提出 Shell 时拒绝；
- 同一工具无限循环被阻止；
- 中途 Worker crash 后可恢复；
- 部分 Tool 失败遵守现有降级策略；
- 0 Evidence 不生成假 RCA；
- bounded_dynamic 的结果可以重放审计。

---

# 7. P0-4：Historical Incident RAG / 历史事故知识库

## 7.1 目标

实现：

```text
过去发生过相似事故吗？
当时根因是什么？
哪些证据相似？
最后怎么解决？
```

但：

> 历史事故只是“参考 Evidence”，不是本次 Incident 的事实。

---

## 7.2 Knowledge 类型

至少支持：

```text
Historical Incident
Postmortem
Runbook
Known Error
Resolution Note
```

---

## 7.3 数据模型

建议：

```text
KnowledgeDocument
KnowledgeChunk
```

metadata：

```text
tenant_id
document_type
service_name
source_incident_id
version
created_at
published_at
review_status
```

只有：

```text
PUBLISHED / APPROVED
```

内容允许进入在线检索。

---

## 7.4 检索架构

第一版优先保持基础设施简单：

```text
Metadata Filter
        ↓
PostgreSQL FTS / lexical retrieval
        ↓
可选 Embedding Retrieval
        ↓
Rerank
        ↓
TopK
```

如果实现向量能力，优先通过 Port 抽象：

```text
KnowledgeRetrieverPort
EmbeddingPort
```

底层可选择：

```text
PostgreSQL + pgvector
```

但不要让业务层依赖具体向量数据库。

---

## 7.5 Query 构造

查询不应只使用用户问题。

RCA 场景 Query 由后端从：

```text
service
alert summary
error fingerprint
important log keywords
trace error names
recent change type
```

生成结构化检索条件。

---

## 7.6 Rerank

可以先采用轻量实现：

```text
metadata score
+
lexical score
+
embedding score（若启用）
+
service match
+
error fingerprint match
```

不要为了简历强行引入独立 Rerank 大模型。

---

## 7.7 Evidence

检索结果保存为：

```text
EvidenceType.KNOWLEDGE
```

报告中必须明确：

```text
“历史类似事故提示……”
```

而不是：

```text
“本次事故已经确认……”
```

---

## 7.8 防幻觉约束

历史事故不能直接让：

```text
conclusion_status = CONFIRMED
```

历史知识只能提升：

```text
candidate confidence
```

前提仍然需要本次 Metrics / Logs / Traces / Change 等现场 Evidence。

---

## 7.9 验收

至少验证：

- tenant 过滤；
- unpublished 不可检索；
- 历史 Incident 可入库；
- 相似 error fingerprint 能召回；
- 不相关服务不应排第一；
- RAG 无结果时 RCA 正常；
- 故意加入错误历史知识，系统不能把它当本次事实；
- 所有输出可追溯到 Knowledge Document ID。

---

# 8. P1-1：Alert Correlation / 告警收敛

## 8.1 目标

模拟：

```text
DB timeout
→ inventory latency
→ checkout latency
→ checkout 500
→ p95 告警
→ error_rate 告警
```

不能产生 5~20 个独立 Incident。

需要：

```text
Alert Storm
      ↓
Correlation
      ↓
1 Incident
```

---

## 8.2 Correlation Key

不要仅依赖完全一致 fingerprint。

综合：

```text
tenant
environment
service / topology
alert type
time window
severity
labels
dependency relationship
```

---

## 8.3 Primary Alert

Incident 保存：

```text
primary_alert_id
correlated_alert_count
correlation_reason
```

Primary Alert 可根据：

```text
最早发生
+
最靠近疑似根因节点
+
severity
```

进行确定性评分。

---

## 8.4 测试

模拟 20 条相关告警：

目标：

```text
20 Alerts
→ 1 Incident
```

再模拟 2 个无关服务同时告警：

不能错误聚合成同一 Incident。

---

# 9. P1-2：MiniShop 升级为 Evaluation Benchmark

当前 3 个场景继续保留。

目标扩展到至少 10~12 个。

推荐场景：

1. `payment-error`
2. `inventory-db-timeout`
3. `checkout-latency`
4. `deployment-regression`
5. `config-regression`
6. `redis-latency`
7. `connection-pool-exhaustion`
8. `third-party-api-timeout`
9. `cpu-saturation`
10. `memory-pressure`
11. `cascading-failure`
12. `false-positive-alert`

每个 Scenario Manifest 必须定义：

```text
injection
trigger
cleanup
expected_signals
required_evidence
root_cause
causal_chain
affected_services
forbidden_claims
```

---

# 10. 评测指标

不要只计算：

```text
final answer 对不对
```

至少新增：

## 10.1 RCA 指标

```text
RCA Top-1 Accuracy
Root Cause Service Accuracy
Causal Chain Accuracy
Blast Radius Accuracy
Evidence Coverage
Unsupported Claim Rate
Forbidden Claim Rate
```

## 10.2 Agent 指标

```text
Tool Selection Accuracy
Tool Parameter Validation Failure Rate
Average Investigation Steps
Redundant Tool Call Rate
Partial Failure Recovery Rate
```

## 10.3 工程指标

```text
RCA P50 / P95 Latency
Tool P95 Latency
Kafka Consumer Lag
Outbox Backlog
Workflow Recovery Time
Duplicate Completion Count
```

## 10.4 LLM 成本

```text
prompt tokens
completion tokens
total tokens
estimated cost
LLM calls per RCA
```

不要编数字。

必须真实跑 benchmark 后再写到 README / 简历。

---

# 11. P1-3：压测与故障注入

## 11.1 压测工具

建议：

```text
k6
```

或：

```text
Locust
```

选一个即可。

---

## 11.2 压测档位

至少：

```text
100 alerts/min
500 alerts/min
1000 alerts/min
```

如果本地机器能力有限，可以降低，但文档必须写真实配置。

观测：

```text
HTTP P95
DB pool
Outbox backlog
Kafka lag
RCA queue latency
CPU
Memory
Error Rate
```

---

## 11.3 故障注入

至少测试：

### Case A：Kill RCA Consumer

```text
running RCA
↓
kill worker
↓
lease expires
↓
new worker claim
↓
resume
```

记录：

```text
recovery_seconds
duplicate_completion
lost_workflow
```

### Case B：Kafka 短暂不可用

验证：

```text
Outbox 不丢事件
Kafka 恢复后继续发布
```

### Case C：PostgreSQL 短暂不可用

验证：

- API 错误是否可控；
- Worker 是否不会错误提交；
- 恢复后是否可继续。

### Case D：Observability Tool Timeout

例如：

```text
Loki timeout
```

验证：

```text
continue_on_step_failure
confidence cap
partial marker
```

---

# 12. P1-4：Agent 自身可观测性

不仅监控 MiniShop，也监控 Agent 自己。

Prometheus Metrics 至少：

```text
devops_agent_rca_total
devops_agent_rca_duration_seconds
devops_agent_rca_failed_total
devops_agent_tool_invocations_total
devops_agent_tool_duration_seconds
devops_agent_tool_failures_total
devops_agent_llm_requests_total
devops_agent_llm_tokens_total
devops_agent_outbox_backlog
devops_agent_consumer_lag
devops_agent_workflow_reclaims_total
devops_agent_partial_reports_total
```

标签必须保持低基数。

禁止：

```text
incident_id
workflow_id
tenant_id
trace_id
```

直接作为 Prometheus label。

---

# 13. P2-1：MCP Tool Adapter

不是让模型直接连接任意 MCP Server。

正确架构：

```text
LLM
 ↓
Tool Intent
 ↓
ToolRegistry
 ↓
Permission / Risk / Schema / Timeout
 ↓
MCPToolAdapter
 ↓
Approved MCP Server
```

MCP 工具仍必须遵循：

- allowlist；
- permission tags；
- timeout；
- result size；
- sensitive output redaction；
- audit；
- readonly / write risk classification。

第一版只接一个只读 Demo MCP Server 即可。

---

# 14. P2-2：Workspace

在 tenant 之上或内部增加：

```text
Workspace
```

Workspace 可以配置：

```text
Prometheus target
Loki target
Tempo target
Knowledge scope
Investigation policy
Allowed tools
LLM provider policy
Retention
```

目标是模拟云厂商：

> 不同业务团队可以有不同的观测源和调查策略。

不要为了 Workspace 重写整个 tenant 系统。

---

# 15. 数据库与 Migration 原则

任何新增实体：

- 必须 Alembic migration；
- 必须 downgrade；
- 必须索引；
- 必须 FK 策略明确；
- 必须 tenant 隔离；
- 必须 timezone aware；
- 必须限定 JSON / text 尺寸；
- 必须有 Repository test。

建议索引重点：

```text
ChangeEvent:
(tenant_id, service_name, started_at)

Topology Edge:
(tenant_id, source_node_id)
(tenant_id, target_node_id)

Knowledge:
(tenant_id, document_type, service_name, review_status)

Alert Correlation:
(tenant_id, created_at)
```

---

# 16. 安全要求

新增功能必须继承现有安全边界。

## 16.1 Change Event

不能保存：

```text
secret
token
password
private key
```

## 16.2 Knowledge

历史 Postmortem 进入知识库前：

```text
redaction
review
publish
```

## 16.3 Dynamic Investigation

LLM 无权：

```text
执行写操作
构造任意查询
修改数据库
执行 shell
突破 Tool Registry
```

## 16.4 Topology

不同 tenant 的节点不能互相可见。

## 16.5 MCP

未知 MCP Server 默认拒绝。

---

# 17. 推荐的实现顺序

严禁一次性改全部。

建议分 8 个阶段，每阶段单独提交。

---

## Phase 1：Change Event

完成：

```text
Domain
Repository
Migration
API
Tool
Evidence
MiniShop deployment regression
Tests
Docs
```

验收后再 Phase 2。

---

## Phase 2：Topology + Blast Radius

完成：

```text
Topology domain
Repository
Tool
Trace / static source
BlastRadiusService
Report
Tests
MiniShop validation
```

---

## Phase 3：Bounded Dynamic Investigation

完成：

```text
policy key
InvestigationState
StepDecision
Policy Validator
Budget
checkpoint/resume
tests
```

必须确认 fixed policy regression test 全绿。

---

## Phase 4：Historical Incident Retrieval

完成：

```text
knowledge domain
publish lifecycle
retrieval port
FTS / optional vector
rerank
Knowledge Evidence
tests
```

---

## Phase 5：Alert Correlation

完成：

```text
correlation service
primary alert
storm test
topology-aware correlation
```

---

## Phase 6：Benchmark

新增至少 10 个场景并产出：

```text
results.json
evaluation-report.md
```

---

## Phase 7：Load / Chaos

产出：

```text
benchmark configuration
raw results
summary
known limitations
```

---

## Phase 8：MCP / Workspace

最后做。

---

# 18. 每个 Phase 的 AI 工作方式

每个阶段必须按照以下流程：

## Step 1：代码考古

先搜索当前实现，输出：

```text
已有能力
可复用类
可复用 Port
可复用 Repository
可复用 Worker
会被影响的测试
```

不要先写代码。

## Step 2：设计

写：

```text
目标
非目标
数据流
状态机
失败路径
安全边界
兼容策略
```

## Step 3：先写测试

优先：

```text
domain test
service test
repository test
policy test
```

再实现。

## Step 4：实现

保持当前：

```text
interfaces
application
domain
ports
infrastructure
agent
tools
bootstrap
```

分层。

## Step 5：验证

至少：

```bash
uv run ruff check .
uv run pytest -q -m "not live"
```

如果当前仓库命令不同，以仓库锁定配置为准。

## Step 6：更新文档

每个 Phase 新建：

```text
docs/xxx.md
```

内容必须包括：

```text
为什么做
怎么做
数据流
关键类
失败策略
如何测试
已实现
未实现
面试可以说什么
不能说什么
```

最后更新：

```text
README.md
缺少内容.md
项目面试文档.md
```

---

# 19. Definition of Done

一个功能只有满足下面所有条件才算完成：

- [x] 领域模型存在；
- [x] Port 存在；
- [x] Infrastructure Adapter 存在；
- [x] Migration 存在；
- [x] Runtime 已接线；
- [x] 权限已接线；
- [x] 审计已接线；
- [x] Redaction 已接线；
- [x] 单测通过；
- [x] 集成 / E2E 有验证；
- [x] README 已更新；
- [x] `缺少内容.md` 已更新；
- [x] 未实现边界仍明确；
- [x] 没有破坏已有固定 RCA；
- [x] 没有新增无界循环；
- [x] 没有把本地验证写成生产上线。

---

# 20. 最终希望形成的面试主线

项目最终应该能讲出下面这条完整故事。

## 20.1 业务问题

传统线上故障排查需要工程师分别查看：

```text
告警
指标
日志
链路
最近变更
服务拓扑
Runbook
历史事故
```

信息分散且强依赖人工经验。

---

## 20.2 第一版

最初实现：

```text
Alert
→ Incident
→ Metrics
→ Logs
→ Traces
→ Runbook
→ Evidence
→ RCA
```

但发现：

- 固定流程不能根据现场决定是否继续调查；
- 不知道最近是否发生变更；
- 不知道服务依赖关系；
- 无法利用历史事故；
- 告警风暴会产生噪声。

---

## 20.3 第二版

因此增加：

```text
Change Event
Service Topology
Historical Incident Retrieval
Alert Correlation
```

让 Agent 的上下文从“四类 observability signals”升级成更完整的事故上下文。

---

## 20.4 为什么没有使用自由 ReAct

因为 DevOps 工具直接连接生产系统。

自由 Agent 存在：

```text
工具误选
参数幻觉
重复执行
权限越界
无限循环
成本不可控
```

因此使用：

```text
LLM 提交调查意图
      ↓
Backend Policy Validator
      ↓
Tool Registry
      ↓
Evidence
```

并通过：

```text
max_steps
allowed tools
budget
permission
timeout
idempotency
checkpoint
```

约束。

---

## 20.5 为什么 Kafka + Outbox

RCA 是长任务。

不能让：

```text
HTTP Request
```

一直等待 Agent。

同时存在：

```text
数据库成功
Kafka 发送失败
```

的一致性问题。

因此：

```text
DB Transaction
  ├─ Workflow
  └─ Outbox

Commit
  ↓
Outbox Worker
  ↓
Kafka
```

---

## 20.6 Worker 崩溃怎么办

通过：

```text
Claim
Lease
Heartbeat
Owner
Attempt
Fence
```

避免：

```text
Worker A 挂掉
Worker B 接管
Worker A 又回来
然后覆盖 B 的结果
```

---

## 20.7 LLM 幻觉怎么办

报告必须：

```text
引用 Evidence ID
```

且：

```text
LLM 不允许 CONFIRMED
```

Provider 异常时：

```text
failover
→ deterministic fallback
```

部分 Evidence 时降低 confidence。

---

# 21. 最终简历可写方向

**只有对应模块真正完成并有测试后才能写。**

目标简历可以逐渐升级为：

> 面向云上微服务异常诊断场景，参与/设计智能运维 AIOps 平台，统一接入告警、Metrics、Logs、Traces、Change Event、服务拓扑与历史事故知识，通过受控 Agent 工作流自动采集 Evidence、分析根因与影响范围。

可以重点写：

### 亮点 1：多源 RCA

```text
Metrics / Logs / Traces / Change / Topology / Knowledge
```

### 亮点 2：受控 Agent

```text
Bounded Dynamic Investigation
+
Policy Validator
+
Tool Registry
+
Evidence Gate
```

### 亮点 3：可靠性

```text
Transactional Outbox
+
Kafka
+
Claim / Lease / Heartbeat
+
Owner / Attempt Fence
+
Idempotency
```

### 亮点 4：AIOps Benchmark

```text
MiniShop
+
Fault Injection
+
Ground Truth
+
RCA / Evidence / Tool Evaluation
```

### 亮点 5：安全

```text
OIDC
HMAC
Permission
Redaction
Approval
Remediation whitelist
```

---

# 22. 禁止出现在简历中的话术

除非未来真的完成，否则禁止：

```text
已在腾讯生产环境上线
承载腾讯真实业务流量
腾讯 Cloud Mate 项目
生产准确率 XX%
自主多 Agent 自动修复
完全无人值守
自由调用任意运维工具
自动执行任意 Shell / K8s 命令
RAG 已线上运行（如果未接线）
Prompt 自动灰度（如果未接线）
```

可以说：

```text
面向云厂商 AIOps 场景
生产导向设计
本地真实观测栈 E2E
故障演练环境
可重复 Benchmark
```

---

# 23. 本轮改造最重要的 4 个交付物

如果时间有限，只做下面四个：

## 1. ChangeEvent

最终链路：

```text
Deployment
→ ChangeEvent
→ changes.query
→ Evidence
→ RCA
```

## 2. Topology + Blast Radius

```text
Trace / Static CMDB
→ Service Graph
→ topology.query
→ causal chain
→ affected services
```

## 3. Bounded Dynamic Investigation

```text
LLM / Rules
→ Step Intent
→ Backend Validation
→ Tool
→ Evidence
→ Next State
```

有限步骤，可恢复，可审计。

## 4. Historical Incident Retrieval

```text
Incident / Postmortem
→ Knowledge Store
→ Retrieve
→ Rerank
→ Knowledge Evidence
```

历史信息只是辅助，不取代现场证据。

完成这四项后，项目从：

> “受控 RCA Agent 后端”

升级为：

> **“云上智能运维 / AIOps 故障诊断平台”**

并且技术叙事仍然可以完全由源码和测试支撑。

---

# 24. 给编程 Agent 的最终约束

执行本计划时始终遵循：

> **叙事 ≤ 代码。**

> **安全边界优先于 Agent 自主性。**

> **确定性后端能力优先于用 LLM 解决一切。**

> **任何写操作都不能因为 Agent 重试而被重复执行。**

> **任何 RCA 结论都必须能够追溯 Evidence。**

> **每增加一个“智能能力”，必须同时增加预算、权限、失败、恢复、审计和评测设计。**

不要为了看起来“高级”而引入没有必要的：

```text
Multi-Agent
复杂框架
新数据库
新中间件
```

优先复用当前项目已经成熟的：

```text
PostgreSQL
Kafka
Outbox
Workflow
ToolRegistry
Evidence
Lease / Fence
MiniShop
Prometheus / Loki / Tempo
```

最终目标不是做一个功能最多的 Demo，而是做一个：

> **面试官沿着代码继续追问时，架构、边界、失败路径、测试和业务逻辑都能自洽的企业级 Agent 项目。**
