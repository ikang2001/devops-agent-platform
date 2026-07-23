# DevOps Agent Audit Retention Runbook

审计留存清理 Worker 负责分批删除已过留存期 RCA 审计子记录，并在
`workflow_runs.audit_purged_at` 上推进清理水位。它只清理终态工作流的
Evidence 和 Tool Invocation，不删除 RCA 报告、工单草稿或提交记录。

## Worker Stopped

1. 确认部署是否预期启用：
   `DEVOPS_AGENT_AUDIT_RETENTION_WORKER_ENABLED=true`。
2. 检查 `/readyz` 中 `audit_retention` 组件状态。
3. 查看应用日志中 `Audit Retention Worker异常退出` 或非停机阶段取消记录。
4. 如果进程仍在但 Worker 停止，先重启实例恢复清理能力，再保留日志分析退出原因。

## Worker Stalled

1. 检查数据库连接池、锁等待和长事务，确认清理批次是否卡在删除或更新阶段。
2. 确认 `audit_retention_batch_size` 没有被配置得过大。
3. 查看数据库慢查询，重点关注 `workflow_runs`、`evidence`、`tool_invocations`。
4. 必要时临时降低批次大小，让 Worker 以更短事务恢复推进。

## No Successful Cycle

1. 查看最近错误摘要和应用日志，判断是数据库不可用、配置错误还是权限问题。
2. 确认 `audit_retention_days`、`audit_retention_batch_size` 等配置通过启动校验。
3. 如果没有错误但长期无成功循环，检查 Worker 是否处于 `STOPPING` 或进程正在反复重启。
4. 恢复后确认 `devops_agent_audit_retention_worker_last_success_timestamp_seconds` 开始推进。

## Repeated Failures

1. 优先处理数据库异常：连接耗尽、锁等待、磁盘空间、迁移不一致。
2. 确认业务表约束没有被手工数据修复破坏。
3. 检查清理 SQL 是否因为异常数据导致整批失败。
4. 修复后观察连续失败计数是否归零，并确认累计清理批次数继续增长。

## Manual Verification

可用以下只读 SQL 辅助判断是否存在可清理候选：

```sql
select count(*)
from workflow_runs
where status in ('SUCCEEDED', 'FAILED', 'CANCELED')
  and ended_at < now() - interval '30 days'
  and audit_purged_at is null;
```

如果候选数量很大，优先保持小批次多轮清理，不要直接执行无界删除。
