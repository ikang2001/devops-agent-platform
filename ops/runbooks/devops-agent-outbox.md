# DevOps Agent Outbox Runbook

## Metrics Unavailable

**信号**：`DevOpsAgentOutboxMetricsUnavailable`

1. 检查 `devops_agent_outbox_backlog_source_up` 与 `stale`。
2. 查看积压聚合查询的超时或数据库连接错误日志。
3. 检查数据库连接池、网络和 `outbox_events` 索引是否存在。
4. 指标恢复前不要把旧快照当作当前积压量。

## Backlog High

**信号**：`DevOpsAgentOutboxBacklogHigh`

1. 对比 Pending 数量、最老年龄和 Worker 连续失败次数。
2. 检查 Kafka 可用性、认证、Topic 权限和发布超时。
3. 使用只读查询确认状态分布：

```sql
SELECT status, COUNT(*)
FROM outbox_events
WHERE status IN ('PENDING', 'PROCESSING', 'FAILED')
GROUP BY status;
```

4. 确认 Worker 仍在消费后再考虑扩容，避免无效扩容放大依赖压力。
5. 禁止直接批量修改 Outbox 状态；状态修复必须经过评审和审计。

## Oldest Message Too Old

**信号**：`DevOpsAgentOutboxOldestMessageTooOld`

1. 查询最老的有限记录，禁止无界扫描：

```sql
SELECT event_id, status, attempts, created_at, available_at, locked_until
FROM outbox_events
WHERE status IN ('PENDING', 'PROCESSING')
ORDER BY created_at, event_id
LIMIT 20;
```

2. 检查是否存在未到 `available_at` 的重试消息或过期租约。
3. 检查单个坏消息是否反复失败，并确认错误摘要不含敏感数据。
4. 恢复后确认最老年龄持续下降，而不只是 Pending 数量短暂下降。

## Failed Messages

**信号**：`DevOpsAgentOutboxHasFailedMessages`

1. 使用 `LIMIT` 查询失败记录及 `last_error`，不得导出完整 payload。
2. 按 `event_type` 和错误类型聚合，确认是否为系统性契约错误。
3. 修复根因后通过受审计的重放流程处理，禁止直接改为 `PENDING`。
4. 保留原事件 ID、尝试次数和处置记录，保证可追溯性。

## Worker Stopped

**信号**：`DevOpsAgentOutboxWorkerStopped`

1. 检查 Runtime 严重日志和 Worker 任务异常堆栈。
2. 确认数据库、Kafka 和进程资源是否健康。
3. 若是代码异常，停止继续发布并回滚；若是单实例问题，执行滚动替换。
4. 恢复后验证 Worker 状态、发布速率和积压年龄同时改善。

## Worker Repeated Failures

**信号**：`DevOpsAgentOutboxWorkerRepeatedFailures`

1. 检查最近失败类型，区分数据库抢占失败、Kafka 发布失败和租约冲突。
2. 验证指数退避是否生效，避免依赖故障期间形成紧密重试。
3. 检查失败是否集中于单个事件；必要时按事件 ID 隔离排查。
4. 不要通过提高最大重试次数掩盖永久性契约错误。
