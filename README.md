# DevOps 智能排障 Agent 平台

> 生产导向的 Incident / 受控 RCA 作业编排平台。
> 它不是自由式自主 Agent，也不是通用自动修复引擎。

平台把告警接入、Incident 管理、只读证据采集、RCA 报告、人工反馈、工单草稿和审批式修复计划串成一条可审计链路。核心工程能力包括 Outbox、幂等、租约、Owner/Attempt Fence、审批门禁、低基数监控和敏感信息脱敏。

RCA 支持服务端发布的固定只读计划，以及有界动态调查策略：

```text
Metrics → Change → Logs → Traces → Runbooks → Evidence → RCA Report
```

当 `rca_investigation_policy=bounded_dynamic_v1` 时，Runtime 进入
`BoundedDynamicInvestigator`。Planner 只能提交调查意图，后端负责工具白名单、权限、
只读风险、拓扑服务范围、预算、超时、checkpoint/resume 和 Evidence 生成；它不是自由式
ReAct，也不接受 SQL、PromQL、LogQL、TraceQL、Shell 或 Kubernetes 命令。

LLM 只负责生成供人工复核的结构化候选报告，不能把根因标记为 `CONFIRMED`。告警接入也不会自动启动 RCA，必须由授权操作员显式触发。

## 快速导航

| 内容 | 入口 |
|---|---|
| 验收边界与禁止话术 | [诚实边界说明](docs/route-a-honest-narrative.md) |
| Step 4 验收 | [STEP4_ACCEPTANCE.md](STEP4_ACCEPTANCE.md) |
| Step 5 部署与生产化资产 | [STEP5_PRODUCTION.md](STEP5_PRODUCTION.md) |
| Step 6 产品化边界 | [STEP6_PRODUCTIZATION.md](STEP6_PRODUCTIZATION.md) |
| 最小改造总计划 | [docs/remediation-roadmap-master-plan.md](docs/remediation-roadmap-master-plan.md) |
| MiniShop 端到端 RCA 导读 | [docs/minishop-e2e-rca-code-tour.md](docs/minishop-e2e-rca-code-tour.md) |
| Change Event 到 RCA 闭环 | [docs/change-event-rca-loop.md](docs/change-event-rca-loop.md) |
| Benchmark 基础评分闭环 | [docs/evaluation-benchmark-foundation.md](docs/evaluation-benchmark-foundation.md) |
| 真实 Provider Benchmark 与消融 | [ops/evaluation/REAL_PROVIDER.md](ops/evaluation/REAL_PROVIDER.md) |
| 生产级本地仿真验收 | [ops/simulation/README.md](ops/simulation/README.md) |
| Reference staging 验收 | [ops/reference-staging/README.md](ops/reference-staging/README.md) |
| AIOps 项目改造任务书 | [DevOps_Agent_AIOps_项目完善改造任务书.md](DevOps_Agent_AIOps_项目完善改造任务书.md) |
| Benchmark 实施规范 | [DevOps_Agent_Evaluation_Benchmark_实施规范.md](DevOps_Agent_Evaluation_Benchmark_实施规范.md) |
| 云智实习场景最终完善与简历口径 | [云智实习场景_AIOps项目最终完善与简历包装任务书.md](云智实习场景_AIOps项目最终完善与简历包装任务书.md) |
| 项目面试与简历讲解 | [项目面试文档.md](项目面试文档.md) |
| 当前完成度与外部验收边界 | [缺少内容.md](缺少内容.md) |
| 部署说明 | [ops/deploy/README.md](ops/deploy/README.md) |
| 运维 Runbook | [ops/runbooks](ops/runbooks) |

## 项目定位与诚实边界

面试、设计评审和项目介绍建议使用下面这句话：

> 这是一个生产导向的 Incident / RCA 作业编排后端：通过固定只读调查计划收集可追溯 Evidence，使用 Outbox、租约、幂等和审批机制保证关键流程可靠，并支持可选 LLM 报告封装和人工审核修复计划。

可以说明平台已经具备：

- 固定、受权限控制的只读 RCA 调查计划；
- 可选的部分采集降级和置信度护栏；
- PostgreSQL、Kafka、Prometheus、Loki、Tempo 的端口与适配器；
- 可启动的 reference staging 全栈（PostgreSQL、Redpanda、OIDC/JWKS、Prometheus、Loki、Tempo、OpenAI-compatible Provider、Ticketing、MCP），并有 11/11 协议验收报告；
- 工单提交、人工反馈、审批式修复计划和 MiniShop 演练闭环；
- HTTP MCP 适配器、租户 Workspace 管理 API，以及双审核人在线数据集发布状态机；
- 可本地验证的测试、迁移、镜像、SBOM 和安全扫描门禁。

不能宣称：

- 已承载真实生产流量；
- Agent 会自由选择工具、自由执行命令或自动修复任意生产故障；
- Prompt Registry、Feature Flag、向量 RAG 或反馈自动学习闭环已经在线自动运行；
- 平台能够自动接管、重试任意外部写操作；
- 本地 Compose、Mock 或单元测试等同于 staging / production 签字。

仓库中的 Prompt Registry、Feature Flag 资产仍属于治理蓝图；Historical Knowledge 已通过
受控 `knowledge.search@v1` 接入有界动态调查，但它只作为历史参考，不会单独把当前事故升级为 `CONFIRMED`。

### Example Or Blueprint Only / Not Loaded By Runtime

`ops/product` 中的 Prompt Registry、Feature Flag、组织级 RAG 和部分自动策展文件是治理示例，
不会被 `src/` 运行时自动加载；运行时只使用已注册的 `knowledge.search@v1` 和显式配置的策略。

## 当前完成状态

本地工程交付已经完成：代码、迁移、测试、Benchmark、Reference staging、CI 和 v0.5.0 发布链路均已落地。下表把“本地可以证明的交付结果”和“必须由目标环境提供的外部证据”分开，外部证据不是本地代码缺陷，也不会被本地 Mock 或合成数据冒充。

| 阶段 | 本地交付结果 | 外部环境验收前置条件 |
|---|---|---|
| Step 4 工程主链路 | 已完成 Incident、Change Event、Outbox、RCA、Evidence、反馈和修复计划闭环；Reference staging 11/11 协议验收通过 | 若要声明生产可用，需要真实外部 staging/production 签字和商业 LLM 准确率 |
| Dynamic RCA 与告警聚合 | `bounded_dynamic_v1` 已进入 Runtime；Topology/Knowledge Evidence 已纳入动态调查；AlertCorrelationService 已接入跨服务候选与 Alert Storm E2E | 真实观测栈、真实 LLM 和目标环境故障注入仍需外部验收 |
| Step 5 生产化资产 | 已完成 Docker、Compose、Kubernetes 骨架、CI、安全门禁、100/500/1000 alerts/min 合成压测和四类 Chaos 证据 | 目标环境容量、备份恢复、恢复时间和生产故障注入签字 |
| Step 6 产品面 | 已完成 Ops Console、反馈 API、Workspace API、HTTP MCP、离线策展和在线双审核人发布状态机 | 自动匿名化、真实 Web 产品、运行时 Prompt/Flag/RAG 和组织级发布流程需在目标环境接线 |

## Reference staging 实测资产（2026-08-18）

下面的数字来自本机 Docker Compose 全栈，全部是 `synthetic/reference` 证据，不能当作生产签字：

- 协议验收：`ops/reference-staging/artifacts/reference-20260818-r7/results.json`，11/11 通过；新增 Dataset Release 创建、幂等、双审核、发布后不可变和禁止创建人自审检查；
- Real-LLM-compatible Runner：`ops/evaluation/artifacts/minishop-v2-reference-live-20260818`，12 场景 × 5 次共 60 次，使用故意保守的 reference Provider，0/60 通过，平均 112 tokens、平均成本 0.0000512，不能解释为模型准确率；
- 到达率压测：`ops/load/artifacts/reference-20260818-r2/load-report.json`，100/500/1000 alerts/min 实测到达率约 99.883/499.556/997.892，P95 约 68.177/25.596/24.196 ms，错误率 0，使用 Python fallback，不是 k6 生产容量报告；
- 故障注入：`ops/chaos/artifacts/reference-20260818/chaos-report.json`，Worker/Kafka/PostgreSQL/Loki 四场景通过，Worker fence 拒绝 1 次；报告明确 `synthetic=true`、`production_acceptance=false`。

启动、验收和数据边界见 [reference staging README](ops/reference-staging/README.md)。

## 系统架构

```mermaid
%%{init: {'flowchart': {'nodeSpacing': 28, 'rankSpacing': 42, 'padding': 36}, 'themeVariables': {'fontSize': '14px'}}}%%
flowchart TB
    A[Alertmanager / Webhook] --> B[FastAPI 接入层]
    A2[CI/CD / Change Source] --> B2[Change Event HMAC 接入]
    B --> C[(PostgreSQL)]
    B2 --> C
    B --> D[Transactional Outbox]
    B2 --> D
    D --> E[Kafka / Redpanda]
    E --> F[RCA Consumer]
    F --> G[受控 Agent Workflow]
    G --> H[Prometheus]
    G --> H2[Change Event Store]
    G --> I[Loki]
    G --> J[Tempo]
    G --> K[Topology]
    G --> K2[Historical Knowledge]
    G --> K3[Runbook Catalog]
    H --> L[Evidence]
    H2 --> L
    I --> L
    J --> L
    K --> L
    K2 --> L
    K3 --> L
    L --> M[RCA Report]
    M --> N[人工反馈 / 工单草稿 / 修复计划]
    N --> O[审批与受控执行]
```

代码按职责分层：

- `interfaces`：HTTP DTO、路由、中间件和异常响应；
- `application`：用例命令、查询和业务编排服务；
- `domain`：纯领域模型、枚举、状态机和领域异常；
- `ports`：应用层依赖的出站契约；
- `infrastructure`：数据库、Kafka、OIDC、观测、LLM、工单和修复适配器；
- `agent`：固定调查计划、有界动态调查、工作流执行和报告生成；
- `tools`：工具定义、版本注册、权限、风险和只读执行门禁；
- `ops`：部署、监控、Runbook、评测、MiniShop E2E 和修复沙箱资产。

## 核心业务流程

### 1. 告警到 Incident

1. Alertmanager 通过 `POST /api/v1/alerts` 发送告警。
2. 生产路径要求 HMAC-SHA256 Webhook 鉴权和时间窗口校验。
3. `external_event_id` 提供重试幂等；摘要在入库前完成归一化和脱敏。
4. PostgreSQL 事务内完成告警持久化、Incident 关联和 Outbox 事件写入。

### 2. Incident 到 RCA

1. 具有 `incidents:rca` 权限的操作员显式创建 RCA。
2. 事务内把 Incident 置为分析中、创建 `WorkflowRun` 并写入 `rca.requested` Outbox。
3. Kafka Consumer 领取任务；执行 Claim 带 Worker、Attempt 和过期租约。
4. Agent 按固定计划或有界动态意图调用只读工具，并将有界、脱敏后的结果保存为 Evidence。
5. 工作流完成使用 Worker/Attempt Fence，迟到结果不能覆盖新接管者。
6. 报告使用确定性生成器或可选 LLM，最终仍受 Evidence 引用和结论状态约束。

### 3. RCA 后续操作

- 人工反馈：持久化、审计和只读评测候选导出，不会自动训练或发布模型；
- 工单草稿：从 Incident 和 RCA 报告派生，审批后通过 Outbox 异步请求外部工单；
- 修复计划：先校验证据、动作目录和维护窗口，再执行人工审批；
- 外部修复：只允许预注册动作键和固定控制器地址，不接受任意 Shell、SSH、Kubernetes 或云命令。

## 可靠性与安全机制

### 工作流可靠性

- Transactional Outbox 避免数据库提交成功但消息丢失；
- 幂等键、请求指纹和 ETag 防止重复写和并发覆盖；
- RCA、Outbox、审计清理和 Remediation Worker 都有有界批次与协作式停止；
- RCA 执行使用 Claim、Lease、Heartbeat 和 Owner/Attempt Fence；
- Remediation 执行与回滚使用独立 Attempt 和 Lease，过期任务只收口失败，不自动重放外部写；
- Kafka 使用手工提交、可重试回退和 Dead Letter 先写后提交语义。

### 输入与隐私边界

- 租户、Incident、Workflow、操作员和 Worker 标识禁止空白与 ASCII 控制字符；
- `Idempotency-Key`、OIDC Claim、Kafka 元数据和外部 URL 均在进入业务层前校验；
- 日志、Evidence、报告、工单和故障原因在存储或输出前重复脱敏；
- 默认 API 不返回原始 Evidence Payload、请求指纹、幂等哈希或第三方原始错误；
- 指标标签保持低基数，不使用租户、Trace、Workflow、Ticket 或分区 ID。

### 权限与认证

- 管理接口使用可插拔 Bearer Authenticator；未配置时默认失败关闭；
- OIDC 采用非对称 JWT 校验，检查 issuer、audience、时间声明和 JWKS；
- 工具权限按租户和操作员校验，授权来源不可用与明确拒绝使用不同错误语义；
- Webhook 生产路径使用 HMAC-SHA256，固定演练 Token 在 `prod` / `production` 环境会被拒绝。

## RCA 调查策略

部署可以选择三种服务端固定计划，或启用一套有界动态调查：

| 策略 | 调查步骤 | 适用环境 |
|---|---|---|
| `fixed_default` | Metrics → Change → Logs → Traces → Runbooks | Prometheus、Change Store、Loki、Tempo 均可用 |
| `fixed_no_traces` | Metrics → Change → Logs → Runbooks | 没有 Tempo |
| `fixed_metrics_logs_runbooks` | Metrics → Logs → Runbooks | 显式强调无 Change/Trace 的基线流程 |
| `bounded_dynamic_v1` | Planner 按证据选择 Metrics/Logs/Change/Traces/Topology/Knowledge/Runbook | 需要严格预算、工具权限和拓扑边界 |

固定策略是部署时选择的静态计划；`bounded_dynamic_v1` 是服务端有界的规则优先动态调查，
不是自由 ReAct。Topology 和 Knowledge 调查结果都会生成带来源的 Evidence。

`continue_on_step_failure` 默认关闭。显式开启后，只读步骤失败可以继续采集其他证据，但仍满足以下护栏：

- 零 Evidence 时整体失败，不生成假报告；
- 报告摘要列出失败步骤；
- 部分报告置信度最高为 `0.4`；
- 结论不能为 `CONFIRMED`。

## LLM 报告能力

LLM 报告默认关闭。开启后可以配置有序 Provider 链，推荐 OpenAI → DashScope → 确定性生成器。缺少凭据的 Provider 会跳过，超时、拒绝、非法结构、伪造 Evidence 引用或敏感输出会进入下一个 Provider，全部失败后回退到 `UNDETERMINED` 确定性报告。

示例配置：

```text
DEVOPS_AGENT_LLM_REPORT_ENABLED=true
DEVOPS_AGENT_RCA_CONSUMER_ENABLED=true
DEVOPS_AGENT_LLM_PROVIDER_ORDER=openai,dashscope

DEVOPS_AGENT_LLM_OPENAI_API_STYLE=responses
DEVOPS_AGENT_LLM_OPENAI_MODEL=approved-openai-model
DEVOPS_AGENT_LLM_OPENAI_API_KEY=inject-from-secret-manager

DEVOPS_AGENT_LLM_DASHSCOPE_API_STYLE=chat_completions
DEVOPS_AGENT_LLM_DASHSCOPE_MODEL=qwen3.7-plus
DEVOPS_AGENT_LLM_DASHSCOPE_API_KEY=inject-from-secret-manager
```

支持的 Provider 名称为 `openai`、`dashscope`、`openai_compatible` 和 `custom`。旧版单 Provider 环境变量仍保留兼容，但新部署建议使用有序 Provider 配置。

## 云原生微服务故障演练与评测环境（MiniShop 源码）

仓库内置一套云原生微服务故障演练与评测环境（源码目录保留 `MiniShop` 名称），用于跑通本地告警到 RCA 路径：

```text
云原生微服务演练环境 → Prometheus / Loki / Tempo → Alertmanager → 平台 → Kafka → RCA → Ground Truth 评测
```

四个机器可读场景覆盖典型的下单故障，并通过 Manifest 与 Ground Truth 校验 RCA 报告：
`checkout-latency`、`inventory-db-timeout`、`payment-error` 和
`deployment-regression`。发布回归场景要求 Change、Metric、Log、Trace 共同归因；
其他场景允许 Change 查询为空，不能因为历史部署记录单独改变根因。

在 PowerShell 中执行：

```powershell
.\ops\minishop-e2e\run-e2e.ps1
```

Runner 会重建隔离 Compose 栈、发布演练 Runbook、注入故障、等待 Incident/RCA、评测结果并写入：

```text
ops/minishop-e2e/artifacts/results.json
```

仅在排查容器现场时使用 `-KeepStack`。演练固定管理员 Token 默认关闭、与 OIDC 互斥，并且不是生产认证方案。

### Benchmark 基础评分闭环

该云原生微服务故障演练与评测环境的 v2 版本现有 12 个 Manifest 均定义了根因类型、Evidence 类型、有向因果边、影响服务
以及期望/禁止工具。独立 Scorer 可以对结构化 Prediction 计算 RCA、Evidence、Claim、因果链、
影响面和 Tool 指标，并生成 `results.json` 与中文 `evaluation-report.md`。

运行 deterministic contract fixture：

```powershell
uv run python -m ops.evaluation.run_benchmark `
  --scenarios '.\MiniShop 电商下单故障演练靶场\scenarios' `
  --input '.\ops\evaluation\fixtures\minishop-v1-predictions.json' `
  --output '.\ops\evaluation\artifacts\contract-fixture-001'
```

该 fixture 只验证评分合同，不是实时 E2E 或真实 LLM 准确率。完整设计、指标和未实现
边界见 [Benchmark 基础评分闭环](docs/evaluation-benchmark-foundation.md)。

### 真实 Provider Benchmark 与消融

真实模型运行必须使用非 synthetic 输入、有效凭据和独立输出目录。Runner 提供
`--require-real` 门禁，要求完整 12 场景；默认每场景 5 次，即 60 次运行：

```powershell
$env:DEVOPS_AGENT_BENCHMARK_API_KEY = '<从密钥管理器注入>'
uv run python -m ops.evaluation.run_live_benchmark `
  --scenarios '.\MiniShop 电商下单故障演练靶场\scenarios' `
  --input '.\ops\evaluation\artifacts\real-input.json' `
  --output '.\ops\evaluation\artifacts\real-v1-<timestamp>' `
  --git-commit (git rev-parse --short HEAD) `
  --provider dashscope --model qwen3.5-plus `
  --base-url 'https://dashscope.aliyuncs.com/compatible-mode/v1' `
  --api-key-env DEVOPS_AGENT_BENCHMARK_API_KEY `
  --input-cost-per-million 0.0 --output-cost-per-million 0.0 `
  --runs-per-scenario 5 --require-real
```

五变体消融入口为 `python -m ops.evaluation.run_real_ablation`，变体名称固定为
`baseline/change/topology/knowledge/dynamic`。它会拒绝 synthetic/reference 结果，校验
每个变体 12×5，并对 RCA、Evidence、Unsupported Claim、Tool、Token、Cost、P50/P95
延迟执行 Dynamic 门禁。没有真实凭据时只能运行 contract/reference，不得把结果写成真实模型效果。

### 生产级本地仿真验收

`ops/simulation` 复用真实 PostgreSQL、Redpanda、OIDC、Prometheus、Loki、Tempo 和平台 Worker，
增加持久化 Ticketing 仿真服务，并直接调用项目根 `.env` 中的千问 OpenAI-compatible API；不部署本地 Ollama/vLLM 模型。
它可以执行本地 12×5、成本/延迟、容量、Chaos 和恢复测试；所有结果明确标记
`simulation=true`、`production_acceptance=false`，不会与真实 Provider 结果混淆。
2026-08-19 已用真实千问 `qwen3.7-plus` 完成五变体各 12×5（共 300 次）消融，Dynamic 门禁 PASS；
指标与证据边界见 [`docs/local-high-fidelity-simulation-acceptance.md`](docs/local-high-fidelity-simulation-acceptance.md)。

## 本地开发

### 环境要求

- Python 3.11 或 3.12；
- `uv==0.11.31`；
- 需要运行完整演练时安装 Docker Desktop 或 Podman。

安装锁定的包管理器并同步依赖：

```powershell
python -m pip install uv==0.11.31
uv sync --locked --extra dev
```

运行平台测试：

```powershell
uv run python -m pytest -q -m "not live"
uv run ruff check .
```

运行 MiniShop 测试：

```powershell
Set-Location '.\MiniShop 电商下单故障演练靶场'
uv sync --locked --extra dev
uv run python -m pytest -q
```

启动本地 API：

```powershell
uv run uvicorn main:app --reload
```

执行数据库迁移：

```powershell
uv run alembic upgrade head
```

`uv.lock` 是平台可复现依赖基线，MiniShop 拥有独立锁文件。修改依赖约束后使用 `.\scripts\update-lockfiles.ps1` 更新锁文件；只有明确升级依赖时才传入 `-Upgrade`。CI 使用 `uv sync --locked` 拒绝过期锁文件。

## 发布与供应链门禁

`pyproject.toml` 是平台版本号唯一真相源。安装包中的 `devops_agent_platform.__version__`、FastAPI/OpenAPI、根 `uv.lock` 和 Kubernetes 示例镜像 Tag 都由测试约束为同一版本。

发布 Annotated Tag 前执行：

```powershell
uv build --out-dir dist
uv run python scripts/check-release-version.py `
  --tag v0.5.0 --dist-dir dist
```

Tag 流水线依次执行：

1. Python 3.12 主测试、Ruff、MiniShop 测试和 Alembic 离线迁移；
2. Python 3.11 兼容性测试和 Alembic 离线迁移；
3. 平台与 MiniShop 锁定生产依赖的 `pip-audit --strict` 审计；
4. Trivy 仓库密钥扫描；
5. 非 Root 镜像构建、SPDX SBOM 生成和高危漏洞门禁；
6. 发布 wheel、sdist、`sbom.spdx.json` 和覆盖全部载荷的 `SHA256SUMS`。

发布器支持安全重试：远端同名同内容资产直接保留，缺失资产补传，同名不同内容立即失败。只有最终 Release Job 拥有 `contents: write`，前置 Job 全部只读。所有第三方 GitHub Action 均固定到经过审查的完整 40 位 Commit SHA。

## v0.3.4 相比 v0.3.3 的完善

`v0.3.4` 不改变业务 API 或 Remediation 状态机语义，重点完善发布兼容性和供应链证据：

- 新增 Python 3.11 独立兼容性 Job，覆盖非 Live 全量测试和 Alembic 离线迁移；
- 新增安全 Job，分别审计平台与 MiniShop 的锁定生产依赖；
- 将 `cryptography` 锁定版本升级到 `50.0.0`，修复 `PYSEC-2026-3552`；
- 将 `uv` 与其构建依赖隔离在 Builder 环境，避免 `msgpack`、`setuptools` 进入运行时镜像；
- Runtime 构建阶段升级 Debian 安全补丁并清理 APT 索引，修复基础镜像中可升级的高危项；
- 新增 Trivy 文件系统密钥扫描，并把安全门禁加入镜像构建前置依赖；
- 镜像 SBOM 作为 CI Artifact 保留，并在 Tag 发布时作为正式 GitHub Release 资产下载；
- 发布器在任何 GitHub API 写操作前校验 SPDX 2.x 文档结构、文档 ID 和非空 Package 清单；
- wheel、sdist 和镜像 SBOM 统一进入 `SHA256SUMS`；
- Release 同名资产继续保持内容一致性校验和幂等补传；
- Kubernetes Deployment、Migration Job、项目版本和锁文件统一到 `0.3.4`；
- 增加 CI、Action SHA、SBOM、版本和发布资产契约测试；
- 更新发布 Runbook，要求验收 wheel、sdist、SBOM 和校验和文件。

这些改动提升了版本可追溯性，但不等于目标环境已经完成生产验收。真实部署仍必须补齐 OIDC、Kafka、观测源、LLM、工单系统、外部修复控制器、容量和故障注入证据。

## v0.4.0 相比 v0.3.4 的完善

`v0.4.0` 是本项目面向评测闭环和平台治理能力的一次功能版本升级。相较上一版，主要完成了以下修复和增强：

- 补齐 Change Event 到 RCA 的持久化、查询、证据引用和 HMAC 鉴权闭环，并覆盖变更时间窗、租户隔离与幂等语义；
- 新增拓扑、影响面、告警关联、历史知识检索和有界动态调查能力，保持默认固定只读 RCA 计划和无界循环禁止；
- 新增 Workspace 管理 API、HTTP MCP Adapter，以及 Dataset Release 在线双审核、幂等、版本 fence 和发布后不可变约束；
- 将 MiniShop Benchmark 扩展为 12 个机器可读场景，补齐 Scenario Manifest、Ground Truth、结构化 Prediction、因果链、影响面、证据和 Tool 指标；
- 新增 Real-LLM-compatible Runner、`RCAReport → RCAPrediction` Runtime Adapter、消融评测、Bad Cases 和中文评测报告输出；
- 新增 Reference staging 全栈，覆盖 PostgreSQL、Redpanda、OIDC/JWKS、Prometheus、Loki、Tempo、LLM/Ticketing Mock、MCP 和 Agent；
- 新增 11/11 协议验收、100/500/1000 alerts/min 合成压测、Worker/Kafka/PostgreSQL/Loki 故障注入和租约 Fence 拒绝证据；
- 新增 Dataset Release 验收中的创建人自审禁止校验，修复验收主体映射错误，避免把创建者误当成 domain 审核人；
- 更新任务书完成清单、中文项目说明和 Reference staging 最终报告，明确 `synthetic=true`、`production_acceptance=false` 的证据边界；
- 统一项目版本、Kubernetes 镜像、锁文件和发布检查到 `0.4.0`，使 Tag、构建产物和部署清单可追溯。

本版本已通过 `1875 passed, 9 skipped` 全量测试、Ruff、Compose 配置校验和 Reference staging 11/11 验收。另已完成一轮真实千问 API 驱动的本地高保真仿真：12 场景 × 5 次、真实 PostgreSQL/Redpanda/OIDC/Prometheus/Loki/Tempo、Ticketing 幂等和四类故障注入均有独立证据；该结果明确标记为 `simulation=true`，不等同于生产签字。真实外部 staging/production 签字、目标环境容量和生产故障注入仍需在具备凭据的环境中完成。

## v0.5.0 相比 v0.4.0 的完善

`v0.5.0` 是面向“真实模型、真实组件、本地可复核证据”的工程完善版本。相较上一版，主要完成了以下修复和增强：

- 新增生产级本地仿真轨道，使用真实 PostgreSQL、Redpanda/Kafka、OIDC/JWKS、Prometheus、Loki、Tempo、Worker、MCP 和持久化 Ticketing 服务；
- 仿真直接调用项目 `.env` 中的 DashScope 千问 `qwen3.7-plus`，明确禁止 Ollama/vLLM 本地模型，不把 API Key 写入报告或 Git；
- 完成 12 个 MiniShop 场景的真实千问 RCA 12×5 评测，并记录根因、证据、工具、Token、成本和 P50/P95 延迟；
- 完成 baseline、change、topology、knowledge、dynamic 五变体消融，共 300 次真实调用；Dynamic 门禁通过，Evidence Recall 为 1.0、Unsupported Claim 为 0；
- 为真实 LLM 传输层增加有上限的连接重试，并为消融脚本增加 `--resume`，中断后只恢复缺失变体，不复用部分结果；
- 修复真实 Provider 消融元数据，准确记录变体调查策略，并增加安全并发参数；
- 完成 100/500/1000 alerts/min 本地负载验证、Worker/Kafka/PostgreSQL/Loki 故障注入和观测超时 Partial Report 降级证据；
- 增加中文本地高保真仿真验收报告，补齐任务书完成状态、生产边界、证据目录和清理说明；
- 更新项目版本、Kubernetes 镜像示例、锁文件和发布检查到 `0.5.0`，使 Tag、构建产物和部署清单保持一致。

本版本最终门禁为 `1875 passed, 9 skipped`、Ruff 通过、`uv lock --check` 通过、`git diff --check` 通过。真实千问仿真结果仍明确标记 `simulation=true`、`synthetic=false`、`production_acceptance=false`，不能替代企业生产签字。

## 部署与监控资产

- Docker：`Dockerfile`、`.dockerignore`；
- 本地生产化演练：`ops/deploy/docker-compose.yml`；
- Kubernetes 骨架：`ops/deploy/kubernetes/`；
- 环境变量清单：`.env.example`、`ops/deploy/env.production.example`；
- 发布与回滚：`ops/deploy/release-runbook.md`；
- 容量与 SLO：`ops/deploy/capacity-and-slo.md`；
- 备份恢复：`ops/deploy/backup-restore.md`；
- Prometheus 规则：`ops/prometheus/rules/`；
- Alertmanager：`ops/alertmanager/`；
- Grafana：`ops/grafana/`；
- 故障 Runbook：`ops/runbooks/`。

在安装 Prometheus 的环境中校验规则：

```powershell
promtool check rules ops/prometheus/rules/*.yml
```

Alertmanager Slack Webhook 必须以 Secret 文件挂载：

- `/run/secrets/alertmanager-slack-critical-url`；
- `/run/secrets/alertmanager-slack-warning-url`。

## 上线前必须完成

以下内容不能通过本地 Mock 或文档替代：

1. 真实外部 PostgreSQL、Kafka、OIDC、Prometheus、Loki、Tempo、LLM 和 Ticketing 的 staging/sandbox 签字；reference staging 只能证明本地协议和装配；
2. HTTPS OIDC issuer、JWKS、audience、Token 和证书链验证；
3. 1x、2x 和峰值流量下的 k6 压测报告；
4. 故障注入后的恢复时间、重复/丢失、Consumer Lag、Outbox Backlog、CPU 和内存证据；
5. 外部修复控制器的白名单、审批、超时、回滚和租约过期演练；
6. 组织级数据集存储、匿名化、多审核人签字和模型发布流程。

在这些证据完成前，项目的准确定位仍是：

> 生产导向的 Incident / 受控 RCA 作业编排平台，而不是已生产上线的自适应智能排障 Agent。

## 任务书实现进度（2026-08）

本分支已按两份任务书补齐可本地验证的能力：

- P0 Topology：`ServiceNode`、`ResourceNode`、`DependencyEdge`、静态/Trace 来源、租户隔离、TTL、环检测、最大深度、`topology.query@v1` 与确定性 Blast Radius；SQLAlchemy 适配器已接入 RCA runtime。
- P0 Dynamic RCA：`bounded_dynamic_v1`、`InvestigationState`、`StepDecision`、后端 Policy Validator、工具/证据/LLM 预算、重复调用阻止、Checkpoint/Resume。
- P0 Historical Knowledge：知识文档发布生命周期、租户隔离、lexical 检索、服务/指纹加权、`KNOWLEDGE` 参考证据和历史事实防混淆；内存/SQLAlchemy 检索器已接入 RCA runtime。
- P1 Alert Correlation：时间窗口、环境、服务/拓扑关系、告警类型和严重度的确定性收敛，以及 Primary Alert 元数据。
- P1 Benchmark：MiniShop-v2 场景 Manifest 已扩展到 12 个；Runner 支持 extended/scenario 选择，`RCAReportPredictionAdapter` 可接入生产报告，并输出 `results.json`、`evaluation-report.md`、`ablation-report.md`、`bad_cases.jsonl`。
- P1 Load/Chaos：提供 100/500/1000 alerts/min 负载计划、Python fallback 和 Worker/Kafka/PostgreSQL/观测超时演练 Harness；reference staging 已有 synthetic 实测，外部目标环境仍须独立签字。
- P1 Agent 可观测性：增加 RCA、Tool、LLM、Lease reclaim、Partial Report、Consumer Lag 指标，禁止使用 tenant/incident/workflow/trace 作为标签。
- P2 MCP/Workspace/Release：提供 HTTPS/Bearer/JSON-RPC 校验的 HTTP MCP Adapter、租户 Workspace 管理 API、Topology/Knowledge 工具注册，以及带 revision、幂等、双角色审核和同事务 Outbox 审计的在线 Dataset Release API。

扩展 Benchmark 的本地确定性合同示例：

```powershell
$commit = git rev-parse --short HEAD
uv run python -m ops.evaluation.run_benchmark `
  --scenarios '.\MiniShop 电商下单故障演练靶场\scenarios' `
  --include-extended `
  --mode deterministic --git-commit $commit `
  --output '.\ops\evaluation\artifacts\minishop-v2-run'
```

该命令生成 12 个场景的 `contract_fixture=true` 结果；真实 Agent/LLM 评测时改用包含
12 个结构化 Prediction 的 `--input` 文件。Runner 不会把 Ground Truth 注入模型输入，
也不会把未执行的消融或真实环境性能写成数字。
