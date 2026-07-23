# Payment 5xx 激增排查手册

## 故障现象

- `payment-service` 返回 500。
- 指标 `minishop_payment_error_total` 持续增长。
- 日志出现 `PAYMENT_GATEWAY_ERROR`。

## 可能原因

- 支付网关模拟异常。
- payment error fault 被开启。
- 真实场景中可能是第三方支付服务不可用。

## 推荐查询

- 查看 `minishop_fault_enabled{service_name="payment-service",fault_type="payment_error"}`。
- 查看 `minishop_payment_error_total` 增长趋势。
- 查询 `error_code=PAYMENT_GATEWAY_ERROR` 的日志。

## 处理建议

- 演练环境先调用 `/faults/reset` 恢复。
- 真实生产环境应检查第三方支付网关状态、超时配置和降级策略。
