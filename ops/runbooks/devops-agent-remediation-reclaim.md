# DevOps Agent Remediation Reclaim Runbook

修复租约回收 Worker 只扫描已过期的 `EXECUTING` 和 `ROLLING_BACK` 计划，
并通过 owner/attempt fence 将其收口为 `FAILED` 或 `ROLLBACK_FAILED`。
它不重试外部写、不接管执行，也不自动创建新计划。

## Worker Stopped

1. 确认部署是否预期启用：
   `DEVOPS_AGENT_REMEDIATION_RECLAIM_WORKER_ENABLED=true`。
2. 检查 `/readyz` 中 `remediation_reclaim` 组件状态。
3. 查看固定分类日志 `Remediation Reclaim Worker异常退出` 或非停机阶段取消记录。
4. 如果进程仍在但 Worker 已退出，重启实例恢复扫描，再分析退出原因。

## Worker Stalled

1. 检查数据库连接池、锁等待、长事务和 `remediation_plans` 慢查询。
2. 确认 `remediation_reclaim_batch_size` 和轮询间隔没有被异常放大。
3. 核对应用实例是否处于反复终止或 `STOPPING` 状态。
4. 恢复后确认最近循环和最近成功时间戳持续推进。

## Repeated Failures

1. 优先检查数据库可用性、迁移版本、表约束和事务冲突。
2. 健康快照中的错误摘要只用于分类；不要据此直接修改计划状态。
3. 修复依赖后确认连续失败计数归零，并观察至少两个成功扫描周期。
4. 若持续冲突，检查是否有其他实例或管理员并发收口同一批计划。

## Lease Expired

1. 从管理 API 按租户查询最近转为 `FAILED` / `ROLLBACK_FAILED` 的计划，核对
   `execution_summary` 或 `rollback_summary` 是否为 lease expired 固定摘要。
2. 结合计划的 trace ID 检查修复控制器超时、进程重启、网络中断或停机记录。
3. 确认迟到结果已被 owner/attempt fence 拒绝，不能手工覆盖失败态。
4. 由人工重新评估风险和现场状态；确需再次执行时创建并审批新计划。

## Manual Verification

以下只读 SQL 用于确认当前仍有多少过期候选：

```sql
select status, count(*)
from remediation_plans
where (
    status = 'EXECUTING'
    and execution_lease_expires_at <= now()
  ) or (
    status = 'ROLLING_BACK'
    and rollback_lease_expires_at <= now()
  )
group by status;
```

不要直接更新状态列，也不要重放原外部请求。应保留 Worker 生成的事务事件和
版本 fence，让所有状态变化继续可审计。
