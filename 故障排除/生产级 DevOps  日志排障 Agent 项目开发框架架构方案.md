# 生产级 DevOps / 日志排障 Agent 项目开发框架架构方案

## 1. 项目定位

本项目定位为一套面向企业微服务体系的 **DevOps 智能排障 Agent 平台**，用于在服务告警、接口异常、日志错误、调用链延迟、发布异常、基础设施故障等场景下，自动完成故障信号采集、证据聚合、根因分析、处理建议生成、工单流转和复盘沉淀。

系统不是 Demo 级日志问答工具，而是面向正式生产环境的长期可迭代平台，核心目标包括：

- 支持多团队、多服务、多环境接入
- 支持长期功能演进和模块替换
- 支持监控、日志、Trace、告警、工单等企业系统集成
- 支持 RAG、工具调用、工作流 Agent、规则引擎、人工确认等能力
- 支持权限控制、审计追踪、灰度发布、版本维护和横向扩展
- 支持可观测、可回放、可评测、可治理的 Agent 执行体系

------

## 2. 整体架构设计思想

### 2.1 架构核心思想

本项目采用 **分层架构 + 领域驱动设计 + 插件化工具体系 + 工作流式 Agent 编排** 的架构思想。

核心原则是：

> 业务流程由工作流控制，领域规则由领域层承载，外部系统通过适配器接入，Agent 能力通过引擎层封装，LLM 只作为推理与归纳能力，不直接污染核心业务模型。

系统整体分为：

```text
接入层
↓
接口层
↓
应用编排层
↓
领域层
↓
Agent 引擎层
↓
工具适配层
↓
知识检索层
↓
基础设施层
↓
运维治理层
```

------

### 2.2 设计目标

| 目标     | 说明                                                         |
| -------- | ------------------------------------------------------------ |
| 长期迭代 | 支持功能持续扩展，不因新增工具、新增场景、新增模型导致主链路重构 |
| 生产可用 | 支持权限、审计、监控、日志、异常处理、灰度、限流、降级、扩容 |
| 模块解耦 | Agent、工具、RAG、工单、告警、模型调用互相隔离               |
| 可替换   | LLM、向量库、日志系统、Trace 系统、监控系统均可替换          |
| 可运维   | 每次请求、工具调用、模型调用、Agent 决策都可追踪             |
| 可扩展   | 支持多租户、多服务、多团队、多环境、多数据源                 |
| 可评测   | 支持故障样本集、根因命中率、证据完整率、人工采纳率评估       |
| 可治理   | 支持 Prompt 版本、工具权限、模型路由、知识库版本、策略规则版本治理 |

------

### 2.3 设计原则

#### 2.3.1 单一职责原则

每一层只做自己的事情：

- 接入层只负责请求入口和协议适配
- 应用层只负责编排流程
- 领域层只负责核心业务规则
- Agent 引擎只负责推理、规划、上下文和工具调用调度
- 工具层只负责外部系统访问
- 基础设施层只负责存储、消息、缓存、配置等技术能力

------

#### 2.3.2 高内聚低耦合

模块之间通过接口、事件、DTO、契约通信，不允许跨层直接访问内部实现。

错误示例：

```text
RCAService 直接调用 Prometheus SDK
AgentExecutor 直接查询 MySQL 表
LogTool 直接修改 Incident 状态
Controller 直接操作向量数据库
```

正确做法：

```text
RCAService → MetricsQueryPort → PrometheusMetricsAdapter
AgentExecutor → ToolRegistry → LogQueryTool
ApplicationService → IncidentRepository
RetrievalService → VectorStorePort → MilvusAdapter
```

------

#### 2.3.3 业务稳定，技术可替换

核心业务模型不能依赖具体技术实现。

例如：

- 领域层不依赖 LangChain / LangGraph
- 领域层不依赖 Prometheus / Loki / Jaeger
- 领域层不依赖 OpenAI / Qwen / Claude
- 领域层不依赖 Milvus / Elasticsearch
- 领域层不依赖 Kafka / RabbitMQ

所有外部能力通过接口适配。

------

#### 2.3.4 Agent 可控，不允许自由失控

本项目不采用完全自由的 Autonomous Agent。

生产环境中排障属于高风险场景，因此采用：

```text
固定工作流主流程
+
局部 Agent 推理
+
工具权限控制
+
人工确认
+
审计追踪
```

Agent 可以做：

- 归纳日志模式
- 关联证据
- 生成根因候选
- 生成排障报告
- 推荐处理动作

Agent 不应该直接做：

- 自动回滚
- 自动重启
- 自动扩容
- 自动修改配置
- 自动删除资源
- 自动执行数据库变更

------

#### 2.3.5 所有关键行为必须可观测

任何一次 Agent 排障都必须能回答：

- 谁触发的？
- 哪个租户？
- 哪个服务？
- 哪个告警？
- 调用了哪些工具？
- 每个工具输入是什么？
- 每个工具输出摘要是什么？
- 用了哪个模型？
- 用了哪个 Prompt 版本？
- 检索了哪些知识文档？
- 生成了哪些根因候选？
- 最终建议是什么？
- 人工是否采纳？
- 后续是否创建工单？

------

## 3. 适用场景

本架构适用于以下生产级场景：

### 3.1 微服务故障排查

- 服务 5xx 错误率升高
- P99 延迟升高
- 服务调用超时
- 下游依赖异常
- 接口吞吐下降
- 实例频繁重启

------

### 3.2 日志智能分析

- 自动聚类错误日志
- 提取异常模式
- 关联异常时间窗口
- 生成日志摘要
- 定位错误来源

------

### 3.3 可观测性多信号关联

- Metrics 指标分析
- Logs 日志分析
- Traces 调用链分析
- Deployment 发布记录分析
- K8s 事件分析
- 历史事故案例检索

------

### 3.4 SRE / DevOps 运维辅助

- 告警降噪
- 根因分析
- 故障工单创建
- 处理建议生成
- 复盘报告生成
- Runbook 推荐

------

### 3.5 企业知识沉淀

- 历史事故沉淀
- 排障手册沉淀
- 服务依赖文档沉淀
- 错误码文档沉淀
- 发布规范沉淀
- 处理经验复用

------

## 4. 总体架构图

```text
┌──────────────────────────────────────────────────────────────┐
│                        Client / Ops Portal                    │
│  Web Console / ChatOps / API Client / Alert Webhook / CLI     │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                         Access Layer                         │
│ API Gateway / Auth / Rate Limit / Tenant Resolver / Webhook   │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                       Interface Layer                        │
│ REST API / OpenAPI / WebSocket / Event Consumer / BFF         │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                    Application Orchestration Layer            │
│ Alert App Service / Incident App Service / RCA Workflow       │
│ Ticket App Service / Evaluation App Service / Admin Service   │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                         Domain Layer                         │
│ Alert / Incident / Evidence / RootCause / ToolInvocation      │
│ Runbook / KnowledgeDoc / Ticket / User / Tenant / Policy      │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                         Agent Engine Layer                   │
│ Workflow Executor / Planner / Context Manager / Tool Router   │
│ Prompt Manager / Model Gateway / Guardrail / Memory / Eval    │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                      Tool Integration Layer                  │
│ Metrics Tool / Logs Tool / Trace Tool / Deploy Tool / K8s     │
│ Ticket Tool / Notify Tool / CMDB Tool / SQL Tool / Git Tool   │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                    Knowledge & Retrieval Layer               │
│ Doc Ingestion / Chunking / Embedding / Hybrid Retrieval       │
│ Rerank / Permission Filter / Knowledge Version / Citation     │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                      Infrastructure Layer                    │
│ RDBMS / Redis / MQ / Object Storage / Vector DB / Search      │
│ Config Center / Secret Manager / Scheduler / Lock Service     │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                     Observability & Ops Layer                │
│ Logs / Metrics / Traces / Audit / Alert / Dashboard / CI/CD   │
└──────────────────────────────────────────────────────────────┘
```

------

## 5. 完整分层架构设计

# 5.1 接入层：Access Layer

## 职责

接入层负责所有外部流量进入系统前的统一处理。

主要职责：

- API Gateway
- 鉴权认证
- 租户识别
- 请求限流
- IP 白名单
- 签名校验
- Webhook 接入
- 协议转换
- 请求路由
- 基础安全拦截

------

## 组成模块

```text
access/
├── gateway
├── auth-filter
├── tenant-resolver
├── rate-limiter
├── webhook-adapter
├── request-signature
└── security-filter
```

------

## 接入类型

| 接入类型             | 用途                             |
| -------------------- | -------------------------------- |
| Web Console          | 运维人员查看告警、分析报告、工单 |
| Alertmanager Webhook | 接收 Prometheus / Grafana 告警   |
| ChatOps              | 接入飞书、企业微信、Slack        |
| OpenAPI              | 提供给外部系统调用               |
| CLI                  | 内部排障命令行工具               |
| Scheduler            | 定时任务触发评测、同步、巡检     |

------

## 边界规范

接入层不允许包含业务逻辑。

接入层可以做：

```text
认证
鉴权
限流
租户解析
协议适配
基础参数校验
请求转发
```

接入层不可以做：

```text
根因分析
日志查询
Agent 调度
工单创建
知识检索
数据库业务写入
```

------

# 5.2 接口层：Interface Layer

## 职责

接口层负责对外暴露稳定 API，并将外部请求转换为应用层可理解的命令对象。

主要职责：

- REST API
- WebSocket
- SSE
- Webhook Controller
- Event Consumer
- BFF 聚合接口
- DTO 转换
- 参数校验
- API 版本管理

------

## 典型接口

```text
POST   /api/v1/alerts
GET    /api/v1/incidents/{incidentId}
POST   /api/v1/incidents/{incidentId}/analyze
GET    /api/v1/incidents/{incidentId}/report
POST   /api/v1/incidents/{incidentId}/tickets
GET    /api/v1/agent-traces/{traceId}
POST   /api/v1/knowledge/docs
GET    /api/v1/evaluations/runs/{runId}
```

------

## 接口层目录

```text
interfaces/
├── rest/
│   ├── AlertController
│   ├── IncidentController
│   ├── RCAController
│   ├── TicketController
│   ├── KnowledgeController
│   └── EvaluationController
├── webhook/
│   ├── AlertmanagerWebhookController
│   ├── GrafanaWebhookController
│   └── CustomWebhookController
├── consumer/
│   ├── AlertEventConsumer
│   ├── ToolResultConsumer
│   └── EvaluationTaskConsumer
├── dto/
│   ├── request
│   └── response
└── mapper/
```

------

## 边界规范

接口层只允许调用应用层 Service。

允许：

```text
Controller → ApplicationService
Consumer → ApplicationService
WebhookController → ApplicationService
```

禁止：

```text
Controller → Repository
Controller → PrometheusClient
Controller → LLMClient
Controller → VectorDB
Controller → DomainEntity 内部复杂行为
```

------

# 5.3 应用编排层：Application Orchestration Layer

## 职责

应用层是业务用例入口，负责组织完整业务流程。

它不负责具体技术细节，也不直接访问外部系统。

主要职责：

- 告警接入用例
- 事故创建用例
- RCA 分析用例
- 工单流转用例
- 知识库管理用例
- 评测执行用例
- 用户权限用例
- 后台管理用例

------

## 应用服务划分

```text
application/
├── alert/
│   ├── AlertApplicationService
│   ├── AlertCommandHandler
│   └── AlertQueryService
├── incident/
│   ├── IncidentApplicationService
│   ├── IncidentCommandHandler
│   └── IncidentQueryService
├── rca/
│   ├── RCAApplicationService
│   ├── RCAWorkflowService
│   └── RCAReportService
├── ticket/
│   ├── TicketApplicationService
│   └── TicketSyncService
├── knowledge/
│   ├── KnowledgeApplicationService
│   └── KnowledgeSyncService
├── evaluation/
│   ├── EvaluationApplicationService
│   └── EvaluationRunService
└── admin/
    ├── TenantApplicationService
    ├── PolicyApplicationService
    └── ModelConfigApplicationService
```

------

## 应用层核心用例

### 5.3.1 接收告警

```text
ReceiveAlertCommand
↓
AlertApplicationService.receive()
↓
创建 AlertEvent
↓
根据规则判断是否创建 Incident
↓
发布 IncidentCreatedEvent
```

------

### 5.3.2 启动 RCA 分析

```text
StartRCACommand
↓
RCAApplicationService.start()
↓
校验权限
↓
加载 Incident
↓
创建 RCA Task
↓
调用 AgentWorkflowExecutor
↓
异步执行分析流程
```

------

### 5.3.3 生成事故报告

```text
GenerateReportCommand
↓
RCAReportService.generate()
↓
加载 Evidence
↓
加载 RootCauseCandidate
↓
加载 ToolInvocationTrace
↓
调用 ReportGenerator
↓
保存 RCAReport
```

------

## 边界规范

应用层可以调用：

```text
Domain Service
Repository Interface
Agent Engine Interface
Tool Gateway Interface
Event Publisher
Transaction Manager
Policy Service
```

应用层不可以直接调用：

```text
Prometheus SDK
Loki SDK
Jaeger SDK
LLM API
Milvus Client
Kafka 原生客户端
Redis 原生客户端
```

------

# 5.4 领域层：Domain Layer

## 职责

领域层承载系统最核心、最稳定的业务模型和业务规则。

领域层不依赖任何外部框架、数据库、消息队列、LLM、向量库、监控系统。

------

## 核心领域对象

```text
domain/
├── alert/
│   ├── AlertEvent
│   ├── AlertRule
│   ├── AlertSeverity
│   └── AlertStatus
├── incident/
│   ├── Incident
│   ├── IncidentStatus
│   ├── IncidentTimeline
│   └── IncidentImpact
├── evidence/
│   ├── Evidence
│   ├── EvidenceType
│   ├── EvidenceSource
│   └── EvidenceConfidence
├── rca/
│   ├── RootCauseCandidate
│   ├── RCAReport
│   ├── RCAConclusion
│   └── ConfidenceScore
├── tool/
│   ├── ToolDefinition
│   ├── ToolInvocation
│   ├── ToolPermission
│   └── ToolRiskLevel
├── knowledge/
│   ├── KnowledgeDocument
│   ├── KnowledgeChunk
│   ├── KnowledgeVersion
│   └── Citation
├── workflow/
│   ├── WorkflowDefinition
│   ├── WorkflowRun
│   ├── WorkflowStep
│   └── StepStatus
├── tenant/
│   ├── Tenant
│   ├── Workspace
│   └── Environment
└── policy/
    ├── AccessPolicy
    ├── DataScope
    └── OperationPolicy
```

------

## 核心聚合设计

### Alert 聚合

负责告警生命周期。

```text
AlertEvent
├── alertId
├── tenantId
├── environment
├── serviceName
├── alertName
├── severity
├── startTime
├── endTime
├── status
└── rawPayload
```

------

### Incident 聚合

负责事故生命周期。

```text
Incident
├── incidentId
├── tenantId
├── serviceName
├── severity
├── status
├── ownerTeam
├── impact
├── timeline
├── relatedAlerts
└── relatedReports
```

状态流转：

```text
CREATED
↓
ANALYZING
↓
ANALYSIS_COMPLETED
↓
CONFIRMED
↓
PROCESSING
↓
RESOLVED
↓
CLOSED
```

------

### Evidence 聚合

负责证据管理。

```text
Evidence
├── evidenceId
├── incidentId
├── sourceType
├── sourceSystem
├── timeRange
├── contentSummary
├── rawReference
├── confidence
└── createdAt
```

证据类型：

```text
METRIC
LOG
TRACE
DEPLOYMENT
K8S_EVENT
RUNBOOK
HISTORICAL_INCIDENT
MANUAL_NOTE
```

------

### RootCauseCandidate 聚合

负责根因候选。

```text
RootCauseCandidate
├── candidateId
├── incidentId
├── causeType
├── description
├── evidenceIds
├── confidenceScore
├── supportingEvidence
├── conflictingEvidence
└── status
```

------

### ToolInvocation 聚合

负责工具调用审计。

```text
ToolInvocation
├── invocationId
├── traceId
├── incidentId
├── toolName
├── toolVersion
├── inputSummary
├── outputSummary
├── status
├── latencyMs
├── errorCode
├── riskLevel
└── createdAt
```

------

## 领域层边界规范

领域层允许：

```text
定义实体
定义值对象
定义领域服务
定义领域事件
定义业务状态机
定义业务规则
```

领域层禁止：

```text
调用数据库
调用 Redis
调用 Kafka
调用 LLM
调用 Prometheus
调用 Loki
调用 Jaeger
调用 HTTP Client
读取配置中心
写日志框架细节
```

------

# 5.5 Agent 引擎层：Agent Engine Layer

## 职责

Agent 引擎层负责大模型相关能力的统一封装。

主要职责：

- 工作流执行
- Agent 状态管理
- 工具选择
- 工具调用路由
- 上下文构建
- Prompt 管理
- 模型路由
- Token 控制
- 输出解析
- 安全护栏
- 结果评测
- Agent Trace 记录

------

## 模块结构

```text
agent/
├── workflow/
│   ├── WorkflowExecutor
│   ├── WorkflowDefinitionLoader
│   ├── StepExecutor
│   ├── StepRetryPolicy
│   └── WorkflowStateStore
├── planner/
│   ├── RCAPlanner
│   ├── ToolSelectionPlanner
│   └── EvidencePlanningService
├── context/
│   ├── ContextBuilder
│   ├── ContextCompressor
│   ├── EvidenceContextAssembler
│   └── TokenBudgetManager
├── prompt/
│   ├── PromptTemplate
│   ├── PromptRegistry
│   ├── PromptVersionManager
│   └── PromptRenderer
├── model/
│   ├── ModelGateway
│   ├── ModelRouter
│   ├── ModelProvider
│   ├── ModelFallbackPolicy
│   └── ModelUsageRecorder
├── tool/
│   ├── ToolRegistry
│   ├── ToolRouter
│   ├── ToolExecutor
│   ├── ToolPermissionChecker
│   └── ToolResultNormalizer
├── guardrail/
│   ├── InputGuardrail
│   ├── OutputGuardrail
│   ├── SensitiveDataMasker
│   └── RiskActionBlocker
├── memory/
│   ├── ShortTermMemory
│   ├── IncidentMemory
│   └── TeamKnowledgeMemory
└── evaluator/
    ├── RCAEvaluator
    ├── EvidenceQualityEvaluator
    └── ReportQualityEvaluator
```

------

## Agent 工作流设计

### 主流程

```text
Alert Parsed
↓
Incident Loaded
↓
Build Investigation Plan
↓
Query Metrics
↓
Query Logs
↓
Query Traces
↓
Query Deployments
↓
Retrieve Runbooks
↓
Retrieve Historical Incidents
↓
Generate Evidence Summary
↓
Generate Root Cause Candidates
↓
Validate Evidence
↓
Generate RCA Report
↓
Human Confirmation
↓
Ticket / Notification
```

------

## Agent 与业务系统边界

Agent 引擎只负责智能能力，不直接修改核心业务状态。

正确调用方式：

```text
ApplicationService
↓
AgentWorkflowExecutor
↓
ToolExecutor
↓
Tool Adapter
↓
返回 ToolResult
↓
ApplicationService 保存业务状态
```

错误调用方式：

```text
AgentWorkflowExecutor 直接修改 Incident 表
ToolExecutor 直接创建正式工单
LLM 输出直接触发回滚
```

------

## 模型调用规范

所有模型调用必须经过统一 ModelGateway。

禁止业务代码直接调用模型供应商 API。

```text
业务模块
↓
ModelGateway
↓
ModelRouter
↓
ModelProvider
↓
OpenAI / Qwen / Claude / 本地模型
```

------

## Prompt 版本管理

所有 Prompt 必须版本化。

```text
prompt_key: rca.root_cause_generation
version: v1.3.2
status: active
owner: sre-ai-team
created_at: 2026-06-26
```

Prompt 不允许散落在业务代码中。

Prompt 必须支持：

- 版本号
- 适用场景
- 输入变量
- 输出格式
- 回滚版本
- 灰度版本
- 评测结果绑定
- 审批记录

------

## 工具权限规范

工具分级：

| 风险等级 | 工具类型                       | 是否允许自动执行 |
| -------- | ------------------------------ | ---------------- |
| LOW      | 查询指标、查询日志、查询 Trace | 允许             |
| MEDIUM   | 创建工单、发送通知             | 需要策略判断     |
| HIGH     | 重启服务、回滚版本、扩容实例   | 必须人工确认     |
| CRITICAL | 删除数据、修改数据库、变更权限 | 禁止自动执行     |

------

# 5.6 工具适配层：Tool Integration Layer

## 职责

工具适配层负责对接外部系统，并向 Agent 提供标准化工具接口。

------

## 工具模块结构

```text
tools/
├── metrics/
│   ├── MetricsTool
│   ├── MetricsQueryPort
│   ├── PrometheusMetricsAdapter
│   └── GrafanaMetricsAdapter
├── logs/
│   ├── LogsTool
│   ├── LogsQueryPort
│   ├── LokiLogsAdapter
│   ├── ElasticsearchLogsAdapter
│   └── LogPatternExtractor
├── traces/
│   ├── TraceTool
│   ├── TraceQueryPort
│   ├── JaegerTraceAdapter
│   └── TempoTraceAdapter
├── deployment/
│   ├── DeploymentTool
│   ├── DeploymentQueryPort
│   ├── GitlabDeploymentAdapter
│   ├── GithubDeploymentAdapter
│   └── JenkinsDeploymentAdapter
├── k8s/
│   ├── K8sEventTool
│   ├── K8sResourceTool
│   └── KubernetesAdapter
├── ticket/
│   ├── TicketTool
│   ├── TicketPort
│   ├── JiraTicketAdapter
│   └── InternalTicketAdapter
├── notify/
│   ├── NotificationTool
│   ├── FeishuNotifyAdapter
│   ├── WeComNotifyAdapter
│   └── SlackNotifyAdapter
└── cmdb/
    ├── CMDBTool
    ├── ServiceCatalogAdapter
    └── OwnerResolver
```

------

## 工具接口规范

每个工具必须具备统一结构：

```text
ToolDefinition
├── name
├── version
├── description
├── input_schema
├── output_schema
├── risk_level
├── timeout_ms
├── retry_policy
├── permission_policy
└── owner
```

------

## 工具调用结果规范

所有工具输出必须标准化。

```json
{
  "tool_name": "query_logs",
  "tool_version": "v1",
  "status": "SUCCESS",
  "latency_ms": 842,
  "data": {},
  "summary": "发现 328 条 ERROR 日志，主要模式为 SQLTimeoutException",
  "evidence_refs": [],
  "error": null
}
```

------

## 工具边界规范

工具层可以：

```text
访问 Prometheus
访问 Loki
访问 Elasticsearch
访问 Jaeger
访问 K8s API
访问 GitLab
访问 Jira
访问企业微信
```

工具层不可以：

```text
决定事故状态
决定最终根因
直接修改领域对象
直接绕过权限执行高危操作
把原始大数据量结果直接塞给 LLM
```

------

# 5.7 知识检索层：Knowledge & Retrieval Layer

## 职责

知识检索层负责企业排障知识的接入、解析、索引、检索、权限过滤和版本管理。

------

## 知识类型

```text
历史事故报告
排障手册 Runbook
服务依赖文档
错误码文档
数据库慢查询优化文档
Redis 故障处理文档
Kafka 堆积处理文档
K8s 事件处理文档
发布规范
架构说明文档
接口文档
SOP 文档
```

------

## 知识处理链路

```text
Document Source
↓
Document Ingestion
↓
Parsing
↓
Cleaning
↓
Chunking
↓
Metadata Extraction
↓
Embedding
↓
Indexing
↓
Hybrid Retrieval
↓
Permission Filtering
↓
Reranking
↓
Citation Building
```

------

## 模块结构

```text
retrieval/
├── ingestion/
│   ├── DocumentIngestionService
│   ├── FileIngestionAdapter
│   ├── WikiIngestionAdapter
│   └── GitRepoDocIngestionAdapter
├── parser/
│   ├── MarkdownParser
│   ├── PDFParser
│   ├── HTMLParser
│   └── StructuredDocParser
├── chunking/
│   ├── ChunkingStrategy
│   ├── MarkdownHeadingChunker
│   ├── SemanticChunker
│   └── CodeAwareChunker
├── embedding/
│   ├── EmbeddingService
│   ├── EmbeddingModelRouter
│   └── EmbeddingCache
├── index/
│   ├── VectorIndexService
│   ├── KeywordIndexService
│   └── MetadataIndexService
├── search/
│   ├── HybridRetriever
│   ├── Reranker
│   ├── PermissionFilter
│   └── CitationBuilder
└── version/
    ├── KnowledgeVersionService
    └── KnowledgeSnapshotService
```

------

## 检索策略

生产环境不使用单一向量检索。

推荐使用：

```text
向量检索
+
关键词检索
+
元数据过滤
+
权限过滤
+
重排序
+
引用溯源
```

------

## 元数据规范

每个知识 Chunk 必须包含：

```text
doc_id
chunk_id
tenant_id
workspace_id
service_name
team
environment
doc_type
version
section_path
source_url
created_at
updated_at
access_scope
```

------

## 权限过滤规范

知识检索必须先过滤权限，再进入 LLM 上下文。

禁止：

```text
先检索全部文档
再让 LLM 自己判断能不能看
```

正确方式：

```text
用户身份
↓
租户权限
↓
团队权限
↓
服务权限
↓
文档权限
↓
检索候选
↓
Rerank
↓
进入上下文
```

------

# 5.8 基础设施层：Infrastructure Layer

## 职责

基础设施层负责所有技术型基础能力。

------

## 模块结构

```text
infrastructure/
├── persistence/
│   ├── mysql/
│   ├── postgres/
│   ├── migration/
│   └── repository_impl/
├── cache/
│   ├── redis/
│   └── local_cache/
├── mq/
│   ├── kafka/
│   ├── rabbitmq/
│   └── event_bus/
├── object_storage/
│   ├── s3/
│   └── minio/
├── vector_store/
│   ├── milvus/
│   ├── pgvector/
│   └── elastic_vector/
├── search/
│   ├── elasticsearch/
│   └── opensearch/
├── config/
│   ├── config_center/
│   └── feature_flag/
├── secret/
│   ├── vault/
│   └── kms/
├── scheduler/
│   ├── distributed_scheduler
│   └── cron_jobs
└── lock/
    ├── redis_lock
    └── db_lock
```

------

## 基础设施选型原则

| 能力           | 推荐原则                                 |
| -------------- | ---------------------------------------- |
| 关系型数据库   | 存核心业务状态、事务数据、审计记录       |
| Redis          | 缓存、分布式锁、短期状态、限流计数       |
| MQ             | 异步任务、削峰填谷、事件解耦             |
| 对象存储       | 存原始日志片段、报告附件、知识文档       |
| 向量库         | 存知识库向量索引                         |
| 搜索引擎       | 支持关键词检索、日志检索、混合检索       |
| 配置中心       | 管理环境配置、模型配置、策略配置         |
| Secret Manager | 管理 API Key、数据库密码、Webhook Secret |
| Scheduler      | 定时评测、知识同步、报告生成、数据清理   |

------

# 5.9 运维治理层：Observability & Ops Layer

## 职责

运维治理层负责系统自身的运行可观测、告警、审计、安全和发布治理。

------

## 核心能力

```text
结构化日志
指标监控
分布式追踪
错误告警
审计日志
健康检查
容量监控
成本监控
模型调用监控
工具调用监控
Agent 执行链路回放
```

------

## 观测对象

不仅要观察业务服务，还要观察 Agent 系统自身。

| 观测对象     | 关键指标                        |
| ------------ | ------------------------------- |
| API 服务     | QPS、错误率、P99 延迟           |
| Agent 工作流 | 成功率、失败率、平均执行时间    |
| 工具调用     | 成功率、超时率、平均耗时        |
| 模型调用     | Token 消耗、失败率、延迟、成本  |
| RAG 检索     | 召回耗时、TopK 命中率、空召回率 |
| MQ           | 积压量、消费延迟、失败重试数    |
| 数据库       | 慢查询、连接数、锁等待          |
| 缓存         | 命中率、连接数、内存使用        |
| 工单流转     | 创建成功率、人工采纳率          |

------

## 6. 核心模块依赖关系

## 6.1 依赖方向

依赖方向必须单向。

```text
interfaces
    ↓
application
    ↓
domain
```

Agent、工具、基础设施通过接口被应用层调用。

```text
application
    ↓
ports
    ↓
infrastructure / tools / agent
```

------

## 6.2 禁止反向依赖

禁止：

```text
domain 依赖 application
domain 依赖 infrastructure
domain 依赖 agent
domain 依赖 tools
agent 直接依赖 controller
tools 直接依赖 controller
infrastructure 反向调用 domain 业务行为
```

------

## 6.3 推荐依赖关系

```text
Controller
↓
ApplicationService
↓
DomainService / RepositoryPort / AgentPort / ToolPort
↓
Adapter Implementation
```

------

## 7. 核心调用链路

# 7.1 告警接入链路

```text
Alertmanager Webhook
↓
Gateway 鉴权、限流、签名校验
↓
AlertWebhookController
↓
AlertApplicationService.receiveAlert()
↓
AlertEvent 创建
↓
IncidentPolicy 判断是否创建事故
↓
Incident 创建
↓
发布 IncidentCreatedEvent
↓
异步触发 RCA Workflow
```

------

# 7.2 RCA 分析链路

```text
IncidentCreatedEvent
↓
RCAApplicationService.startRCA()
↓
加载 Incident、Tenant、Policy
↓
AgentWorkflowExecutor.start()
↓
Step 1：查询 Metrics
↓
Step 2：查询 Logs
↓
Step 3：查询 Traces
↓
Step 4：查询 Deployment
↓
Step 5：检索 Runbook
↓
Step 6：检索历史事故
↓
Step 7：生成 Evidence Summary
↓
Step 8：生成 Root Cause Candidates
↓
Step 9：证据校验
↓
Step 10：生成 RCA Report
↓
保存 Evidence、Candidate、Report、Trace
↓
通知负责人
```

------

# 7.3 工具调用链路

```text
Agent Step
↓
ToolRouter
↓
ToolPermissionChecker
↓
ToolExecutor
↓
Tool Adapter
↓
External System
↓
ToolResultNormalizer
↓
ToolInvocationRecorder
↓
EvidenceBuilder
↓
返回 Agent Context
```

------

# 7.4 知识检索链路

```text
RCA Context
↓
RetrievalQueryBuilder
↓
PermissionFilter
↓
HybridRetriever
↓
Vector Search + Keyword Search
↓
Metadata Filter
↓
Reranker
↓
CitationBuilder
↓
KnowledgeContextAssembler
↓
进入 Agent Context
```

------

# 7.5 工单创建链路

```text
RCA Report Generated
↓
人工确认
↓
TicketApplicationService.createTicket()
↓
TicketPolicyChecker
↓
TicketTool
↓
Jira / 内部工单系统
↓
保存 Ticket 映射关系
↓
通知相关负责人
```

------

## 8. 边界规范

# 8.1 Controller 边界

Controller 只负责：

```text
参数接收
参数校验
身份上下文获取
DTO 转换
调用 ApplicationService
返回响应
```

Controller 禁止：

```text
写业务规则
查数据库
调 LLM
调 Prometheus
调向量库
做复杂 if-else 编排
```

------

# 8.2 Application Service 边界

Application Service 负责：

```text
用例编排
事务控制
权限校验
调用领域服务
调用端口接口
发布领域事件
```

Application Service 禁止：

```text
写具体 SQL
写 HTTP 调用细节
写模型 Prompt
写日志聚类算法细节
写具体外部系统 SDK 代码
```

------

# 8.3 Domain 边界

Domain 负责：

```text
业务状态
业务规则
领域行为
领域事件
核心模型
```

Domain 禁止：

```text
依赖框架
依赖数据库
依赖外部系统
依赖 LLM
依赖缓存
```

------

# 8.4 Agent 边界

Agent 负责：

```text
构建上下文
选择工具
调用工具
整理证据
生成候选根因
生成报告
```

Agent 禁止：

```text
绕过权限
直接执行高危动作
直接修改核心业务状态
直接访问数据库表
直接信任 LLM 输出
```

------

# 8.5 Tool 边界

Tool 负责：

```text
访问外部系统
转换查询参数
处理外部系统响应
做结果摘要
返回标准 ToolResult
```

Tool 禁止：

```text
决定业务状态
直接创建领域对象
直接改变事故流转
绕过审计
```

------

## 9. 目录规范

# 9.1 后端目录结构

```text
backend/
├── interfaces/
│   ├── rest/
│   ├── webhook/
│   ├── consumer/
│   ├── dto/
│   └── mapper/
├── application/
│   ├── alert/
│   ├── incident/
│   ├── rca/
│   ├── ticket/
│   ├── knowledge/
│   ├── evaluation/
│   └── admin/
├── domain/
│   ├── alert/
│   ├── incident/
│   ├── evidence/
│   ├── rca/
│   ├── tool/
│   ├── knowledge/
│   ├── workflow/
│   ├── tenant/
│   └── policy/
├── agent/
│   ├── workflow/
│   ├── planner/
│   ├── context/
│   ├── prompt/
│   ├── model/
│   ├── tool/
│   ├── guardrail/
│   ├── memory/
│   └── evaluator/
├── tools/
│   ├── metrics/
│   ├── logs/
│   ├── traces/
│   ├── deployment/
│   ├── k8s/
│   ├── ticket/
│   ├── notify/
│   └── cmdb/
├── retrieval/
│   ├── ingestion/
│   ├── parser/
│   ├── chunking/
│   ├── embedding/
│   ├── index/
│   ├── search/
│   └── version/
├── infrastructure/
│   ├── persistence/
│   ├── cache/
│   ├── mq/
│   ├── object_storage/
│   ├── vector_store/
│   ├── search/
│   ├── config/
│   ├── secret/
│   ├── scheduler/
│   └── lock/
├── common/
│   ├── exception/
│   ├── result/
│   ├── validation/
│   ├── id/
│   ├── time/
│   ├── json/
│   ├── logging/
│   └── security/
└── bootstrap/
    ├── config/
    ├── startup/
    └── dependency_injection/
```

------

# 9.2 前端目录结构

```text
frontend/
├── src/
│   ├── app/
│   ├── pages/
│   │   ├── dashboard/
│   │   ├── alerts/
│   │   ├── incidents/
│   │   ├── rca/
│   │   ├── knowledge/
│   │   ├── evaluations/
│   │   └── admin/
│   ├── components/
│   ├── services/
│   ├── stores/
│   ├── hooks/
│   ├── routes/
│   ├── types/
│   ├── utils/
│   └── assets/
└── tests/
```

------

# 9.3 运维目录结构

```text
ops/
├── docker/
├── k8s/
│   ├── base/
│   ├── overlays/
│   │   ├── dev/
│   │   ├── test/
│   │   ├── staging/
│   │   └── prod/
├── helm/
├── terraform/
├── ansible/
├── monitoring/
│   ├── prometheus/
│   ├── grafana/
│   └── alert-rules/
├── logging/
├── tracing/
├── ci/
├── cd/
└── runbooks/
```

------

# 9.4 文档目录结构

```text
docs/
├── architecture/
├── api/
├── domain/
├── deployment/
├── operations/
├── runbooks/
├── security/
├── evaluation/
├── changelog/
└── adr/
```

ADR 用于记录架构决策。

```text
docs/adr/
├── 0001-use-workflow-based-agent.md
├── 0002-use-hybrid-retrieval.md
├── 0003-tool-risk-level-policy.md
└── 0004-prompt-version-management.md
```

------

## 10. 统一编码规范

# 10.1 命名规范

| 类型                 | 规范                     |
| -------------------- | ------------------------ |
| Controller           | XxxController            |
| Application Service  | XxxApplicationService    |
| Domain Service       | XxxDomainService         |
| Repository Interface | XxxRepository            |
| Repository Impl      | XxxRepositoryImpl        |
| Tool                 | XxxTool                  |
| Adapter              | XxxAdapter               |
| DTO                  | XxxRequest / XxxResponse |
| Command              | XxxCommand               |
| Event                | XxxEvent                 |
| Policy               | XxxPolicy                |
| Prompt               | xxx.xxx.xxx              |

------

# 10.2 代码组织规范

必须遵循：

```text
接口入参使用 Request DTO
应用层使用 Command / Query
领域层使用 Entity / ValueObject
外部调用使用 Port / Adapter
数据库对象使用 PO / DO
返回前端使用 Response DTO
```

禁止：

```text
Controller 直接传数据库实体
领域对象直接暴露给前端
外部系统响应直接进入领域层
业务代码直接拼 Prompt
```

------

# 10.3 异常规范

统一异常结构：

```json
{
  "code": "INCIDENT_NOT_FOUND",
  "message": "Incident not found",
  "trace_id": "trc_xxx",
  "timestamp": "2026-06-26T10:15:00Z",
  "details": {}
}
```

异常分类：

```text
BusinessException        业务异常
ValidationException      参数校验异常
PermissionException      权限异常
ExternalSystemException  外部系统异常
ToolExecutionException   工具执行异常
ModelCallException       模型调用异常
RetrievalException       检索异常
SystemException          系统异常
```

------

# 10.4 日志规范

所有日志必须为结构化日志。

必须包含：

```text
trace_id
request_id
tenant_id
user_id
incident_id
alert_id
workflow_run_id
step_id
tool_name
model_name
latency_ms
status
error_code
```

禁止：

```text
打印明文密钥
打印完整用户敏感数据
打印超大原始日志
打印未经脱敏的外部响应
```

------

# 10.5 API 规范

所有 API 必须版本化：

```text
/api/v1/...
/api/v2/...
```

返回结构统一：

```json
{
  "success": true,
  "data": {},
  "error": null,
  "trace_id": "trc_xxx"
}
```

错误返回统一：

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "PERMISSION_DENIED",
    "message": "Permission denied"
  },
  "trace_id": "trc_xxx"
}
```

------

# 10.6 数据库规范

每张业务表必须包含：

```text
id
tenant_id
created_at
updated_at
created_by
updated_by
deleted
version
```

重要表必须包含：

```text
status
trace_id
environment
```

禁止硬删除核心业务数据，采用逻辑删除。

------

# 10.7 幂等规范

以下场景必须支持幂等：

```text
告警接入
事故创建
RCA 任务启动
工具调用回调
工单创建
通知发送
知识库同步
评测任务执行
```

推荐幂等键：

```text
tenant_id + source_system + external_event_id
tenant_id + incident_id + workflow_type
tenant_id + ticket_external_id
```

------

## 11. 配置规范

# 11.1 配置分层

配置分为：

```text
环境配置
服务配置
租户配置
模型配置
工具配置
工作流配置
Prompt 配置
权限策略配置
降级策略配置
```

------

# 11.2 环境配置

环境必须隔离：

```text
dev
test
staging
prod
```

不同环境的数据库、缓存、消息队列、向量库、模型 Key、Webhook Secret 必须隔离。

------

# 11.3 配置文件结构

```text
config/
├── application.yaml
├── application-dev.yaml
├── application-test.yaml
├── application-staging.yaml
├── application-prod.yaml
├── model-routing.yaml
├── tool-registry.yaml
├── workflow-rca.yaml
├── prompt-registry.yaml
├── feature-flags.yaml
└── degradation-policy.yaml
```

------

# 11.4 Secret 管理

以下内容禁止写入代码仓库：

```text
数据库密码
Redis 密码
模型 API Key
Webhook Secret
OAuth Client Secret
云服务 AccessKey
向量库密码
工单系统 Token
```

必须使用：

```text
环境变量
K8s Secret
Vault
云厂商 KMS
```

------

# 11.5 Feature Flag 规范

关键能力必须支持开关：

```text
是否启用 RCA 自动分析
是否启用某个模型
是否启用某个工具
是否启用自动工单
是否启用某个 Prompt 版本
是否启用某个检索策略
是否启用某个降级策略
```

------

## 12. 数据库设计规范

# 12.1 核心表

```text
tenant
user
role
permission
workspace
service_catalog

alert_event
incident
incident_timeline
evidence
root_cause_candidate
rca_report
workflow_run
workflow_step_run
tool_invocation
model_invocation
prompt_version

knowledge_document
knowledge_chunk
knowledge_version
retrieval_record

ticket
notification_record
evaluation_dataset
evaluation_case
evaluation_run
evaluation_result

audit_log
operation_log
```

------

# 12.2 表关系核心说明

```text
tenant 1 - N workspace
workspace 1 - N service_catalog
service_catalog 1 - N alert_event
alert_event N - 1 incident
incident 1 - N evidence
incident 1 - N root_cause_candidate
incident 1 - N rca_report
incident 1 - N workflow_run
workflow_run 1 - N workflow_step_run
workflow_step_run 1 - N tool_invocation
workflow_step_run 1 - N model_invocation
incident 1 - N ticket
```

------

# 12.3 数据生命周期

| 数据类型     | 保存策略                              |
| ------------ | ------------------------------------- |
| 告警数据     | 长期保存摘要，原始 Payload 可定期归档 |
| 事故数据     | 长期保存                              |
| Evidence     | 长期保存摘要，原始数据引用外部系统    |
| Agent Trace  | 保留 90 天到 180 天，可归档           |
| 模型调用记录 | 保留摘要和成本信息，不保存敏感内容    |
| 原始日志片段 | 短期保存，过期归档                    |
| 知识库版本   | 保留历史版本                          |
| 审计日志     | 按合规要求长期保存                    |

------

## 13. 运维适配设计

# 13.1 日志设计

## 日志类型

```text
访问日志
业务日志
Agent 执行日志
工具调用日志
模型调用日志
检索日志
异常日志
审计日志
安全日志
```

------

## 日志字段

```json
{
  "timestamp": "2026-06-26T10:15:00Z",
  "level": "INFO",
  "service": "devops-agent-api",
  "trace_id": "trc_xxx",
  "tenant_id": "tenant_001",
  "user_id": "user_001",
  "incident_id": "inc_001",
  "workflow_run_id": "wf_001",
  "step_name": "QUERY_LOGS",
  "tool_name": "logs.query",
  "message": "query logs completed",
  "latency_ms": 842,
  "status": "SUCCESS"
}
```

------

# 13.2 监控指标设计

## API 指标

```text
http_requests_total
http_request_duration_seconds
http_request_error_total
http_active_requests
```

------

## Agent 指标

```text
agent_workflow_started_total
agent_workflow_completed_total
agent_workflow_failed_total
agent_workflow_duration_seconds
agent_step_duration_seconds
agent_step_failed_total
```

------

## 工具指标

```text
tool_invocation_total
tool_invocation_failed_total
tool_invocation_timeout_total
tool_invocation_duration_seconds
tool_circuit_breaker_open_total
```

------

## 模型指标

```text
model_invocation_total
model_invocation_failed_total
model_latency_seconds
model_input_tokens_total
model_output_tokens_total
model_cost_total
model_fallback_total
```

------

## RAG 指标

```text
retrieval_query_total
retrieval_empty_result_total
retrieval_latency_seconds
rerank_latency_seconds
knowledge_index_build_total
knowledge_index_build_failed_total
```

------

# 13.3 告警规则设计

必须配置以下告警：

```text
API 错误率过高
API P99 延迟过高
Agent Workflow 失败率过高
工具调用超时率过高
模型调用失败率过高
模型成本异常升高
MQ 消费积压
数据库连接池耗尽
Redis 连接异常
知识库索引构建失败
工单创建失败
通知发送失败
```

------

# 13.4 分布式追踪

每次请求必须生成统一 trace_id。

链路贯穿：

```text
API 请求
↓
ApplicationService
↓
AgentWorkflow
↓
ToolInvocation
↓
ExternalSystem
↓
ModelGateway
↓
RetrievalService
↓
Database
```

------

# 13.5 异常处理

## 异常处理原则

```text
业务异常可预期，返回明确错误码
系统异常不可暴露内部细节
外部系统异常必须记录上下文
工具异常必须支持重试和降级
模型异常必须支持 fallback
所有异常必须带 trace_id
```

------

## 外部系统异常处理

| 异常类型     | 处理方式            |
| ------------ | ------------------- |
| 超时         | 重试 + 熔断         |
| 连接失败     | 快速失败 + 降级     |
| 认证失败     | 告警 + 禁止继续调用 |
| 限流         | 指数退避 + 队列削峰 |
| 数据为空     | 标记证据缺失        |
| 返回格式异常 | 标准化失败结果      |

------

# 13.6 容错设计

必须包含：

```text
超时控制
重试机制
熔断机制
限流机制
隔离机制
幂等机制
降级机制
补偿机制
死信队列
任务恢复
```

------

## 超时规范

| 调用类型   | 建议超时   |
| ---------- | ---------- |
| API 请求   | 3s - 10s   |
| 工具查询   | 5s - 30s   |
| 日志查询   | 10s - 60s  |
| Trace 查询 | 10s - 30s  |
| 模型调用   | 30s - 120s |
| 知识库索引 | 异步任务   |
| 评测任务   | 异步任务   |

------

# 13.7 降级设计

当某些能力不可用时，系统必须降级，而不是整体不可用。

| 不可用模块        | 降级方案                                          |
| ----------------- | ------------------------------------------------- |
| Trace 系统不可用  | 使用 Metrics + Logs + Deployment 生成低置信度报告 |
| 日志系统不可用    | 使用 Metrics + Trace + Runbook 分析               |
| 模型不可用        | 切换备用模型或生成规则报告                        |
| 向量库不可用      | 使用关键词检索                                    |
| 工单系统不可用    | 先保存本地 Ticket Draft，后续补偿同步             |
| 通知系统不可用    | 写入待通知队列，稍后重试                          |
| Prometheus 不可用 | 标记指标证据缺失，不阻塞整体流程                  |

------

# 13.8 横向扩展设计

系统必须支持无状态服务横向扩展。

可横向扩展模块：

```text
API Server
Agent Worker
Tool Worker
Retrieval Worker
Evaluation Worker
Knowledge Index Worker
Notification Worker
```

状态必须放到：

```text
数据库
Redis
MQ
对象存储
Workflow State Store
```

禁止将关键状态保存在单机内存中。

------

# 13.9 高可用设计

生产环境建议：

```text
API 多副本
Worker 多副本
数据库主从或高可用集群
Redis 高可用
MQ 高可用
向量库高可用
搜索引擎集群
对象存储多副本
配置中心高可用
```

------

# 13.10 灾备设计

必须具备：

```text
数据库定期备份
对象存储备份
知识库索引可重建
配置备份
Prompt 版本备份
评测集备份
审计日志归档
关键任务可恢复
```

------

## 14. 权限与安全设计

# 14.1 身份认证

支持：

```text
OAuth2
OIDC
SSO
LDAP
企业微信 / 飞书登录
API Token
Webhook Signature
```

------

# 14.2 权限模型

采用 RBAC + ABAC 混合模型。

RBAC：

```text
系统管理员
租户管理员
SRE
研发负责人
普通研发
只读观察者
```

ABAC：

```text
tenant_id
workspace_id
service_name
environment
team
resource_type
operation_type
risk_level
```

------

# 14.3 数据权限

必须控制：

```text
用户只能看自己租户的数据
用户只能看有权限的服务
用户只能检索有权限的知识文档
用户只能执行被授权的工具
高危工具必须人工确认
```

------

# 14.4 敏感信息处理

必须脱敏：

```text
Token
Password
Secret
手机号
邮箱
身份证
数据库连接串
内部 IP
用户隐私数据
业务敏感字段
```

------

# 14.5 审计日志

以下行为必须审计：

```text
登录
查看事故
启动 RCA
调用高风险工具
创建工单
修改 Prompt
修改模型配置
修改工具配置
修改权限
导入知识库
删除知识文档
导出报告
```

------

## 15. 版本维护与升级方案

# 15.1 API 版本管理

API 必须版本化：

```text
/api/v1
/api/v2
```

升级策略：

```text
新增字段保持兼容
废弃字段保留过渡期
破坏性变更必须开新版本
旧版本设置明确下线时间
API 文档同步更新
```

------

# 15.2 数据库版本管理

必须使用 Migration 工具管理数据库变更。

规范：

```text
每次变更一个 migration 文件
禁止手工改生产库结构
禁止直接删除字段
字段删除必须经过废弃周期
大表变更必须灰度执行
上线前必须验证回滚方案
```

------

# 15.3 Prompt 版本管理

Prompt 版本必须独立于代码发布。

支持：

```text
版本发布
版本回滚
灰度实验
效果评测
人工审批
按租户生效
按场景生效
```

------

# 15.4 模型版本管理

模型配置必须支持：

```text
主模型
备用模型
低成本模型
高质量模型
本地模型
按场景路由
按租户路由
按成本预算路由
```

------

# 15.5 工具版本管理

工具必须版本化：

```text
logs.query.v1
logs.query.v2
metrics.query.v1
trace.query.v1
```

旧工具不可直接删除。

工具升级流程：

```text
新增 v2
灰度部分工作流
对比调用成功率
评估输出差异
切换默认版本
保留 v1 回滚
最终废弃
```

------

# 15.6 知识库版本管理

知识库必须支持：

```text
文档版本
索引版本
快照版本
回滚版本
增量更新
全量重建
权限同步
引用溯源
```

------

# 15.7 工作流版本管理

RCA 工作流必须版本化。

```text
rca_workflow_v1
rca_workflow_v2
rca_workflow_canary
```

工作流升级策略：

```text
新事故使用新版本
历史事故保留原版本回放
灰度租户先试用
评测通过后全量切换
保留回滚能力
```

------

## 16. CI/CD 与发布规范

# 16.1 CI 流程

每次提交必须执行：

```text
代码格式检查
静态扫描
单元测试
集成测试
契约测试
安全扫描
依赖漏洞扫描
镜像构建
镜像扫描
```

------

# 16.2 CD 流程

推荐流程：

```text
构建镜像
↓
推送镜像仓库
↓
部署 dev
↓
自动化测试
↓
部署 test
↓
集成测试
↓
部署 staging
↓
灰度验证
↓
生产发布
↓
监控观察
↓
自动或人工确认完成
```

------

# 16.3 发布策略

支持：

```text
滚动发布
蓝绿发布
金丝雀发布
按租户灰度
按功能开关灰度
按工作流版本灰度
按 Prompt 版本灰度
```

------

# 16.4 回滚策略

必须支持：

```text
应用版本回滚
配置版本回滚
Prompt 版本回滚
工作流版本回滚
模型路由回滚
工具版本回滚
知识库索引回滚
```

------

## 17. 测试体系

# 17.1 测试分层

```text
单元测试
领域模型测试
应用服务测试
接口测试
工具适配器测试
RAG 检索测试
Agent 工作流测试
模型输出解析测试
权限测试
性能测试
故障注入测试
端到端测试
```

------

# 17.2 Agent 专项测试

必须建立故障评测集。

评测维度：

```text
根因 Top-1 命中率
根因 Top-3 命中率
证据完整率
错误证据率
报告可读性
工具调用准确率
无效工具调用率
平均分析耗时
平均 Token 成本
人工采纳率
```

------

# 17.3 契约测试

以下接口必须做契约测试：

```text
Prometheus 查询接口
Loki / ES 日志接口
Jaeger / Tempo Trace 接口
Jira / 工单接口
飞书 / 企业微信通知接口
模型网关接口
向量库接口
Webhook 接入接口
```

------

# 17.4 故障注入测试

必须覆盖：

```text
日志系统超时
Trace 系统不可用
模型接口失败
向量库不可用
MQ 积压
数据库慢查询
工单创建失败
通知失败
外部接口限流
Agent Step 执行失败
Worker 异常退出
```

------

## 18. 团队协作规范

# 18.1 分支规范

推荐：

```text
main        生产稳定分支
release/*   发布分支
feature/*   功能分支
fix/*       修复分支
hotfix/*    紧急修复分支
```

------

# 18.2 提交规范

```text
feat: 新功能
fix: 修复问题
refactor: 重构
docs: 文档
test: 测试
chore: 工程配置
perf: 性能优化
security: 安全修复
```

------

# 18.3 Code Review 规范

必须检查：

```text
是否破坏分层边界
是否绕过权限
是否直接调用外部系统
是否缺少异常处理
是否缺少日志和监控
是否支持幂等
是否影响多租户隔离
是否存在敏感数据泄露
是否有测试覆盖
是否需要补充 ADR
```

------

# 18.4 文档规范

每个重要模块必须包含：

```text
设计文档
接口文档
数据模型
时序图
异常处理说明
部署说明
运维手册
测试说明
变更记录
```

------

## 19. 后期迭代扩展方案

# 19.1 新增工具扩展

新增工具流程：

```text
定义 ToolDefinition
↓
实现 Tool Adapter
↓
注册 ToolRegistry
↓
配置权限和风险等级
↓
编写契约测试
↓
接入工作流
↓
灰度启用
```

------

# 19.2 新增模型扩展

新增模型流程：

```text
实现 ModelProvider
↓
注册 ModelGateway
↓
配置 ModelRouter
↓
配置成本和限流策略
↓
跑评测集
↓
灰度部分租户
↓
正式启用
```

------

# 19.3 新增故障场景扩展

例如新增 Kafka 堆积排障场景：

```text
新增 Kafka Metrics Tool
新增 Kafka Logs Pattern
新增 Kafka Runbook
新增 Kafka RCA Workflow Step
新增评测样本
新增告警规则
新增报告模板
```

------

# 19.4 新增租户扩展

新增租户必须配置：

```text
租户信息
团队信息
服务目录
权限策略
数据源配置
工具权限
模型策略
知识库范围
告警接入规则
成本预算
```

------

# 19.5 新增知识源扩展

新增知识源流程：

```text
实现 Ingestion Adapter
↓
配置 Parser
↓
配置 Chunking Strategy
↓
配置 Metadata Extractor
↓
配置 Permission Mapping
↓
构建索引
↓
评估召回效果
```

------

## 20. 性能优化设计

# 20.1 请求级优化

```text
接口分页
异步任务
批量查询
缓存热点配置
减少同步阻塞
限制单次日志查询范围
限制单次上下文大小
```

------

# 20.2 Agent 级优化

```text
工具并行调用
步骤结果缓存
上下文压缩
Token 预算控制
模型按场景路由
低风险场景使用低成本模型
复杂场景使用高质量模型
失败快速降级
```

------

# 20.3 RAG 优化

```text
离线预构建索引
增量更新索引
Embedding 缓存
混合检索
元数据过滤
Rerank 限制候选数量
权限过滤前置
```

------

# 20.4 数据库优化

```text
核心查询建立索引
大表按租户和时间分区
历史数据归档
读写分离
慢查询监控
避免大事务
避免无条件全表扫描
```

------

# 20.5 Worker 优化

```text
任务队列分级
高优先级事故优先处理
低优先级评测任务错峰执行
Worker 水平扩容
任务超时释放
失败进入死信队列
```

------

## 21. 风险点说明

# 21.1 架构复杂度风险

风险：

```text
生产级架构模块较多，初期开发成本高。
```

应对：

```text
按核心链路优先落地
先实现主流程闭环
再逐步补齐治理能力
模块边界从第一天就保持清晰
```

------

# 21.2 LLM 幻觉风险

风险：

```text
模型可能生成没有证据支持的根因。
```

应对：

```text
根因必须绑定 Evidence
报告必须引用证据来源
无证据输出低置信度
高风险建议需要人工确认
```

------

# 21.3 工具误操作风险

风险：

```text
Agent 可能错误调用高危工具。
```

应对：

```text
工具分风险等级
高危工具禁止自动执行
人工确认
审计日志
权限策略校验
```

------

# 21.4 数据泄露风险

风险：

```text
日志、Trace、知识库可能包含敏感信息。
```

应对：

```text
权限过滤前置
敏感信息脱敏
访问审计
租户隔离
最小权限原则
```

------

# 21.5 外部系统依赖风险

风险：

```text
日志系统、Trace 系统、模型服务、向量库可能不可用。
```

应对：

```text
超时
重试
熔断
降级
备用模型
备用检索方案
任务补偿
```

------

# 21.6 成本失控风险

风险：

```text
模型调用和日志查询成本可能过高。
```

应对：

```text
Token 预算
上下文压缩
模型路由
缓存
限制查询窗口
成本监控
租户预算控制
```

------

# 21.7 知识库污染风险

风险：

```text
过期文档、错误文档可能误导 Agent。
```

应对：

```text
知识版本管理
文档可信度评分
引用溯源
定期评测
人工审核
过期标记
```

------

## 22. 架构优点

```text
分层清晰，职责明确，适合团队长期维护
领域层稳定，不依赖具体技术实现
Agent 能力可替换，可升级，可治理
工具插件化，便于新增外部系统
知识库版本化，支持长期沉淀
支持多租户、多服务、多环境扩展
具备完整日志、监控、审计和 Trace 能力
支持灰度发布、版本回滚和配置治理
支持评测闭环，能够持续优化 Agent 效果
高风险操作受控，符合生产安全要求
```

------

## 23. 架构缺点

```text
初期开发成本高
模块数量多，对团队工程能力要求高
需要较完善的基础设施支持
需要持续维护知识库和评测集
需要处理多系统集成复杂度
LLM 效果仍然依赖评测和治理
生产落地需要严格权限和审计体系
```

------

## 24. 落地注意事项

# 24.1 不要一开始追求全量能力

推荐优先完成：

```text
告警接入
Incident 创建
Metrics 查询
Logs 查询
基础 RCA 工作流
报告生成
Agent Trace
权限基础能力
```

再逐步扩展：

```text
Trace 查询
发布记录
历史事故 RAG
工单系统
评测平台
多租户
高级降级
成本治理
```

------

# 24.2 第一版就要保证架构边界

即使初期功能少，也不能把代码写成 Demo 结构。

必须从第一版就保持：

```text
Controller 不写业务
Application 做编排
Domain 放规则
Tool 做外部适配
Agent 做智能推理
Infrastructure 做技术实现
```

------

# 24.3 Agent 输出不能直接等于业务结论

Agent 输出必须经过：

```text
结构化解析
证据校验
置信度判断
安全检查
人工确认
```

------

# 24.4 知识库必须有权限和版本

不能只做一个简单向量库。

必须支持：

```text
租户过滤
团队过滤
服务过滤
文档版本
Chunk 引用
索引版本
知识过期
```

------

# 24.5 工具必须统一注册和审计

不能让模型随意调用函数。

必须通过：

```text
ToolRegistry
ToolPermissionChecker
ToolExecutor
ToolInvocationRecorder
```

------

# 24.6 运维能力必须和业务功能同步建设

以下能力不能等上线后再补：

```text
结构化日志
trace_id
错误码
指标监控
健康检查
告警规则
审计日志
配置隔离
Secret 管理
```

------

## 25. 推荐实施路线

# 25.1 第一阶段：生产骨架搭建

目标：

```text
建立分层架构
建立基础领域模型
建立告警接入
建立 Incident 生命周期
建立 Agent Workflow 框架
建立 ToolRegistry
建立结构化日志和 Trace
```

交付物：

```text
工程骨架
数据库基础表
API 基础接口
告警接入流程
基础 RCA 工作流
Agent Trace
基础权限
```

------

# 25.2 第二阶段：核心排障闭环

目标：

```text
接入 Metrics
接入 Logs
接入基础 Runbook
生成 RCA 报告
支持人工确认
支持工单草稿
```

交付物：

```text
MetricsTool
LogsTool
Runbook Retrieval
RCA Report
ToolInvocation Trace
基础 Dashboard
```

------

# 25.3 第三阶段：多信号关联

目标：

```text
接入 Trace
接入 Deployment
接入 K8s Event
接入历史事故库
增强根因候选生成
```

交付物：

```text
TraceTool
DeploymentTool
K8sTool
Historical Incident Retrieval
Evidence Scoring
RootCauseCandidate
```

------

# 25.4 第四阶段：企业级治理

目标：

```text
多租户
权限控制
Prompt 版本
模型路由
工具权限
灰度发布
评测平台
成本监控
```

交付物：

```text
Tenant Management
RBAC / ABAC
Prompt Registry
Model Gateway
Evaluation Platform
Cost Dashboard
Feature Flags
```

------

# 25.5 第五阶段：规模化运维

目标：

```text
高可用
横向扩展
任务队列治理
知识库增量更新
自动化评测
灾备恢复
运维 Runbook
```

交付物：

```text
K8s 部署
Helm Chart
HPA
MQ 分级队列
Backup Strategy
Disaster Recovery
Ops Runbook
```

------

## 26. 最终架构总结

本架构的核心不是构建一个简单的日志问答 Demo，而是构建一个可长期演进的企业级 DevOps Agent 平台。

系统通过：

```text
清晰分层
领域建模
工作流约束
工具插件化
RAG 知识治理
统一模型网关
权限与审计
日志与监控
版本化管理
灰度与回滚
评测闭环
```

保证项目能够在正式生产环境中长期维护、持续迭代、稳定运行和横向扩展。

最终形成的能力闭环是：

```text
告警接入
↓
事故建模 
↓
多信号证据采集
↓
历史知识检索
↓
Agent 根因分析
↓
报告生成
↓
人工确认
↓
工单流转
↓
复盘沉淀
↓
评测优化
↓
版本迭代
```

该架构适合作为正式企业级大模型应用项目、Agent 平台项目、DevOps 智能运维项目、SRE 辅助排障项目的长期开发框架。

