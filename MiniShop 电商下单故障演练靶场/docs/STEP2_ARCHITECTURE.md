# Step 2：架构设计

## 1. 模块划分

| 模块 | 职责 |
|---|---|
| routers | 暴露 HTTP API |
| services | 编排下单、支付、库存、通知逻辑 |
| faults | 管理内存故障状态 |
| metrics | 定义 Prometheus 指标 |
| logging | 输出 JSON 结构化日志 |
| runbooks | 提供故障排查知识 |
| scripts | 提供压测和 Agent 告警样例 |

## 2. 数据流

```text
用户或脚本请求 /checkout
  -> checkout service
  -> inventory reserve
  -> payment pay
  -> notification send
  -> 返回订单结果和 trace_id
```

当开启 inventory db timeout 后：

```text
/faults/inventory-db-timeout
  -> 内存故障状态 enabled
  -> /checkout 调用 inventory
  -> inventory 延迟并返回 DB_TIMEOUT
  -> checkout 返回 CHECKOUT_DOWNSTREAM_FAILURE
  -> 指标和 JSON 日志记录异常
```

## 3. 技术选型

| 能力 | 选择 | 原因 |
|---|---|---|
| Web 框架 | FastAPI | 和主 Agent 项目技术栈接近，学习成本低 |
| 故障状态 | 内存字典 | v1 演示足够，避免引入数据库 |
| 指标 | prometheus-client | Prometheus 原生格式，后续能直接采集 |
| 日志 | JSON logging | 方便 Loki 或 Agent 日志工具检索 |
| 测试 | pytest + TestClient | 适合本地快速验收 |

## 4. 稳定性设计

1. 每个请求都有 `trace_id`，支持从响应头和日志串联。
2. 故障有 `expires_at`，避免演示后忘记关闭。
3. `/faults/reset` 可以一键恢复正常状态。
4. 通知链路按 best effort 处理，避免非核心链路影响下单主流程。

## 5. 为什么先做单体

MiniShop 的第一目标是证明 Agent 能接入真实故障信号，而不是展示复杂部署能力。单体能让你更快完成故障注入、指标采集和 RCA 演示；多服务版本等 v1 跑通后再做，收益更高。
