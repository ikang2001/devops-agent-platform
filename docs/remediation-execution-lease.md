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

## 运行时入口

- 领域：`RemediationPlan.start_execution/finish_execution/fail_stale_execution` 及 rollback 对称方法
- 服务：`RemediationApplicationService.execute/rollback/reclaim_stale`
- 仓储：`list_stale_execution` / `list_stale_rollback`
- 迁移：`20260723_0028` 建表时已包含 lease 列与约束

## 如何回收卡住计划

应用层提供：

```python
await remediation_service.reclaim_stale(limit=50)
```

当前 **没有** 独立后台 worker 自动轮询；可在运维脚本、定时任务或运维 API 中调用。
未接入定时 reclaim 时，过期计划会在下次 execute/rollback 重放或显式 reclaim 时收口。

## 面试可说 / 不可说

**可以说：**

- 修复执行采用 claim + lease + owner/attempt fence；
- 崩溃后不会永久卡在 `EXECUTING`；
- 迟到回调不能覆盖已 reclaim 的失败结果。

**不可以说：**

- 这是通用自动修复引擎；
- 已具备自动重试与自动接管重跑外部写操作；
- 已有生产级后台 reclaim worker（当前是服务方法，需外部调度）。

## 相关文件

- `src/devops_agent_platform/domain/models/remediation.py`
- `src/devops_agent_platform/application/services/remediation_service.py`
- `src/devops_agent_platform/infrastructure/adapters/sqlalchemy/remediation_repository.py`
- `migrations/versions/20260723_0028_create_remediation_plans.py`
