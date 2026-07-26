# 修复执行租约与崩溃恢复（路线 B）

本文说明平台 remediation 执行/回滚路径的 **claim → 超时调用 → fence 收口** 语义。
这是对“进程在外部控制器返回前崩溃，导致永久卡在 `EXECUTING` / `ROLLING_BACK`”缺陷的最小修复。

## 问题

旧路径：

1. 把计划推进到 `EXECUTING` 并提交；
2. 调用外部修复控制器；
3. 再 `finish_execution` 收口。

若进程在第 2 步崩溃，计划会永久停留在中间态，没有租约、没有过期扫描、迟到的 finish 也没有 owner/attempt 栅栏。

## 目标语义

| 阶段 | 行为 |
| --- | --- |
| claim | `start_execution` / `start_rollback` 写入 `attempt`、`owner`、`lease_expires_at` 后先持久化 |
| 外部调用 | `asyncio.wait_for` 包住 executor，超时时间 < 租约 |
| fence finish | `finish_*` 必须匹配当前 `owner` 与 `attempt`，并清除 lease |
| reclaim | 租约过期后 `fail_stale_*` → `FAILED` / `ROLLBACK_FAILED` |

MVP **不**自动重试外部写操作；过期直接失败，由人工决定是否新建计划。

## 关键字段

- `execution_attempt` / `rollback_attempt`
- `execution_lease_expires_at` / `rollback_lease_expires_at`
- owner 复用既有 `executed_by` / `rolled_back_by`

## 配置

| 配置项 | 默认 | 约束 |
| --- | --- | --- |
| `DEVOPS_AGENT_REMEDIATION_REQUEST_TIMEOUT_SECONDS` | 10 | 必须 **严格小于** lease |
| `DEVOPS_AGENT_REMEDIATION_LEASE_SECONDS` | 60 | 5–3600 |
| `DEVOPS_AGENT_REMEDIATION_RECLAIM_WORKER_ENABLED` | false | 默认关闭，部署显式开启 |
| `DEVOPS_AGENT_REMEDIATION_RECLAIM_BATCH_SIZE` | 50 | 1–1000，每轮总收口上限 |
| `DEVOPS_AGENT_REMEDIATION_RECLAIM_INTERVAL_SECONDS` | 30 | `(0, 3600]` |
| `DEVOPS_AGENT_REMEDIATION_RECLAIM_ERROR_BACKOFF_INITIAL_SECONDS` | 5 | 必须不大于最大退避 |
| `DEVOPS_AGENT_REMEDIATION_RECLAIM_ERROR_BACKOFF_MAX_SECONDS` | 300 | 最大 86400 |
| `DEVOPS_AGENT_REMEDIATION_RECLAIM_SHUTDOWN_TIMEOUT_SECONDS` | 30 | `(0, 3600]` |

## 运行时入口

- 领域：`RemediationPlan.start_execution/finish_execution/fail_stale_execution` 及 rollback 对称方法
- 服务：`RemediationApplicationService.execute/rollback/reclaim_stale`
- Worker：`RemediationReclaimWorkerRunner`，有界轮询、退避、健康快照和协作式停止
- 仓储：`list_stale_execution` / `list_stale_rollback`
- Runtime：独立任务监督、readiness 组件、优雅停机超时
- 监控：低基数指标、Prometheus 告警和专用 Runbook
- 迁移：`20260723_0028` 建表时已包含 lease 列与约束

## 如何回收卡住计划

应用层提供：

```python
await remediation_service.reclaim_stale(limit=50)
```

当前已有默认关闭的独立后台 Worker。部署同时配置修复控制器和动作目录，并显式设置
`DEVOPS_AGENT_REMEDIATION_RECLAIM_WORKER_ENABLED=true` 后，Runtime 会每轮最多收口
`BATCH_SIZE` 个候选；执行租约与回滚租约交错处理，避免单一状态长期挤占批次。
失败使用有上限的指数退避，停机先发协作式停止信号，超时后才取消任务。

Worker 只调用同一个 `reclaim_stale()` 应用服务；显式运维调用仍可使用，但 `limit`
同样被限制在 1–1000。Worker 不调用 executor，不重放原请求，也不创建新计划。

## 面试可说 / 不可说

**可以说：**

- 修复执行采用 claim + lease + owner/attempt fence；
- 崩溃后不会永久卡在 `EXECUTING`；
- 迟到回调不能覆盖已 reclaim 的失败结果；
- 可选后台 Worker 能自动扫描并收口过期租约，且具备 readiness、指标、告警和优雅停机。

**不可以说：**

- 这是通用自动修复引擎；
- 已具备自动重试与自动接管重跑外部写操作；
- 已在真实 staging 或生产控制器故障注入中完成验收。

## 相关文件

- `src/devops_agent_platform/domain/models/remediation.py`
- `src/devops_agent_platform/application/services/remediation_service.py`
- `src/devops_agent_platform/application/services/remediation_reclaim_worker.py`
- `src/devops_agent_platform/bootstrap/runtime.py`
- `src/devops_agent_platform/infrastructure/adapters/sqlalchemy/remediation_repository.py`
- `ops/runbooks/devops-agent-remediation-reclaim.md`
- `migrations/versions/20260723_0028_create_remediation_plans.py`
