# Payment 发布回归排查手册

## 故障现象

- `payment-service` 在 v2 发布后开始返回 500。
- 指标 `minishop_payment_deployment_regression_total` 增长。
- 日志或 Trace 出现 `PAYMENT_DEPLOYMENT_REGRESSION`。

## 证据要求

- 核对 `changes.query@v1` 返回的服务、版本和变更时间。
- 同时确认错误指标，以及日志或 Trace 证据。
- 只有发布记录、没有错误信号时，不得把发布直接判定为根因。

## 处理建议

- 演练环境调用 `/faults/reset` 恢复。
- 生产环境先按发布平台流程停止扩容或灰度，再由人工确认是否回滚。
- 回滚后验证支付成功率和 checkout 错误率恢复。
