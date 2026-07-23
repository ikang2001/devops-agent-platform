# Step 4：增量实现说明

## 1. 基础服务

先实现 `/healthz`、`/metrics` 和请求中间件。原因是后续所有业务接口都需要统一日志、指标和 `trace_id`。

## 2. 正常链路

再实现正常 `/checkout`，内部依次调用 inventory、payment 和 notification。这样能先证明业务链路能跑通，再开始注入故障。

## 3. 故障注入

故障注入不写死在业务逻辑里，而是通过 `/faults/*` 动态开启。这样演示时可以先跑正常流量，再打开故障观察指标和日志变化。

## 4. 指标设计

通用 HTTP 指标用于观察请求量、错误率和延迟；专用业务指标用于明确某类故障是否发生，例如 `minishop_inventory_db_timeout_total`。

## 5. 日志设计

日志字段固定包含 `trace_id`、`service_name`、`endpoint`、`status_code`、`latency_ms`、`error_code` 和 `fault_type`。这些字段足够支撑一次 RCA 演示。

## 6. Agent 接入

脚本生成的告警 payload 使用 Agent 当前接口需要的字段，特别是 `external_event_id`。当前 DevOps Agent DTO 仍要求 `fingerprint` 字段，所以 MiniShop 同时发送 `fingerprint` 兼容字段，但业务幂等仍以 `external_event_id` 为主。如果以后接 Alertmanager 原生 webhook，可以在适配层把 Alertmanager 的 fingerprint 映射成 `external_event_id`。
