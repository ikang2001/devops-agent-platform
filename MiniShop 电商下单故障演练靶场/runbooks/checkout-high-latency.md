# Checkout 高延迟排查手册

## 故障现象

- `checkout-service` p95 延迟升高。
- `/checkout` 响应时间明显变长。
- 可能出现 `CHECKOUT_DOWNSTREAM_FAILURE`。

## 可能原因

- checkout 自身处理延迟。
- inventory 下游响应变慢。
- payment 下游响应变慢。

## 推荐查询

- 查看 `minishop_http_request_duration_seconds` 中 `/checkout` 的 p95。
- 查看 `minishop_fault_enabled{service_name="checkout-service",fault_type="latency"}`。
- 查询带同一 `trace_id` 的 JSON 日志。

## 处理建议

- 先确认是否开启 checkout latency 故障。
- 如果 checkout 自身没有故障，继续看 inventory 和 payment 日志。
- 如果是下游导致，应在 RCA 中标明根因服务，不要只归因于 checkout。
