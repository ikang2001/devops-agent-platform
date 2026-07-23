# MiniShop 电商下单故障演练靶场

MiniShop 是 `DevOps 智能排障 Agent 平台` 的被排障对象。它不是完整电商系统，而是一个可控、可复现、可观测的故障靶场，用来模拟下单链路里的支付 500、库存数据库超时、下单接口变慢等典型线上故障。

完整演示链路：

```text
MiniShop 注入故障
  -> 暴露 Prometheus 指标和 JSON 日志
  -> Prometheus / Alertmanager 触发告警
  -> DevOps Agent 接收告警
  -> 创建 Incident 并启动 RCA
  -> 查询 Metrics / Logs / Runbook
  -> 生成 RCA 报告草稿
```

## 1. 启动

```powershell
cd "C:\Users\jwk\Desktop\llm\DevOps 智能排障 Agent 平台\MiniShop 电商下单故障演练靶场"
python -m pip install uv==0.11.31
uv sync --locked --extra dev
uv run uvicorn app.main:app --reload --port 18080
```

打开：

```text
http://127.0.0.1:18080/docs
http://127.0.0.1:18080/healthz
http://127.0.0.1:18080/metrics
```

## 2. 正常下单

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:18080/checkout" `
  -ContentType "application/json" `
  -Body '{"user_id":"u1001","items":[{"sku":"sku-001","quantity":1}],"idempotency_key":"demo-001"}'
```

返回里会包含 `order_id`、`status`、`trace_id`、`payment`、`notification` 和各阶段耗时。

## 3. 注入故障

开启 payment 500：

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:18080/faults/payment-error" `
  -ContentType "application/json" `
  -Body '{"error_rate":1.0,"duration_seconds":300,"created_by":"demo"}'
```

开启 inventory db timeout：

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:18080/faults/inventory-db-timeout" `
  -ContentType "application/json" `
  -Body '{"delay_ms":1200,"duration_seconds":300,"created_by":"demo"}'
```

开启 checkout latency：

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:18080/faults/checkout-latency" `
  -ContentType "application/json" `
  -Body '{"delay_ms":1200,"duration_seconds":300,"created_by":"demo"}'
```

清空故障：

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18080/faults/reset"
```

## 4. 指标与告警

MiniShop 暴露的核心指标：

```text
minishop_http_requests_total
minishop_http_request_duration_seconds
minishop_http_request_errors_total
minishop_fault_enabled
minishop_payment_error_total
minishop_inventory_db_timeout_total
```

Prometheus 配置在 `prometheus/` 目录，Alertmanager 示例配置在 `alertmanager/` 目录。

## 5. DevOps Agent 告警样例

只打印告警 payload：

```powershell
python .\scripts\send_agent_alert.py
```

发送到 Agent：

```powershell
python .\scripts\send_agent_alert.py --agent-url "http://127.0.0.1:8000/api/v1/alerts"
```

如果 DevOps Agent 开启了 HMAC 验签，先设置和平台一致的密钥：

```powershell
$env:DEVOPS_AGENT_ALERT_WEBHOOK_SECRET="replace-with-at-least-32-bytes-secret"
python .\scripts\send_agent_alert.py --agent-url "http://127.0.0.1:8000/api/v1/alerts"
```

真正联立演示，也就是“MiniShop 注入故障 -> checkout 失败 -> 发送告警到 DevOps Agent”：

```powershell
python .\scripts\demo_minishop_to_agent.py --agent-url "http://127.0.0.1:8000/api/v1/alerts"
```

payload 使用 `external_event_id` 作为外部幂等键；`fingerprint` 是为了兼容当前 DevOps Agent DTO 的必填字段：

```json
{
  "tenant_id": "demo",
  "source": "alertmanager",
  "service_name": "checkout-service",
  "severity": "CRITICAL",
  "summary": "checkout-service p95 latency is higher than 1s",
  "starts_at": "2026-07-06T10:00:00Z",
  "fingerprint": "minishop:checkout-service:high-latency",
  "external_event_id": "checkout-high-latency-001"
}
```

`http://127.0.0.1:18080/docs` 的作用只是 MiniShop 的接口调试台。真正和故障平台联立，靠的是上面的 `demo_minishop_to_agent.py` 或后续 Prometheus + Alertmanager Webhook。

## 6. 测试

```powershell
pytest
```

测试覆盖正常下单、三类故障注入、故障清理、指标暴露和 Agent 告警字段。

## 7. 面试讲法

可以这样介绍：

> 为了证明 DevOps 智能排障 Agent 不是空跑，我做了一个 MiniShop 故障演练靶场作为接入对象。它模拟电商下单链路，但不做完整商城，而是重点制造可观测的线上故障，例如 payment 500、inventory 数据库超时和 checkout 延迟升高。靶场会输出 Prometheus 指标、结构化 JSON 日志和 Runbook，Agent 接到告警后可以创建 Incident，查询多源证据，最后生成 RCA 报告。这样整个项目就形成了从故障发生、告警接入、证据采集到根因分析的闭环。

## 8. 后续扩展

- v1.1：接入完整 Prometheus + Alertmanager compose。
- v1.2：增加 Loki / Tempo / Grafana。
- v2：把单体拆成 checkout、payment、inventory、notification 多服务。
- v3：让 DevOps Agent 基于真实 Metrics / Logs / Trace 自动生成完整 RCA。
