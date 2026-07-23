# DevOps Agent Ticketing Gateway Runbook

## Error Ratio High

**信号**：`DevOpsAgentTicketingGatewayErrorRatioHigh`

1. 检查 HTTP JSON 工单网关 endpoint、DNS、TLS 证书、代理和出口网络。
2. 检查 Bearer Token 是否过期、被轮换或缺少目标系统提交权限。
3. 查看应用日志中的脱敏错误分类，确认是 HTTP 状态、响应结构漂移还是响应过大。
4. 不要把外部响应正文直接贴到排障群或新工单；先确认其中不包含凭据或用户数据。
5. 恢复后确认 `GATEWAY_ERROR` 比例回落，并抽查本地 `TicketSubmission` 是否通过幂等键只提交一次。

## Business Failure Ratio High

**信号**：`DevOpsAgentTicketingGatewayBusinessFailureRatioHigh`

1. 区分业务拒绝和网关错误：业务拒绝说明外部系统给出了可信失败结果，不应盲目重试。
2. 抽样本地 `TicketSubmission` 失败记录，检查失败摘要是否集中在项目只读、字段缺失、优先级不支持或目标系统路由错误。
3. 核对工单草稿生成规则、目标系统映射和外部工单中间层字段契约是否同步变更。
4. 若需要补偿，先确认提交幂等键和外部工单去重规则，避免重复创建工单。
5. 修复后观察业务失败比例回落，并确认 Consumer 连续失败和死信没有同步升高。

## P95 Latency High

**信号**：`DevOpsAgentTicketingGatewayP95LatencyHigh`

1. 检查外部工单系统或中间层是否处于慢响应、限流或排队状态。
2. 对比 Consumer 重试速率、网关错误比例和 HTTP 客户端超时配置。
3. 检查是否存在下游响应接近 `request_timeout_seconds`，导致 Worker 处理吞吐下降。
4. 扩大超时前先确认消息积压、数据库连接池和 HTTP 连接池没有同步耗尽。
5. 恢复后确认 P95 耗时回落，且提交成功率和 Consumer 最近成功时间恢复正常。
