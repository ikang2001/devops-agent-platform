# Step 3 代码骨架搭建计划

## Summary

在 `C:\Users\MSN\Desktop\llm\DevOps 智能排障 Agent 平台` 下创建一个 **FastAPI + SQLAlchemy + PostgreSQL 语义** 的生产级后端骨架。此阶段只做目录、接口、类、方法签名、注释、`TODO/pass` 占位，不实现业务逻辑。

## Key Changes

- 初始化 Python 后端工程结构：
  - `src/devops_agent_platform/interfaces`：API 路由、DTO、异常响应。
  - `src/devops_agent_platform/application`：用例编排服务。
  - `src/devops_agent_platform/domain`：领域实体、枚举、领域规则。
  - `src/devops_agent_platform/ports`：Repository、EventBus、Workflow、Audit 抽象接口。
  - `src/devops_agent_platform/infrastructure`：配置、数据库、日志、实现适配入口。
  - `src/devops_agent_platform/agent`：Agent Workflow 抽象执行器。
  - `src/devops_agent_platform/tools`：ToolRegistry、ToolExecutor、权限校验骨架。
  - `tests`：单元测试骨架。
- 定义首批 API 契约：
  - `POST /api/v1/alerts`：接收外部告警，只返回接收状态、`alert_id`、`incident_id`、`trace_id`。
  - `POST /api/v1/incidents/{incident_id}/rca`：触发 RCA 分析入口骨架。
  - `GET /healthz`：健康检查入口。
- 定义首批核心类型：
  - `Alert`：告警领域对象。
  - `Incident`：事故领域对象。
  - `ToolInvocation`：工具调用记录领域对象。
  - `AlertApplicationService`：告警接收用例编排。
  - `RCAApplicationService`：RCA 启动用例编排。
  - `ToolRegistry` / `ToolExecutor`：后续工具统一注册和审计入口。

## Implementation Skeleton

- 工程入口：

  - `main.py` 只负责创建 FastAPI app、注册路由、注册异常处理器。
  - 不写业务逻辑，不直接访问数据库。

- 接口层骨架：

  - `AlertWebhookRequest` 字段：`tenant_id`、`source`、`service_name`、`severity`、`summary`、`starts_at`、`fingerprint`。
  - `AlertAcceptedResponse` 字段：`alert_id`、`incident_id`、`status`、`trace_id`。
  - Router 只做 DTO 接收、基础参数校验、调用 Application Service。

- 应用层骨架：

  - ```
    AlertApplicationService.receive_alert(command)
    ```

    ：

    - 校验业务上下文。
    - 调用幂等端口。
    - 创建 `Alert` / `Incident`。
    - 调用 RepositoryPort。
    - 发布 `IncidentCreatedEvent`。
    - 全部内部逻辑先 `TODO/pass`。

  - ```
    RCAApplicationService.start_rca(command)
    ```

    ：

    - 加载 Incident。
    - 调用 `AgentWorkflowPort.start()`。
    - 记录审计。
    - 先保留方法签名和注释。

- 领域层骨架：

  - 使用纯 Python dataclass / enum。
  - 不引入 FastAPI、SQLAlchemy、LLM SDK。
  - 状态流转方法只定义签名，例如 `Incident.mark_analyzing()`、`Incident.mark_resolved()`。

- 端口层骨架：

  - 使用 

    ```
    typing.Protocol
    ```

     定义抽象依赖：

    - `AlertRepositoryPort`
    - `IncidentRepositoryPort`
    - `IdempotencyPort`
    - `EventPublisherPort`
    - `AgentWorkflowPort`
    - `AuditLogPort`

  - 所有端口只定义方法签名、参数含义、返回约束。

- 工具层骨架：

  - `ToolDefinition`：工具名、版本、风险等级、超时、权限标签。
  - `ToolRegistry.register()` / `get()` / `list_tools()`。
  - `ToolPermissionChecker.check()`。
  - `ToolExecutor.execute()`。
  - 暂不接 Prometheus、Loki、Jaeger，只预留扩展点。

## Test Plan

- 创建测试骨架但不实现复杂断言：
  - `tests/unit/domain/test_incident.py`：验证 Incident 状态流转入口存在。
  - `tests/unit/application/test_alert_application_service.py`：验证告警接收服务依赖端口可注入。
  - `tests/unit/tools/test_tool_registry.py`：验证工具注册接口结构。
  - `tests/api/test_health.py`：验证健康检查路由规划。
- Step 3 验收标准：
  - 项目目录分层清晰。
  - 所有核心类、接口、方法签名存在。
  - 所有方法有入参、返回值、异常、调用约束注释。
  - 业务实现均为 `TODO/pass`，不提前写逻辑。
  - Controller、Application、Domain、Infrastructure 边界没有混用。

## Assumptions

- 技术栈使用 Python FastAPI。
- 数据库按 PostgreSQL 生产语义设计，但 Step 3 不创建真实表逻辑。
- ORM 预留 SQLAlchemy 2.x，迁移工具预留 Alembic。
- 异步 RCA 调度先通过 `AgentWorkflowPort` / `EventPublisherPort` 抽象，不直接引入 Celery。
- 当前阶段不接真实 LLM、Prometheus、Loki、向量库或工单系统。







# Step 3 生产级代码骨架补强计划

## Summary

你指出得对：原 Step 3 骨架还缺几个生产级后端地基入口。补强后，Step 3 不只创建分层目录和空方法，还要明确 **依赖注入、trace_id、Command 分层、Evidence / WorkflowRun 最小领域模型、工程元文件、Alembic 占位**。本阶段仍只搭骨架和约束，不实现业务逻辑。

## Key Changes

- 新增 `bootstrap` 启动装配层：
  - `bootstrap/app.py`：创建 FastAPI app、注册 middleware、router、exception handler。
  - `bootstrap/dependencies.py`：集中定义依赖注入入口，例如 `get_alert_application_service()`、`get_rca_application_service()`。
  - `main.py` 只调用 `create_app()`，不承载装配细节。
- 明确 `trace_id` middleware：
  - 请求入口读取 `X-Trace-Id`，没有则生成 UUID。
  - 将 `trace_id` 放入 request state 和日志上下文。
  - 响应头统一返回 `X-Trace-Id`。
  - 全局异常响应必须包含 `trace_id`。
- 新增 Command 对象分层：
  - 接口层 DTO 只表达 HTTP 请求/响应。
  - Application Command 表达用例输入，例如 `ReceiveAlertCommand`、`StartRCACommand`。
  - Router 负责 DTO → Command 转换，Application Service 不感知 FastAPI request。
- 补齐最小领域对象：
  - `Alert`：外部告警事实。
  - `Incident`：事故生命周期。
  - `Evidence`：RCA 证据，绑定来源、摘要、可信度、关联 incident。
  - `WorkflowRun`：一次 RCA 工作流执行记录，包含状态、step 计数、开始/结束时间。
  - `ToolInvocation`：工具调用审计记录。
- 补齐工程化文件：
  - `pyproject.toml`：项目元信息、依赖、pytest、ruff 配置。
  - `README.md`：项目定位、分层说明、启动方式占位、开发约束。
  - `.env.example`：数据库、日志、环境、服务名配置样例。
  - `alembic.ini` + `migrations/env.py` + `migrations/versions/.gitkeep`：数据库迁移占位。
  - `tests/`：测试目录骨架。

## Implementation Structure

- 推荐目录结构：
  - `src/devops_agent_platform/bootstrap/`
  - `src/devops_agent_platform/interfaces/http/`
  - `src/devops_agent_platform/application/commands/`
  - `src/devops_agent_platform/application/services/`
  - `src/devops_agent_platform/domain/models/`
  - `src/devops_agent_platform/domain/policies/`
  - `src/devops_agent_platform/ports/`
  - `src/devops_agent_platform/infrastructure/config/`
  - `src/devops_agent_platform/infrastructure/database/`
  - `src/devops_agent_platform/infrastructure/logging/`
  - `src/devops_agent_platform/agent/`
  - `src/devops_agent_platform/tools/`
- `pyproject.toml` 默认依赖：
  - runtime：`fastapi`、`uvicorn[standard]`、`pydantic`、`pydantic-settings`、`sqlalchemy`、`alembic`、`psycopg[binary]`。
  - dev/test：`pytest`、`pytest-asyncio`、`httpx`、`ruff`。
- 依赖注入设计：
  - `dependencies.py` 先返回内存/占位实现或 `TODO` 端口实现。
  - Router 只依赖 Application Service。
  - Application Service 只依赖 Port，不依赖 Infrastructure 具体类。
- Command 设计：
  - `ReceiveAlertCommand` 字段：`tenant_id`、`source`、`service_name`、`severity`、`summary`、`starts_at`、`fingerprint`、`trace_id`。
  - `StartRCACommand` 字段：`incident_id`、`tenant_id`、`operator_id`、`trace_id`。
  - Command 不包含 HTTP header、request、response 等 Web 框架对象。
- 最小 API 契约：
  - `GET /healthz`
  - `POST /api/v1/alerts`
  - `POST /api/v1/incidents/{incident_id}/rca`
  - 所有响应结构预留 `trace_id`。

## Test Plan

- `tests/api/test_health.py`：
  - 验证 `/healthz` 路由存在。
  - 验证响应包含基本健康状态。
- `tests/api/test_trace_id_middleware.py`：
  - 未传 `X-Trace-Id` 时响应头自动生成。
  - 传入 `X-Trace-Id` 时响应头原样透传。
- `tests/unit/application/test_commands.py`：
  - 验证 Router 层 DTO 与 Application Command 分离。
  - 验证 Command 字段不依赖 FastAPI 类型。
- `tests/unit/domain/test_minimal_models.py`：
  - 验证 `Alert`、`Incident`、`Evidence`、`WorkflowRun`、`ToolInvocation` 可被构造。
  - 验证领域模型不导入 FastAPI、SQLAlchemy、LLM SDK。
- Step 3 验收标准：
  - 目录和文件骨架完整。
  - 所有核心类、接口、方法签名有注释和 `TODO/pass`。
  - 依赖注入入口存在。
  - trace_id middleware 设计明确。
  - Command 分层明确。
  - Alembic 与工程元文件占位完整。
  - 不提前实现告警接收、Incident 创建、RCA、工具调用业务逻辑。

## Assumptions

- 继续使用 Python FastAPI 技术栈。
- Python 版本按 `>=3.11` 规划。
- 数据库按 PostgreSQL 生产语义预留，Step 3 不写真实表模型和迁移脚本。
- 本阶段允许创建可运行空壳 API，但业务方法必须保持 `TODO/pass`。
- 第一版依赖注入采用函数式 provider，暂不上复杂 DI 框架。





# Step 3 生产级代码骨架最终计划

## Summary

在 `DevOps 智能排障 Agent 平台` 下搭建 FastAPI 后端工程骨架。本阶段只做 **分层目录、启动装配、DTO/Command/领域对象/端口/异常/中间件/测试骨架**，不实现告警接收、Incident 创建、RCA、工具调用等业务逻辑。

## Key Changes

- 启动与依赖注入：

  - 新增 `bootstrap/app.py` 创建 FastAPI app，统一注册 router、middleware、exception handler。
  - 新增 `bootstrap/dependencies.py`，集中提供 Application Service 依赖。
  - `main.py` 只调用 `create_app()`。

- API 行为约定：

  - `GET /healthz` 真正可用，返回统一响应 envelope。
  - `POST /api/v1/alerts` 和 `POST /api/v1/incidents/{incident_id}/rca` 路由、DTO、Command 转换存在。
  - 业务方法统一抛 `NotImplementedInSkeleton`，全局异常处理成 HTTP 501。
  - 不使用固定假数据，避免误导为业务已完成。

- 统一响应结构：

  ```
  {
    "success": true,
    "data": {},
    "error": null,
    "trace_id": "trc_xxx"
  }
  ```

  错误响应：

  ```
  {
    "success": false,
    "data": null,
    "error": {
      "code": "NOT_IMPLEMENTED",
      "message": "Business logic is not implemented in Step 3"
    },
    "trace_id": "trc_xxx"
  }
  ```

- trace_id middleware：

  - 请求头有 `X-Trace-Id` 则透传。
  - 没有则生成 `trc_` 前缀 UUID。
  - 写入 `request.state.trace_id`。
  - 响应头统一返回 `X-Trace-Id`。
  - 异常响应也必须包含同一个 `trace_id`。

- 分层对象：

  - 接口层 DTO 只表达 HTTP 入参出参。
  - 应用层 Command 表达用例输入：`ReceiveAlertCommand`、`StartRCACommand`。
  - 领域层最小对象：`Alert`、`Incident`、`Evidence`、`WorkflowRun`、`ToolInvocation`。
  - 领域枚举：`AlertSeverity`、`IncidentStatus`、`WorkflowRunStatus`、`ToolInvocationStatus`、`ToolRiskLevel`、`EvidenceType`。

- 异常体系：

  - 新增 `domain/exceptions.py`：`AppException`、`ValidationError`、`PermissionDenied`、`ResourceNotFound`、`ConflictError`、`NotImplementedInSkeleton`。
  - 新增 `interfaces/http/exception_handlers.py`：统一将业务异常映射为 response envelope。

- 端口与工具骨架：

  - 使用 `Protocol` 定义 `AlertRepositoryPort`、`IncidentRepositoryPort`、`EventPublisherPort`、`AuditLogPort`、`AgentWorkflowPort`。
  - 新增 `ToolDefinition`，字段包含 `tool_name`、`version`、`risk_level`、`timeout_ms`、`permission_tags`。
  - 新增 `ToolRegistry`、`ToolPermissionChecker`、`ToolExecutor` 方法签名。

- Stub 装配：

  - 新增 `infrastructure/adapters/stub/`。
  - 包含 `StubAlertRepository`、`StubIncidentRepository`、`StubEventPublisher`、`StubAuditLog`、`StubAgentWorkflow`。
  - Stub 只用于骨架依赖装配，不返回业务假成功数据。

- 工程文件：

  - 新增 `pyproject.toml`、`README.md`、`.env.example`。
  - 新增 Alembic 占位：`alembic.ini`、`migrations/env.py`、`migrations/versions/.gitkeep`。
  - 每层目录添加 `__init__.py`，保持正式项目包结构清晰。

## Test Plan

- `tests/api/test_health.py`：
  - `/healthz` 返回 200。
  - 响应符合统一 envelope。
  - 响应包含 `trace_id`。
- `tests/api/test_trace_id_middleware.py`：
  - 未传 `X-Trace-Id` 时自动生成。
  - 传入 `X-Trace-Id` 时原样透传。
  - 响应头包含 `X-Trace-Id`。
- `tests/api/test_not_implemented_routes.py`：
  - `/api/v1/alerts` 返回 501。
  - `/api/v1/incidents/{incident_id}/rca` 返回 501。
  - 错误响应 code 为 `NOT_IMPLEMENTED`。
- `tests/unit/application/test_commands.py`：
  - Command 不依赖 FastAPI request/response。
  - DTO 到 Command 的转换入口存在。
- `tests/unit/domain/test_minimal_models.py`：
  - `Alert`、`Incident`、`Evidence`、`WorkflowRun`、`ToolInvocation` 可构造。
  - 领域模型不导入 FastAPI、SQLAlchemy、LLM SDK。
- `tests/unit/architecture/test_dependency_boundaries.py`：
  - 占位检查架构边界：domain 不依赖 Web/ORM，application 不直接依赖 infrastructure 具体实现，interfaces 不直接依赖 SQLAlchemy model。
- `tests/unit/tools/test_tool_definition.py`：
  - 验证 `ToolDefinition` 的基本字段和风险等级枚举存在。

## Assumptions

- Python 版本按 `>=3.11`。
- Runtime 依赖使用 FastAPI、Uvicorn、Pydantic、pydantic-settings、SQLAlchemy、Alembic、psycopg。
- Step 3 允许实现健康检查、trace_id middleware、异常 envelope 这些基础设施行为。
- Step 3 不实现任何真实业务成功路径。
- 数据库、外部工具、Agent、LLM、RAG、工单系统只预留接口，不接真实服务。

# Step 3 生产级代码骨架最终修订计划

## Summary

在 `DevOps 智能排障 Agent 平台` 下搭建 FastAPI 后端工程骨架。本阶段只做 **分层目录、启动装配、DTO/Command/领域对象/端口/异常/中间件/测试骨架**。明确边界：`healthz`、`trace_id`、异常 envelope 可用；业务成功路径不可用。

## Key Changes

- 启动与依赖注入：

  - `bootstrap/app.py` 创建 FastAPI app，注册 router、middleware、exception handler。
  - `bootstrap/dependencies.py` 集中提供 Application Service 依赖。
  - `main.py` 只调用 `create_app()`。

- API 行为约定：

  - `GET /healthz` 真正可用，返回统一 response envelope。
  - `POST /api/v1/alerts` 和 `POST /api/v1/incidents/{incident_id}/rca` 只保留路由、DTO、Command 转换。
  - 业务方法统一抛 `NotImplementedInSkeleton`，全局异常处理为 HTTP 501。
  - 不返回固定假业务数据，避免误导为业务已实现。

- 统一响应结构：

  ```
  {
    "success": true,
    "data": {},
    "error": null,
    "trace_id": "trc_xxx"
  }
  ```

  错误响应：

  ```
  {
    "success": false,
    "data": null,
    "error": {
      "code": "NOT_IMPLEMENTED",
      "message": "Business logic is not implemented in Step 3"
    },
    "trace_id": "trc_xxx"
  }
  ```

- trace_id middleware：

  - 请求头有 `X-Trace-Id` 则透传。
  - 没有则生成 `trc_` 前缀 UUID。
  - 写入 `request.state.trace_id`。
  - 响应头统一返回 `X-Trace-Id`。
  - 异常响应包含同一个 `trace_id`。

- 分层对象：

  - 接口层 DTO 只表达 HTTP 入参出参。
  - 应用层 Command 表达用例输入：`ReceiveAlertCommand`、`StartRCACommand`。
  - 领域层最小对象：`Alert`、`Incident`、`Evidence`、`WorkflowRun`、`ToolInvocation`。
  - 领域枚举：`AlertSeverity`、`IncidentStatus`、`WorkflowRunStatus`、`ToolInvocationStatus`、`ToolRiskLevel`、`EvidenceType`。

- 异常体系：

  - `domain/exceptions.py` 定义 `AppException`、`AppValidationError`、`PermissionDenied`、`ResourceNotFound`、`ConflictError`、`NotImplementedInSkeleton`。
  - 避免使用 `ValidationError` 命名，防止和 Pydantic 冲突。
  - `interfaces/http/exception_handlers.py` 统一将异常映射为 response envelope。

- 端口与工具骨架：

  - 使用 `Protocol` 定义 `AlertRepositoryPort`、`IncidentRepositoryPort`、`EventPublisherPort`、`AuditLogPort`、`AgentWorkflowPort`。
  - `ToolDefinition` 字段包含 `tool_name`、`version`、`risk_level`、`timeout_ms`、`permission_tags`。
  - `ToolRegistry`、`ToolPermissionChecker`、`ToolExecutor` 只定义方法签名和调用约束。

- Stub 装配：

  - `infrastructure/adapters/stub/` 包含 `StubAlertRepository`、`StubIncidentRepository`、`StubEventPublisher`、`StubAuditLog`、`StubAgentWorkflow`。
  - Stub 只满足依赖注入和接口存在性，不模拟复杂保存、不维护业务状态、不返回业务成功结果。
  - 所有业务成功路径仍由 Application Service 抛 `NotImplementedInSkeleton` 截断。

- 工程文件：

  - 新增 `pyproject.toml`、`README.md`、`.env.example`。
  - 新增 Alembic 占位：`alembic.ini`、`migrations/env.py`、`migrations/versions/.gitkeep`。
  - 每层目录添加 `__init__.py`。

## Test Plan

- `tests/api/test_health.py`：
  - `/healthz` 返回 200。
  - 响应符合统一 envelope。
  - 响应包含 `trace_id`。
- `tests/api/test_trace_id_middleware.py`：
  - 未传 `X-Trace-Id` 时自动生成。
  - 传入 `X-Trace-Id` 时原样透传。
  - 响应头包含 `X-Trace-Id`。
- `tests/api/test_not_implemented_routes.py`：
  - `/api/v1/alerts` 返回 501。
  - `/api/v1/incidents/{incident_id}/rca` 返回 501。
  - 错误响应 code 为 `NOT_IMPLEMENTED`。
- `tests/unit/application/test_commands.py`：
  - Command 不依赖 FastAPI request/response。
  - DTO 到 Command 的转换入口存在。
- `tests/unit/domain/test_minimal_models.py`：
  - `Alert`、`Incident`、`Evidence`、`WorkflowRun`、`ToolInvocation` 可构造。
  - 领域模型不导入 FastAPI、SQLAlchemy、LLM SDK。
- `tests/unit/architecture/test_dependency_boundaries.py`：
  - 占位检查架构边界：domain 不依赖 Web/ORM，application 不直接依赖 infrastructure 具体实现，interfaces 不直接依赖 SQLAlchemy model。
- `tests/unit/tools/test_tool_definition.py`：
  - 验证 `ToolDefinition` 的基本字段和风险等级枚举存在。

## Assumptions

- Python 版本按 `>=3.11`。
- Runtime 依赖使用 FastAPI、Uvicorn、Pydantic、pydantic-settings、SQLAlchemy、Alembic、psycopg。
- Step 3 允许实现健康检查、trace_id middleware、异常 envelope 这些基础设施行为。
- Step 3 不实现任何真实业务成功路径。
- 数据库、外部工具、Agent、LLM、RAG、工单系统只预留接口，不接真实服务。