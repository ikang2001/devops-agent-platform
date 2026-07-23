现在我们进入 **Step 2：设计整体架构、数据流、方案选型与取舍**。

现在我们先搭整体架构骨架，好比建房先出施工图。不能写完代码再考虑结构，要提前拆分模块定好职责，同时对比可选技术方案，权衡复杂度、性能、线上稳定性再做选择，还要想好模块之间怎么协作对接。

本阶段默认选择：**Python FastAPI + PostgreSQL + SQLAlchemy + 端口接口预留异步调度能力**。暂不写代码，不改文件。

**1. 模块划分**

| 模块                        | 职责                                                         | 禁止做的事                              |
| --------------------------- | ------------------------------------------------------------ | --------------------------------------- |
| `interfaces` 接口层         | FastAPI 路由、请求 DTO、响应 DTO、参数基础校验               | 不写业务规则、不查库、不调 LLM          |
| `application` 应用编排层    | 告警接收、Incident 创建、RCA 启动、事务边界、权限校验        | 不写 SQL、不写外部 SDK、不拼 Prompt     |
| `domain` 领域层             | Alert、Incident、Evidence、ToolInvocation 等核心模型和业务规则 | 不依赖 FastAPI、数据库、LLM、第三方系统 |
| `ports` 端口层              | 定义 Repository、EventBus、ToolRegistry、AgentWorkflow 等抽象接口 | 不绑定具体实现                          |
| `infrastructure` 基础设施层 | 数据库、配置、日志、迁移、ID 生成、时间服务、审计落库        | 不承载业务判断                          |
| `agent` Agent 引擎层        | 工作流执行、上下文管理、模型网关、Prompt 管理、结果结构化    | 不直接改 Incident 状态                  |
| `tools` 工具适配层          | Metrics/Logs/Trace/K8s/Ticket 等工具适配器                   | 不绕过 ToolRegistry 调用外部系统        |
| `observability` 可观测层    | trace_id、结构化日志、指标、审计事件                         | 不参与业务决策                          |

教学重点：我们不是按“文件怎么方便写”拆，而是按“谁负责什么、谁不能越界”拆。边界清楚，后续接 Prometheus、Loki、RAG、工单系统时才不会推倒重来。

**2. 文字版数据流 / 调用链路**

告警接入主链路：

```
Alertmanager / Ops Console / OpenAPI
↓
FastAPI Router
↓
Request DTO 参数校验
↓
AlertApplicationService.receive_alert()
↓
幂等校验 + 租户/服务上下文解析
↓
Alert 领域对象创建
↓
IncidentPolicy 判断是否创建或关联 Incident
↓
IncidentRepository / AlertRepository 落库
↓
发布 IncidentCreatedEvent
↓
WorkflowScheduler 触发 RCA Workflow
↓
返回 alert_id / incident_id / status / trace_id
```

RCA 基础链路：

```
IncidentCreatedEvent
↓
RCAApplicationService.start_rca()
↓
加载 Incident + Policy + Tenant Context
↓
AgentWorkflowExecutor.start()
↓
ToolRegistry 选择工具
↓
ToolPermissionChecker 校验权限
↓
ToolExecutor 调用工具适配器
↓
ToolInvocationRecorder 记录输入输出摘要
↓
Evidence 结构化保存
↓
生成 RootCauseCandidate / RCA Report 草稿
↓
等待人工确认
```

关键点：告警接口不能同步做完整 RCA。线上告警高峰时，如果接口里直接查日志、调模型、生成报告，外部监控系统重试会把服务打穿。所以第一阶段要把“接入”和“分析”解耦。

**3. 关键技术选型对比**

| 技术点    | 可选方案                             | 取舍结论                                                     |
| --------- | ------------------------------------ | ------------------------------------------------------------ |
| Web 框架  | FastAPI / Flask / Django             | 选 FastAPI。异步友好、类型约束清晰、OpenAPI 自动生成，适合 Agent 平台 API。Django 太重，Flask 工程约束弱。 |
| 数据库    | PostgreSQL / SQLite / MySQL          | 选 PostgreSQL。JSONB、索引、事务、审计、多租户扩展更稳。SQLite 只适合本地 Demo。 |
| ORM       | SQLAlchemy / Tortoise / 原生 SQL     | 选 SQLAlchemy 2.x。生产成熟、迁移生态好、能兼顾 ORM 和复杂查询。原生 SQL 可控但协作成本高。 |
| Migration | Alembic / 手写 SQL                   | 选 Alembic。数据库版本必须可追踪、可回滚，生产环境不能手改表。 |
| 异步调度  | 端口接口 + 本地实现 / Celery / Kafka | 第一阶段选“端口接口 + 本地实现”。先守住架构边界，后续可替换 Celery/Kafka。直接上 Celery 会增加部署复杂度。 |
| 日志      | structlog / print / loguru           | 选结构化日志思路，优先 stdlib logging + JSON formatter 或 structlog。禁止 print。 |
| 配置      | pydantic-settings / 写死配置         | 选 pydantic-settings。环境隔离、Secret、默认值、校验都更清楚。 |

**4. 类结构与函数调用关系**

第一阶段建议核心类这样设计：

```
AlertController
↓
AlertApplicationService
├─ AlertRepositoryPort
├─ IncidentRepositoryPort
├─ IdempotencyPort
├─ EventPublisherPort
└─ AuditLogPort

IncidentApplicationService
├─ IncidentRepositoryPort
├─ IncidentPolicy
└─ AuditLogPort

RCAApplicationService
├─ IncidentRepositoryPort
├─ AgentWorkflowPort
├─ EvidenceRepositoryPort
└─ ToolInvocationRepositoryPort

AgentWorkflowExecutor
├─ ToolRegistry
├─ ToolPermissionChecker
├─ ToolExecutor
├─ ModelGatewayPort
└─ WorkflowTraceRecorder
```

领域对象第一阶段只保留必要状态：

```
Alert
- alert_id
- tenant_id
- service_name
- severity
- summary
- starts_at
- source
- fingerprint

Incident
- incident_id
- tenant_id
- service_name
- status
- severity
- title
- created_at
- updated_at

ToolInvocation
- invocation_id
- incident_id
- tool_name
- input_summary
- output_summary
- status
- latency_ms
- error_message
```

注意：这里的设计不是为了“类多显得高级”，而是为了防止 Controller、数据库、Agent、外部工具互相乱调。初级开发最容易把所有逻辑塞进接口函数里，前期快，后期非常难维护。

**5. 稳定性与兼容性前置设计**

参数校验：

- 接口层校验字段类型、必填、长度、时间格式。
- 应用层校验业务语义，比如租户是否存在、服务是否允许接入。
- 领域层校验状态流转，比如 Incident 不能从 `resolved` 回到 `analyzing`。

幂等设计：

- 告警接入使用 `tenant_id + source + fingerprint` 作为幂等键。
- 重复请求返回已有 `alert_id / incident_id`，不重复创建事故。
- 后续可用数据库唯一索引或 Redis 实现。

事务设计：

- Alert 创建和 Incident 创建应在同一事务内完成。
- 发布事件建议使用 Outbox 思路，避免“数据库成功但事件丢失”。
- 第一阶段可先定义端口和表结构，后续再增强 Outbox Worker。

异常处理：

- 全局异常拦截统一返回 `trace_id + error_code + message`。
- 外部系统异常不能泄露内部堆栈。
- 数据库异常要打结构化错误日志，并保留请求上下文。

可观测性：

- 每个请求生成或透传 `trace_id`。
- 每次 Tool 调用记录耗时、状态、输入摘要、输出摘要。
- RCA Workflow 每个 step 都要有状态记录，方便排障回放。

兼容性：

- API 从 `/api/v1` 开始。
- DTO 只新增字段，不随意删除或改语义。
- Tool 命名预留版本，如 `logs.query.v1`、`metrics.query.v1`。
- Prompt、模型、工具、工作流都按版本化思想预留字段。

**Step 2 小结**

这一阶段的核心决策是：先搭 **生产级分层骨架**，不追求一次接完所有外部系统；先定义稳定边界和端口，让数据库、Agent、工具、异步任务都能后续替换。

下一步 **Step 3** 才开始搭建代码骨架：只创建目录、类、接口、方法签名、注释和 `TODO/pass` 占位，不写具体业务实现。