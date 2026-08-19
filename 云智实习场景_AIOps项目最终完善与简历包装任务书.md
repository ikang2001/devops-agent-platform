# 云上智能运维 / AIOps 故障诊断平台
## 企业实习场景最终完善与简历包装任务书

> 目标仓库：`https://github.com/ikang2001/devops-agent-platform`
>
> 当前代码基线：`v0.4.0`
>
> 文档用途：直接交给 Codex / Claude Code / Cursor / 其他编程 Agent，作为项目最终完善、文档对齐和简历材料整理任务书。
>
> 适用前提：**如果这段云智研发实习是真实发生的**，可以按“企业实习项目”组织叙事；如果并非真实实习，不得把个人项目改写成腾讯/云智内部项目，只能使用“面向云厂商 AIOps 场景的企业级实践项目”口径。

---

# 0. 最终定位

项目最终统一命名建议：

> **云上智能运维 / AIOps 故障诊断平台**

英文可选：

> **Cloud AIOps Incident Diagnosis Platform**

内部代码仓库仍可保留：

```text
devops-agent-platform
```

不需要为了简历强行重命名仓库。

---

# 1. 企业实习语境下的业务背景

如果这是真实的云智研发实习，可以使用如下业务背景：

> 面向云上微服务异常诊断场景，参与建设智能运维 / AIOps 故障诊断平台，通过统一接入告警、Metrics、Logs、Traces、Change Event、服务拓扑与历史故障知识，辅助运维和研发人员完成 Incident 聚合、根因分析、影响面判断和后续故障处置。

不要写：

```text
“公司业务就是 MiniShop”
“我负责腾讯内部电商业务”
“这是腾讯 Cloud Mate 项目”
```

除非这些事实真实存在并可验证。

---

# 2. MiniShop 在简历和面试中的正确定位

代码中可以继续保留：

```text
MiniShop
checkout-service
inventory-service
payment-service
notification-service
```

但简历和面试统一改称：

> **云原生微服务故障诊断测试与演练环境**

或者：

> **AIOps 故障演练与评测环境**

不要在简历正文中突出 `MiniShop` 名字。

它的角色是：

```text
真实线上系统不可随意制造故障
        ↓
搭建独立微服务演练环境
        ↓
可控注入故障
        ↓
产生真实 Metrics / Logs / Traces / Change / Alert
        ↓
验证 AIOps RCA 能否正确排障
```

因此它是：

> **测试靶场 / 故障演练环境 / Benchmark 环境**

不是公司的核心业务产品。

---

# 3. 最终系统关系

```text
            公司 / 云上业务系统
                    │
                    │ 告警与可观测数据
                    ↓
        云上智能运维 / AIOps 平台
                    │
                    ├── Alert
                    ├── Incident
                    ├── RCA
                    ├── Evidence
                    ├── Topology
                    ├── Historical Knowledge
                    └── Remediation
                    ↑
                    │ 验证
                    │
      云原生微服务故障演练与评测环境
                    │
                    ├── 订单服务
                    ├── 库存服务
                    ├── 支付服务
                    └── 通知服务
```

---

# 4. 简历中“公司业务”和“个人工作”必须分开

企业实习简历最容易出问题的是：

> 把整个团队平台的能力全部写成自己一个人做的。

因此要分四层。

---

## 4.1 公司 / 团队业务背景

可以写：

> 团队面向云上智能运维 / AIOps 场景建设故障诊断能力，平台覆盖告警接入、Incident 管理、可观测证据采集、RCA、工单及审批式故障处置。

这是：

```text
团队 / 平台背景
```

不是个人独占成果。

---

## 4.2 个人主要负责模块

简历只重点写 2~3 个真正自己能讲透、源码能支撑的模块。

推荐优先级：

### 模块 A：RCA Agent 工作流与工具治理

```text
Controlled RCA Workflow
ToolRegistry
Evidence
Permission
Timeout
Partial Failure
LLM Report
```

### 模块 B：异步任务与可靠性治理

```text
Transactional Outbox
Kafka
Claim / Lease / Heartbeat
Owner / Attempt Fence
Idempotency
```

### 模块 C：AIOps Evaluation / 故障演练

```text
故障演练环境
Ground Truth
12 Scenario Benchmark
Scorer
Bad Case
Chaos
```

这三个最适合 Agent / AI Backend 实习岗位。

---

## 4.3 可以写“参与”的模块

如果不是你主要实现：

```text
Topology
Workspace
MCP
Dataset Release
Ticket
Remediation
```

用：

```text
参与
接入
联调
配合
完善
```

不要全部写：

```text
负责设计并实现
```

---

## 4.4 本地验证环境

故障演练和 Reference Staging 应明确是：

```text
测试 / 演练 / Reference / 本地验证环境
```

不能写成：

```text
生产环境
真实客户集群
腾讯正式线上流量
```

---

# 5. 当前技术项目的最终目标架构

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
 ┌──────────┼──────────┬──────────┬──────────┐
 ↓          ↓          ↓          ↓          ↓
Metrics   Change      Logs      Traces    Topology
                                           ↓
                                      Knowledge
                                           ↓
                                        Runbook
                                           ↓
                                        Evidence
                                           ↓
                        Root Cause / Causal Chain
                              / Blast Radius
                                           ↓
                                      RCA Report
                                           ↓
                         Human Review / Remediation
```

---

# 6. 当前代码已经完成的能力

AI 修改前必须先确认这些已经存在，禁止重复实现：

```text
FastAPI
PostgreSQL
SQLAlchemy Async
Alembic

Alert / Incident / WorkflowRun
Evidence / ToolInvocation

Transactional Outbox
Kafka / Redpanda

Claim / Lease / Heartbeat
Owner / Attempt Fence

ToolRegistry
Permission
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

12 Scenario Benchmark
Scorer
Bad Cases
Ablation

Reference Staging
Load Harness
Chaos Harness
```

---

# 7. 当前仍需完善的 4 个核心问题

本轮不再扩 scope，只完成以下 4 项：

```text
1. bounded_dynamic_v1 真正接入 RCA Runtime
2. Topology / Knowledge 真正进入动态调查
3. AlertCorrelationService 真正进入 Alert → Incident 主链
4. 真实 LLM Benchmark + Ablation
```

---

# 8. Phase 1：Bounded Dynamic Runtime 真正接线

当前：

```text
bounded_dynamic.py
```

已经有：

```text
InvestigationState
StepDecision
IntentPlannerPort
InvestigationPolicyValidator
BoundedDynamicInvestigator
Checkpoint
Resume
Budget
```

但是当前主 RCA Runtime 仍主要是：

```text
build_plan_for_policy()
→ ControlledAgentWorkflow
```

目标：

当配置：

```text
rca_investigation_policy=bounded_dynamic_v1
```

时真正进入：

```text
BoundedDynamicInvestigator.run()
```

---

# 9. Runtime 兼容原则

固定策略继续保留：

```text
fixed_default
fixed_no_traces
fixed_metrics_logs_runbooks
```

继续走：

```text
ControlledAgentWorkflow
```

动态策略：

```text
bounded_dynamic_v1
```

走：

```text
DynamicRCAWorkflowAdapter
→ BoundedDynamicInvestigator
```

推荐抽象：

```text
RCAExecutionCoordinator
        ↓
WorkflowExecutorPort
        ↓
┌──────────────────┬────────────────────┐
│ Fixed Workflow   │ Dynamic Workflow   │
└──────────────────┴────────────────────┘
```

---

# 10. Planner 设计

Planner 只输出：

```text
调查意图
```

例如：

```json
{
  "next_tool": "topology.query@v1",
  "target_service": "inventory-service",
  "reason_code": "DOWNSTREAM_TIMEOUT",
  "required_evidence_types": ["TOPOLOGY"],
  "stop": false
}
```

Planner 不能输出：

```text
真实 SQL
PromQL
LogQL
TraceQL
Shell
K8s 命令
```

真实参数仍由后端构造。

---

# 11. Dynamic 调查策略

推荐：

```text
Rule-first
+
LLM fallback
```

第一版规则：

```text
未查 Metrics
→ Metrics

Metrics 异常
→ Logs

Logs 有 deployment/config/version 信号
→ Change

Logs / Traces 有 dependency/timeout
→ Topology

存在 error fingerprint
→ Historical Knowledge

根因服务不明确
→ Traces

证据充分
→ Runbook / Stop
```

---

# 12. 动态调查预算

必须配置：

```text
max_steps
max_total_duration_ms
max_tool_calls_per_type
max_evidence_count
max_llm_calls
```

推荐：

```text
max_steps = 7
max_llm_calls <= 3
max_tool_calls_per_type <= 2
```

禁止无界循环。

---

# 13. Topology 真正进入 RCA

现有：

```text
topology.query@v1
```

已经注册。

本轮要求：

```text
Dynamic Planner
```

根据现场 Evidence 真正触发它。

例如：

```text
checkout error
↓
Logs: inventory timeout
↓
Topology
↓
checkout → inventory → postgres
↓
Traces / Metrics 查询 inventory
↓
定位 root service
```

---

# 14. Blast Radius 真正进入 Report

最终 RCA Report 应包含：

```text
root_cause
causal_chain
affected_services
blast_radius
```

并且：

```text
Causal Chain / Blast Radius
```

必须关联 Evidence ID。

---

# 15. Historical Knowledge 真正进入 RCA

已有：

```text
KnowledgeService
knowledge.search@v1
lexical/service/fingerprint retrieval
```

当前不强制引入：

```text
Vector DB
Embedding
```

只需真正接入动态调查。

调用条件：

```text
error fingerprint 稳定
重复 Incident pattern
known error code
相似服务故障
```

---

# 16. 历史知识必须明确“参考事实”

历史事故不能成为当前事故的直接事实。

报告中区分：

```text
当前现场证据
vs
历史参考
```

例如：

```text
当前：
ev-trace-01 显示 payment timeout

历史：
incident-1003 曾出现相似 timeout
```

历史 Knowledge 不得单独把：

```text
conclusion_status
```

升级为 `CONFIRMED`。

---

# 17. 必须保留 misleading-history 测试

测试：

```text
历史：
payment 500
root = Redis

当前：
payment 500
root = deployment regression
```

目标：

> Agent 不能因为历史相似度高就忽略当前 Change / Logs / Trace。

---

# 18. Alert Correlation 真正进入主链

当前已有：

```text
AlertCorrelationService
```

支持：

```text
tenant
environment
time
service
topology
severity
alert_type
```

但是当前主入口仍主要依赖：

```text
same service + time window
```

本轮目标：

```text
Alert
↓
candidate incidents
↓
Topology
↓
AlertCorrelationService
↓
score
↓
ATTACH / CREATE
```

---

# 19. 告警候选查询

候选不能只限制：

```text
same service
```

应先按：

```text
tenant
environment
active status
time window
```

获取候选。

然后由：

```text
AlertCorrelationService
```

判断：

```text
same service
or
topology-related service
```

---

# 20. Alert Storm 场景

在故障演练环境新增：

```text
cascading-alert-storm
```

模拟：

```text
postgres timeout
↓
inventory alert
↓
checkout latency alert
↓
checkout 5xx alert
↓
order-api alert
```

例如：

```text
20 Alerts
```

期望：

```text
1 Incident
```

同时判断：

```text
Primary Alert
Root Service
Affected Services
```

---

# 21. 故障演练环境的企业化命名

源码：

```text
MiniShop
```

可以保留。

但 README / 简历 / 面试文档优先统一写：

> **云原生微服务故障演练与评测环境**

第一次可以注明：

> 基于订单、库存、支付、通知等典型微服务依赖关系搭建。

之后不要再反复突出 MiniShop。

---

# 22. 故障演练环境应该解释成什么

推荐面试回答：

> 真实线上环境不能为了测试 Agent 主动制造事故，因此我搭建了一套独立的云原生微服务故障演练与评测环境，通过可控故障注入模拟数据库超时、接口 5xx、依赖异常、发布回归和级联故障，并接入 Prometheus、Loki、Tempo、Alertmanager 生成真实可观测信号，用 Ground Truth 验证 RCA 是否正确。

不要说：

> “公司内部有一个 MiniShop 电商业务让我测试。”

---

# 23. 真实 LLM Benchmark

当前 Benchmark 基础设施已有：

```text
12 Scenarios
Structured Prediction
Scorer
results.json
evaluation-report.md
ablation-report.md
bad_cases.jsonl
Live Runner
Token / Cost / Latency
```

本轮必须跑真正 Provider。

推荐：

```text
Qwen / DashScope
```

或其他当前项目已支持的 OpenAI-compatible Provider。

---

# 24. Benchmark 规模

最低：

```text
12 Scenarios
× 5 Runs
= 60 RCA Runs
```

如预算允许：

```text
12 × 10
```

---

# 25. Ablation 实验

至少：

```text
V1 Baseline
Metrics + Logs + Traces + Runbook

V2 + Change

V3 + Topology

V4 + Historical Knowledge

V5 Bounded Dynamic
```

---

# 26. Benchmark 指标

必须真实统计：

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

Average Tool Calls
Average Investigation Steps

P50 RCA Latency
P95 RCA Latency

LLM Calls / RCA
Average Tokens
Average Cost
```

---

# 27. Dynamic 是否成功的判断

动态 Agent 不应只追求“更智能”。

成功条件：

```text
RCA Accuracy >= Baseline
Evidence Recall 不下降
Unsupported Claim 不增加
```

同时至少满足一项：

```text
Tool Calls 降低
Token 降低
Latency 降低
```

---

# 28. Load / Chaos 回归

当前已有 Reference：

```text
100 / 500 / 1000 alerts/min
```

和：

```text
Worker
Kafka
PostgreSQL
Loki
```

Chaos。

Dynamic 接线后需要重新验证：

```text
Worker crash
→ checkpoint resume

Kafka down
→ Outbox 不丢任务

Loki timeout
→ partial RCA

Topology down
→ safe degrade

Knowledge down
→ safe degrade

Planner timeout
→ bounded failure
```

---

# 29. 企业实习版简历项目名

推荐：

> **云上智能运维 / AIOps 故障诊断平台**

如果需要英文：

> Cloud AIOps Incident Diagnosis Platform

不要把：

```text
MiniShop
```

放在项目标题。

---

# 30. 企业实习版项目描述

如果真实实习，可写：

> 面向云上微服务异常诊断场景，参与建设生产导向的 Incident / RCA 智能故障诊断平台，打通告警接入、Incident 聚合、多源可观测数据采集、RCA 生成、人工审核及审批式故障处置链路。

---

# 31. 企业实习版推荐 4 条项目亮点

## 亮点 1：RCA Agent 与工具治理

> 负责/参与受控 RCA Agent 工作流建设，通过 ToolRegistry 对 Metrics、Logs、Traces、Change、Topology、Knowledge 等调查工具统一进行权限、超时、风险、参数与结果脱敏治理，关键 RCA 结论要求绑定 Evidence，限制模型自由操作生产环境。

---

## 亮点 2：异步任务与可靠性

> 基于 PostgreSQL Transactional Outbox + Kafka 解耦长耗时 RCA 任务，通过 Claim / Lease / Heartbeat / Owner-Attempt Fence 实现 Worker 异常退出后的安全接管，避免重复执行与迟到 Worker 覆盖新结果。

---

## 亮点 3：AIOps 多源证据

> 融合 Prometheus、Loki、Tempo、Change Event、Service Topology 与历史故障知识，构建 Evidence 驱动的根因、因果链和影响面分析，并对历史知识与当前现场证据设置不同可信边界。

---

## 亮点 4：故障演练与 Evaluation

> 搭建云原生微服务故障演练与评测环境，通过故障注入模拟 DB Timeout、接口 5xx、依赖超时、Deployment Regression 和级联故障，构建 12 类 Ground Truth Benchmark，自动评测 RCA、Evidence、因果链、影响面、Tool 调用、Token 与延迟。

---

# 32. 简历中的“负责”和“参与”规则

必须按真实情况修改。

如果主要由你实现：

```text
负责
设计并实现
主导
```

如果是团队已有框架，你完成其中一部分：

```text
参与
基于现有框架实现
完成 XXX 模块
接入
联调
优化
```

推荐：

```text
平台已有 Outbox 基础设施
→ 不写“从零设计整个消息架构”

自己主要完成 RCA 消费端和状态治理
→ 写“负责 RCA 消费与任务状态治理”
```

---

# 33. 推荐实习职责边界示例

比较可信的划分：

```text
团队平台：
Incident / Outbox / Kafka / Observability 基础框架

个人负责：
RCA Consumer
Agent Workflow
Tool Governance
Evidence
Evaluation

参与：
Topology
Knowledge
Remediation
Workspace / MCP
```

具体必须根据真实实习内容调整。

---

# 34. 如果真实职责更偏后端

简历突出：

```text
Outbox
Kafka
Lease
Fence
Idempotency
Transaction
Worker
```

Agent 放第二位。

---

# 35. 如果真实职责更偏大模型应用

简历突出：

```text
RCA Workflow
Planner
ToolRegistry
Evidence
LLM Report
Evaluation
Bad Case
```

分布式可靠性放第二位。

---

# 36. 面试中如何解释“为什么有故障演练环境”

推荐：

> 因为真实生产系统不能为了测试智能排障能力主动制造故障，而且真实事故标签也不完整，所以我们使用独立微服务测试环境构造可复现故障，保证每个 Scenario 都有 Ground Truth，便于回归 Agent 的根因定位、Evidence 覆盖和因果链效果。

这比：

> “我们自己写了一个商城 Demo。”

专业得多。

---

# 37. 面试中如何解释“为什么不用自由 ReAct”

推荐：

> 运维 Tool 直接连接可观测平台甚至后续修复系统，自由式 ReAct 容易出现 Tool 误选、参数幻觉、重复调用、权限越界和无限循环。因此 Planner 只提交调查意图，后端统一完成 Tool 白名单、权限、预算、服务范围和参数构造，再由执行层获取 Evidence。

---

# 38. 面试中如何解释“为什么用 Outbox”

推荐：

> RCA 是异步长任务。HTTP 事务提交成功但 Kafka 发布失败会产生双写不一致，所以把 Workflow 和 Outbox Event 放在同一个 PostgreSQL 事务中提交，再由 Outbox Worker 异步发布 Kafka。

---

# 39. 面试中如何解释 Worker 崩溃

```text
Worker A claim
↓
执行
↓
A crash
↓
Lease 过期
↓
Worker B 接管
↓
A 又恢复
↓
Fence 拒绝 A 的迟到结果
```

关键术语：

```text
Claim
Lease
Heartbeat
Owner
Attempt
Fence
```

---

# 40. 简历量化数字使用规则

只有真实 Benchmark 跑完后才能写：

```text
RCA Accuracy A% → B%
Tool Calls C → D
Token -E%
P95 Latency F → G
```

所有数字必须来源：

```text
ops/evaluation/artifacts/.../results.json
```

不能根据 Stub 或 Reference Provider 编造。

---

# 41. 当前不能说的话

即使真实在云智研发实习，也不能因为公司背景就自动把个人项目升级成正式产品。

禁止：

```text
我负责腾讯 Cloud Mate
承载腾讯云真实客户流量
已生产上线
生产准确率 XX%
完全自适应多轮 Agent
自动修复任意生产故障
自由执行 Shell / K8s
完整向量 RAG 已上线
自动 Prompt 灰度
自动学习闭环
```

除非这些都是真实发生并能被证明。

---

# 42. 企业实习版与个人项目版的区别

## 真实企业实习

可以写：

```text
腾讯云智研发｜后台开发 / 大模型应用开发实习生
云上智能运维 / AIOps 故障诊断平台
```

但必须保证：

```text
公司
时间
团队
项目
个人职责
```

都真实。

## 个人项目

写：

```text
云上智能运维 / AIOps 故障诊断平台
企业级实践项目
```

不能挂腾讯/云智实习经历。

---

# 43. AI 最终修改任务

本轮 AI 只完成：

```text
Phase 1
Bounded Dynamic Runtime 接线

Phase 2
Topology + Knowledge 进入动态调查

Phase 3
AlertCorrelation 进入 Alert 主链

Phase 4
云原生微服务故障演练环境命名与文档统一

Phase 5
Alert Storm E2E

Phase 6
真实 LLM Benchmark

Phase 7
Ablation / Bad Case / Chaos 回归

Phase 8
README / 面试文档 / 简历素材收口
```

---

# 44. 每个 Phase 的完成标准

- [ ] 源码真实接线；
- [ ] 默认策略兼容；
- [ ] 单元测试；
- [ ] 集成测试；
- [ ] E2E；
- [ ] 失败路径；
- [ ] 权限边界；
- [ ] Audit / Redaction；
- [ ] Metrics；
- [ ] 文档；
- [ ] `缺少内容.md`；
- [ ] 简历话术与源码一致。

---

# 45. 最终停止条件

完成：

```text
bounded_dynamic runtime
Topology dynamic usage
Knowledge dynamic usage
Alert correlation main flow
Alert storm E2E
Real LLM 12×5
Ablation
```

后停止继续增加功能。

不再增加：

```text
Multi-Agent
新向量数据库
新 MQ
新 Agent Framework
自由 ReAct
任意 Shell
自动模型训练
```

---

# 46. 最终项目竞争力

完成后，该项目应该能同时体现：

```text
云上 AIOps 业务理解
+
Agent 应用开发
+
后端工程
+
分布式可靠性
+
可观测
+
安全治理
+
Evaluation
+
Bad Case
+
故障演练
```

这才是该项目最适合作为 Agent / AI Backend 秋招第一项目的状态。

---

# 47. 最终一句话

> **不是把一个 Demo 包装成公司业务，而是让一个真实可解释、可验证、可审计的 AIOps 技术项目，用符合企业研发语境的方式呈现在简历里。**

---

# 本轮实施状态（2026-08）

按照本文档的“真实组件 + 独立演练环境 + 可验证证据”原则，已完成一轮本地高保真仿真：

- 使用真实 PostgreSQL、Redpanda/Kafka、OIDC/JWKS、Prometheus、Loki、Tempo、Worker、MCP 和持久化 Ticketing 仿真服务；
- 直接调用项目 `.env` 中的 DashScope 千问 `qwen3.7-plus`，未部署 Ollama 或本地模型；
- 完成 12 场景 × 5 次 RCA（60 次）和五变体消融（300 次），Dynamic 门禁 PASS；
- 完成 100/500/1000 alerts/min 本地 HTTP 负载、Worker/Kafka/PostgreSQL/Loki 故障注入和 Partial Report 降级验证；
- 全量测试 `1875 passed, 9 skipped`，Ruff 和 `git diff --check` 通过。

证据目录和指标详见 [`docs/local-high-fidelity-simulation-acceptance.md`](docs/local-high-fidelity-simulation-acceptance.md)。这些结果是本地仿真证据，不能改写为腾讯/云智内部生产经历，也不能替代目标企业的 staging 凭据、线上流量、组织审批和生产签字。
