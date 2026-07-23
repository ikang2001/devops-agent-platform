# MiniShop 电商下单故障演练靶场设计方案

## 1. 项目定位

MiniShop 故障演练靶场是 DevOps 智能排障 Agent 平台的被排障对象。

它不是一个完整电商业务系统，而是一个可控、可复现、可观测的故障演练环境，用来模拟真实线上微服务中的延迟升高、错误率升高、数据库超时、下游依赖失败、链路阻塞等故障场景。

DevOps Agent 平台通过接入 MiniShop 靶场产生的告警、指标、日志、链路和 Runbook，验证完整的智能排障闭环。

完整闭环如下：

```text
MiniShop 业务故障
↓
Prometheus 产生指标异常
↓
Alertmanager 发送告警 Webhook
↓
DevOps Agent 接收告警
↓
创建 Alert / Incident
↓
启动 RCA Workflow
↓
查询 Metrics / Logs / Trace / Runbook
↓
生成 Evidence / RCA Report
↓
人工确认 / 工单草稿
```

说明：`RootCauseCandidate` 可以作为后续增强概念，用来做多个根因候选的排序和置信度管理；第一版 MiniShop 接入验证不把它作为必做项，避免把靶场实现范围拉得过大。

一句话定位：

> MiniShop 负责制造可观测的故障现场，DevOps Agent 负责基于故障现场进行根因分析。

---

## 2. 系统关系

两个系统的关系如下：

```text
┌──────────────────────────────┐
│      MiniShop 故障靶场        │
│  checkout / payment / stock  │
│  fault injection / metrics   │
└───────────────┬──────────────┘
                │
                │ 产生指标、日志、链路、告警
                ▼
┌──────────────────────────────┐
│        可观测系统             │
│ Prometheus / Alertmanager    │
│ Loki / Tempo / Grafana       │
└───────────────┬──────────────┘
                │
                │ Webhook / Query API
                ▼
┌──────────────────────────────┐
│ DevOps 智能排障 Agent 平台    │
│ Alert → Incident → RCA       │
│ Evidence → Report → Ticket   │
└──────────────────────────────┘
```

其中：

| 系统 | 作用 |
|---|---|
| MiniShop 故障靶场 | 制造故障、暴露指标、输出日志、生成链路 |
| Prometheus | 采集指标，判断错误率、延迟、QPS 是否异常 |
| Alertmanager | 根据告警规则触发 Webhook |
| Loki | 存储和检索结构化日志 |
| Tempo | 存储和检索 OpenTelemetry Trace |
| Runbook | 提供故障排查手册 |
| DevOps Agent | 接收告警、创建事故、采集证据、生成 RCA 报告 |

---

## 3. 总体架构

建议 MiniShop 靶场采用渐进式架构。

第一版必须优先做单体服务，降低开发复杂度，把重点放在“故障可控、指标可观测、告警可接入、Agent 可分析”上。不要一开始拆成多个微服务，否则时间会被服务编排、网络、部署和链路配置消耗掉，反而影响主项目闭环验证。

等单体版本能稳定演示 3 个核心故障场景后，再演进成多服务版本，用来模拟更真实的跨服务调用链路。

### 3.1 第一版：单体靶场服务

```text
demo-target-service
├── /checkout
├── /payment/pay
├── /inventory/reserve
├── /notification/send
├── /faults/*
├── /metrics
└── /healthz
```

第一版重点：

```text
能产生故障
能暴露 metrics
能输出 JSON 日志
能触发 Prometheus 告警
能被 DevOps Agent 接收告警
```

### 3.2 第二版：MiniShop 微服务靶场

```text
MiniShop Fault Lab
├── checkout-service
├── payment-service
├── inventory-service
├── notification-service
└── fault-control-service
```

调用链路：

```text
用户请求 /checkout
↓
checkout-service
├── 调用 inventory-service 扣库存
├── 调用 payment-service 支付
└── 调用 notification-service 发通知
```

---

## 4. 服务设计

## 4.1 checkout-service：下单服务

checkout-service 是 MiniShop 的主入口，用于模拟用户下单链路。

### 核心接口

```text
POST /checkout
```

### 主要职责

```text
接收下单请求
调用库存服务
调用支付服务
调用通知服务
返回下单结果
暴露下单接口延迟和错误率指标
```

### 可模拟故障

```text
下单接口变慢
checkout 自身处理延迟升高
下游 payment 响应慢导致 checkout 慢
下游 inventory 超时导致 checkout 慢
checkout p95 延迟升高
```

### 典型 RCA 场景

表面现象：

```text
checkout-service p95 延迟升高
```

可能根因：

```text
checkout 自身慢
payment 下游慢
inventory 下游慢
```

Agent 需要通过 Metrics、Logs、Trace 判断真正慢点在哪里。

---

## 4.2 payment-service：支付服务

payment-service 用于模拟支付链路。

### 核心接口

```text
POST /payment/pay
```

### 主要职责

```text
模拟支付请求
模拟第三方支付网关调用
返回支付成功或失败
暴露支付错误率和延迟指标
```

### 可模拟故障

```text
payment 偶发 500
payment 错误率升高
payment 响应延迟升高
第三方支付依赖超时
```

### 典型日志

```json
{
  "level": "ERROR",
  "service_name": "payment-service",
  "error_code": "PAYMENT_GATEWAY_ERROR",
  "message": "payment gateway returned 500",
  "fault_type": "payment_error"
}
```

---

## 4.3 inventory-service：库存服务

inventory-service 用于模拟库存扣减和数据库访问。

### 核心接口

```text
POST /inventory/reserve
```

### 主要职责

```text
模拟库存查询
模拟库存扣减
访问 PostgreSQL 或 mock database
暴露数据库超时、慢查询、连接池等指标
```

### 可模拟故障

```text
数据库连接超时
数据库慢查询
连接池耗尽
库存锁等待
inventory 响应变慢
```

### 典型日志

```json
{
  "level": "ERROR",
  "service_name": "inventory-service",
  "error_code": "DB_TIMEOUT",
  "message": "inventory database query timeout",
  "latency_ms": 3021,
  "fault_type": "db_timeout"
}
```

### 典型 RCA 场景

表面现象：

```text
checkout-service 下单接口变慢
```

真实根因：

```text
inventory-service 数据库访问超时
```

这个场景最适合展示 Trace 的价值。

---

## 4.4 notification-service：通知服务

notification-service 用于模拟短信、邮件、站内信等通知链路。

### 核心接口

```text
POST /notification/send
```

### 主要职责

```text
模拟通知发送
模拟第三方通知平台调用
暴露通知失败率和延迟指标
```

### 可模拟故障

```text
第三方通知服务失败
通知接口超时
通知失败率升高
```

### 设计意义

notification-service 可以用来验证 Agent 是否能区分：

```text
核心下单链路故障
非核心旁路故障
```

例如通知失败可能不会导致下单失败，但会产生错误日志和告警。

---

## 5. 故障注入模块设计

MiniShop 靶场必须有一个 fault-control 能力，用来手动开启、关闭和查询故障状态。

### 5.1 故障控制接口

```text
GET  /faults
POST /faults/latency
POST /faults/error-rate
POST /faults/db-timeout
POST /faults/downstream-timeout
POST /faults/reset
```

### 5.2 示例：开启 payment 错误率故障

```json
{
  "service": "payment-service",
  "fault_type": "error_rate",
  "error_rate": 0.3,
  "duration_seconds": 300
}
```

含义：

```text
让 payment-service 在 5 分钟内有 30% 概率返回 500。
```

### 5.3 示例：开启 inventory 数据库超时故障

```json
{
  "service": "inventory-service",
  "fault_type": "db_timeout",
  "delay_ms": 3000,
  "duration_seconds": 300
}
```

含义：

```text
让 inventory-service 数据库操作延迟 3 秒，持续 5 分钟。
```

### 5.4 故障状态字段

```text
fault_id
service_name
fault_type
enabled
error_rate
delay_ms
starts_at
expires_at
created_by
```

---

## 6. 故障场景设计

第一版建议至少支持 3 个故障场景，后续扩展到 6 个。

### 6.1 第一版必做场景

| 场景 | 故障点 | 表面现象 | 期望根因 |
|---|---|---|---|
| payment_500 | payment-service | 支付 500 错误率升高 | payment-service 偶发异常 |
| inventory_db_timeout | inventory-service | checkout p95 延迟升高 | inventory 数据库访问超时 |
| checkout_latency | checkout-service | checkout 接口变慢 | checkout 自身处理延迟升高 |

### 6.2 后续增强场景

| 场景 | 故障点 | 表面现象 | 期望根因 |
|---|---|---|---|
| notification_downstream_fail | notification-service | 通知失败日志增多 | 第三方通知服务失败 |
| checkout_downstream_timeout | checkout 调 payment | checkout 超时 | payment 下游响应慢 |
| cascading_failure | inventory 慢导致 checkout 慢 | 多服务同时告警 | inventory 数据库超时是根因 |

### 6.3 重点场景：级联故障

级联故障是最能体现 Agent 价值的场景。

```text
inventory-service 数据库慢
↓
checkout-service 等 inventory 返回
↓
checkout p95 延迟升高
↓
payment / notification 可能出现连带异常
↓
多个服务同时告警
↓
Agent 需要判断真正根因是 inventory，而不是 checkout
```

这个场景可以证明：

```text
Agent 不是简单复述告警，而是能关联多源证据定位根因。
```

---

## 7. 可观测数据设计

MiniShop 需要向 DevOps Agent 提供 5 类数据来源。

---

## 7.1 告警：Alertmanager Webhook

告警链路：

```text
MiniShop 暴露 /metrics
↓
Prometheus 采集指标
↓
Prometheus 命中告警规则
↓
Alertmanager 发送 Webhook
↓
DevOps Agent /api/v1/alerts 接收告警
```

### 告警示例

```json
{
  "tenant_id": "demo",
  "source": "alertmanager",
  "service_name": "checkout-service",
  "severity": "P1",
  "summary": "checkout-service p95 latency is higher than 1s",
  "starts_at": "2026-07-06T10:00:00Z",
  "external_event_id": "checkout-high-latency-001"
}
```

注意：当前 DevOps Agent 平台的告警接口使用 `external_event_id` 作为外部幂等键；如果后续从 Alertmanager 原始 Webhook 接入，可以在适配层把 Alertmanager 的 `fingerprint` 映射为 `external_event_id`。

---

## 7.2 指标：Prometheus Metrics

每个服务暴露：

```text
GET /metrics
```

建议指标：

```text
http_requests_total
http_request_duration_seconds
http_request_errors_total
minishop_fault_enabled
minishop_payment_error_total
minishop_inventory_db_timeout_total
minishop_downstream_timeout_total
```

### Agent 可查询的问题

```text
checkout-service 最近 5 分钟 p95 延迟是否升高？
payment-service 最近 5 分钟 5xx 错误率是多少？
inventory-service db_timeout_total 是否持续增加？
当前是否开启了某类 fault？
```

---

## 7.3 日志：Loki / JSON 结构化日志

每个服务输出 JSON 日志。

### 日志字段规范

```text
timestamp
level
service_name
trace_id
span_id
endpoint
method
status_code
latency_ms
error_code
fault_type
message
```

### 日志示例

```json
{
  "timestamp": "2026-07-06T10:00:00Z",
  "level": "ERROR",
  "service_name": "inventory-service",
  "trace_id": "trc_abc123",
  "span_id": "span_001",
  "endpoint": "/inventory/reserve",
  "error_code": "DB_TIMEOUT",
  "message": "inventory database query timeout",
  "latency_ms": 3021,
  "fault_type": "db_timeout"
}
```

Agent 后续可以通过 LogsTool 查询：

```text
某服务最近 15 分钟 ERROR 日志
包含 DB_TIMEOUT 的日志
某个 trace_id 对应的完整日志
```

---

## 7.4 链路：Tempo / OpenTelemetry Trace

下单链路 Trace：

```text
checkout-service /checkout
├── inventory-service /inventory/reserve
├── payment-service /payment/pay
└── notification-service /notification/send
```

如果 inventory 慢，Trace 中应该体现：

```text
checkout 总耗时 3500ms
inventory span 耗时 3100ms
payment span 耗时 100ms
notification span 耗时 80ms
```

Agent 可以据此判断：

```text
checkout 慢不是 checkout 自身慢，而是 inventory 下游慢。
```

---

## 7.5 知识：Runbook 文档

MiniShop 需要准备故障排查手册，供 DevOps Agent 的知识检索层使用。

推荐目录：

```text
runbooks/
├── checkout-high-latency.md
├── payment-5xx-spike.md
├── inventory-db-timeout.md
├── notification-downstream-failure.md
└── cascading-failure.md
```

### Runbook 内容结构

每份 Runbook 建议包含：

```text
故障现象
可能原因
推荐查询指标
推荐查询日志
推荐查看 Trace
处理建议
是否需要人工确认
```

### 示例：inventory-db-timeout.md

```markdown
# Inventory 数据库超时排查手册

## 故障现象

- checkout-service p95 延迟升高
- inventory-service ERROR 日志出现 DB_TIMEOUT
- Trace 中 inventory span 耗时明显升高

## 可能原因

- inventory 数据库连接池耗尽
- PostgreSQL 慢查询
- 数据库临时不可用

## 推荐排查

- 查询 inventory_db_timeout_total
- 查询 inventory-service ERROR 日志
- 查看 checkout trace 中 inventory span 耗时

## 处理建议

- 检查数据库连接池配置
- 检查慢查询
- 检查 PostgreSQL 连接数和锁等待
- 必要时扩容数据库连接池
```

---

## 8. DevOps Agent 接入设计

MiniShop 与 DevOps Agent 的接入关系如下。

| DevOps Agent 层 | MiniShop 提供的数据 |
|---|---|
| 接入层 | Alertmanager Webhook |
| 接口层 | /api/v1/alerts 接收 MiniShop 告警 |
| 应用编排层 | 创建 Alert / Incident，启动 RCA |
| 领域层 | Alert、Incident、Evidence、WorkflowRun、ToolInvocation |
| Agent 引擎层 | RCA Workflow 分析 MiniShop 故障 |
| 工具适配层 | MetricsTool 查 Prometheus，LogsTool 查 Loki，TraceTool 查 Tempo |
| 知识检索层 | 检索 MiniShop Runbook |
| 基础设施层 | PostgreSQL、Redis、MQ、对象存储等后续支撑 |
| 运维治理层 | trace_id、工具调用审计、Agent 执行记录 |

完整接入流程：

```text
1. 压测脚本持续请求 MiniShop /checkout
2. 调用 /faults/db-timeout 开启 inventory 数据库超时
3. checkout-service 响应变慢
4. Prometheus 采集到 p95 延迟升高
5. Alertmanager 触发 HighLatency 告警
6. Alertmanager Webhook 调 DevOps Agent /api/v1/alerts
7. DevOps Agent 创建 Alert 和 Incident
8. RCA Workflow 启动
9. MetricsTool 查询 Prometheus
10. LogsTool 查询 Loki
11. TraceTool 查询 Tempo
12. Knowledge Retrieval 检索 inventory-db-timeout Runbook
13. Agent 基于 Evidence 形成 RCA 结论
14. 系统生成 RCA Report 草稿
15. 人工确认
16. 生成工单草稿
```

---

## 9. MiniShop 项目目录建议

## 9.1 第一版单体目录

```text
demo-target-service/
├── app/
│   ├── main.py
│   ├── routers/
│   │   ├── checkout.py
│   │   ├── payment.py
│   │   ├── inventory.py
│   │   ├── notification.py
│   │   └── faults.py
│   ├── observability/
│   │   ├── metrics.py
│   │   ├── logging.py
│   │   └── tracing.py
│   ├── fault_injection/
│   │   ├── state.py
│   │   └── rules.py
│   └── config.py
├── runbooks/
├── prometheus/
│   ├── prometheus.yml
│   └── alert_rules.yml
├── alertmanager/
│   └── alertmanager.yml
├── docker-compose.yml
└── README.md
```

## 9.2 后续微服务目录

```text
minishop-fault-lab/
├── services/
│   ├── checkout_service/
│   ├── payment_service/
│   ├── inventory_service/
│   └── notification_service/
├── common/
│   ├── logging/
│   ├── tracing/
│   ├── metrics/
│   ├── config/
│   └── fault_injection/
├── observability/
│   ├── prometheus/
│   ├── loki/
│   ├── tempo/
│   └── grafana/
├── runbooks/
├── scripts/
│   ├── load_checkout.py
│   ├── inject_inventory_db_timeout.py
│   ├── inject_payment_500.py
│   └── reset_faults.py
├── docker-compose.yml
└── README.md
```

---

## 10. 技术选型

| 能力 | 推荐技术 | 说明 |
|---|---|---|
| 服务框架 | FastAPI | 与 DevOps Agent 技术栈一致，开发成本低 |
| 指标 | prometheus-client | 暴露 Prometheus 格式指标 |
| 日志 | JSON logging | 方便 Loki 检索和 Agent 分析 |
| Trace | OpenTelemetry | 生成跨服务调用链 |
| 数据库 | PostgreSQL | 模拟真实数据库依赖 |
| 告警 | Prometheus + Alertmanager | 产生真实告警 Webhook |
| 日志系统 | Loki | 查询结构化日志 |
| 链路系统 | Tempo | 查询 Trace |
| 可视化 | Grafana | 展示指标、日志、链路 |
| 部署 | Docker Compose | 本地演示和面试展示足够 |

---

## 11. 实施路线

## 11.1 阶段一：最小靶场

目标：

```text
实现一个 demo-target-service
支持基础 checkout 链路
支持 3 个故障场景
暴露 /metrics
输出 JSON 日志
准备 Runbook
```

交付物：

```text
/checkout
/payment/pay
/inventory/reserve
/faults/payment-error
/faults/inventory-db-timeout
/faults/checkout-latency
/faults/reset
/metrics
runbooks/*.md
```

---

## 11.2 阶段二：接 Prometheus + Alertmanager

目标：

```text
Prometheus 能采集 MiniShop 指标
Alertmanager 能触发告警
DevOps Agent 能收到告警
```

交付物：

```text
prometheus.yml
alert_rules.yml
alertmanager.yml
DevOps Agent /api/v1/alerts 对接
```

---

## 11.3 阶段三：接 Loki + Tempo

目标：

```text
Agent 可以查询日志和链路
```

交付物：

```text
结构化 JSON 日志
Loki 配置
OpenTelemetry Trace
Tempo 配置
Grafana Dashboard
```

---

## 11.4 阶段四：Agent 完整 RCA

目标：

```text
Agent 能基于指标、日志、Trace、Runbook 生成 RCA 报告
```

交付物：

```text
MetricsTool
LogsTool
TraceTool
Runbook Retrieval
Evidence
RCAReport
```

后续增强项：

```text
RootCauseCandidate 排序
根因置信度评分
多候选根因对比
```

---

## 12. 最小验收标准

MiniShop 靶场第一版完成以下能力即可：

```text
1. 能正常调用 /checkout
2. 能手动开启 payment 500 故障
3. 能手动开启 inventory db timeout 故障
4. 能手动开启 checkout latency 故障
5. 能暴露 Prometheus /metrics
6. 能输出带 trace_id 的 JSON 日志
7. 有至少 3 份 Runbook
8. Prometheus 能触发至少 2 条告警
9. Alertmanager 能把告警打到 DevOps Agent
10. DevOps Agent 能创建 Incident
11. Agent 最终报告能指出明确根因和证据
```

---

## 13. 面试讲法

可以这样介绍：

> 为了让 DevOps 智能排障 Agent 平台不是空跑，我设计了一个 MiniShop 故障演练靶场作为被排障对象。它模拟电商下单链路，包括 checkout、payment、inventory、notification 等服务，并内置支付 500、库存数据库超时、下游调用变慢、接口 p95 延迟升高等故障模式。
>
> MiniShop 会暴露 Prometheus 指标、结构化 JSON 日志和 OpenTelemetry Trace，同时配置 Alertmanager 告警规则和对应 Runbook。Agent 平台接收到 Alertmanager Webhook 后，会创建 Alert 和 Incident，启动 RCA Workflow，通过 MetricsTool、LogsTool、TraceTool 和 Knowledge Retrieval 分别查询指标、日志、链路和 Runbook，最后生成根因候选、证据列表和 RCA 报告草稿。
>
> 这样项目形成了完整闭环：业务系统故障 → 告警触发 → 多源证据采集 → Agent 分析 → RCA 报告 → 人工确认 / 工单草稿。

---

## 14. 不建议做的内容

| 不建议 | 原因 |
|---|---|
| 不要做完整电商系统 | 商品、购物车、真实订单太多，会偏离排障主题 |
| 不要一开始拆太多服务 | 部署复杂度高，容易拖慢主项目 |
| 不要一开始上 K8s | Docker Compose 足够演示 |
| 不要接真实支付 | 用 mock 支付即可 |
| 不要让 Agent 自动修复 MiniShop | 第一阶段只做分析和建议 |
| 不要把故障逻辑写死 | 要通过 fault-control 动态开启和关闭 |
| 不要只打印普通文本日志 | 必须结构化，方便 Loki 和 Agent 检索 |

---

## 15. 最终推荐项目组合

建议项目组合命名：

```text
devops-agent-platform/
    生产级 DevOps 智能排障 Agent 平台

minishop-fault-lab/
    MiniShop 电商下单故障演练靶场
```

两者职责：

```text
minishop-fault-lab：制造故障和观测数据
devops-agent-platform：接收告警和智能排障
```

最终目标：

```text
故障发生
↓
告警接入
↓
Incident 建模
↓
多源证据采集
↓
Agent 根因分析
↓
RCA 报告生成
↓
人工确认 / 工单草稿
```

一句话总结：

> MiniShop 靶场不要做成完整电商，而要做成一个可控的故障发生器和观测数据生产器。它通过电商下单链路模拟真实线上故障，再把告警、指标、日志、Trace 和 Runbook 提供给 DevOps Agent 平台，最终验证“故障发生 → 告警接入 → Incident 建模 → 多源证据采集 → RCA 报告生成”的完整闭环。
