# Step 1：需求分析

## 1. 需求复述

MiniShop 要解决的问题不是“做一个电商商城”，而是“给 DevOps 智能排障 Agent 提供一个真实可演示的故障现场”。它需要能稳定制造故障，输出可观测数据，并让 Agent 能接收到告警后完成根因分析。

## 2. 核心功能

1. 提供下单链路：checkout -> inventory -> payment -> notification。
2. 支持手动注入故障：payment 500、inventory db timeout、checkout latency。
3. 暴露 Prometheus 指标，方便 Prometheus 采集并触发告警。
4. 输出带 `trace_id` 的 JSON 日志，方便后续接 Loki 或日志查询工具。
5. 提供 Runbook，方便 Agent 做知识检索和 RCA 解释。

## 3. 输入输出

| 项目 | 内容 |
|---|---|
| 调用方 | 人工演示、压测脚本、Prometheus、DevOps Agent |
| 输入 | 下单请求、故障控制请求、Agent 告警目标地址 |
| 输出 | API 响应、结构化日志、Prometheus 指标、Runbook、告警 payload |
| 存储 | v1 使用内存状态，不引入数据库 |

## 4. 边界条件

1. 下单商品列表不能为空。
2. 商品数量必须大于 0。
3. 故障持续时间必须有限，过期后自动忽略。
4. 故障重置后不能继续影响业务请求。
5. Agent 告警字段必须包含 `external_event_id`。

## 5. 线上风险

1. 如果没有故障开关，演示时很难稳定复现问题。
2. 如果日志没有 `trace_id`，排查时无法串起一次请求。
3. 如果只有接口返回，没有指标，Agent 无法判断错误率和延迟趋势。
4. 如果直接上多服务和全套观测栈，会把第一版复杂度拉得过高。

## 6. 应对方案

第一版采用 FastAPI 单体服务，把核心价值聚焦在“故障可控、指标可采集、日志可追踪、告警可接入”。等闭环跑通后，再扩展到多服务、Loki、Tempo 和 Grafana。
