# 路线 B：Remediation 执行租约总览

## 一句话

修复计划执行/回滚改为 **claim（attempt+owner+lease）→ 超时调用外部控制器 → fence 收口**；崩溃后可 `reclaim_stale` 到 `FAILED` / `ROLLBACK_FAILED`，迟到 finish 不能覆盖。

## 详细设计

完整字段、配置、事件与面试边界见：

→ [`remediation-execution-lease.md`](remediation-execution-lease.md)

## 关键改动清单

| 层 | 路径 |
| --- | --- |
| 领域 | `src/devops_agent_platform/domain/models/remediation.py` |
| 服务 | `src/devops_agent_platform/application/services/remediation_service.py` |
| 端口 | `src/devops_agent_platform/ports/remediation.py` |
| ORM/Mapper/Repo | `infrastructure/database/models|mappers/...`、`adapters/sqlalchemy/remediation_repository.py` |
| 迁移 | `migrations/versions/20260723_0028_create_remediation_plans.py` |
| 配置 | lease/timeout + 默认关闭的 reclaim Worker 有界轮询与停机配置 |
| 接线 | `bootstrap/runtime.py` 注入 lease / timeout，并监督 reclaim Worker |
| 监控 | readiness、低基数指标、Prometheus 告警、专用 Runbook |
| 测试 | service / Worker / Runtime / Settings / metrics / ops 资产测试 |

## 当前边界（诚实）

- **有**：领域 fence、服务 timeout、stale 列表、`reclaim_stale()`、默认关闭的
  独立后台 Worker、Runtime 监督、指标、告警和 Runbook
- **无**：自动重试/接管外部写、HTTP reclaim API、真实 staging 控制器故障注入签字

## 验收要点

1. `EXECUTING` 必须带 `execution_attempt >= 1` 与未过期 `execution_lease_expires_at`
2. finish 不匹配 owner/attempt → Conflict
3. 租约过期 → reclaim 为 FAILED，且旧 finish 被拒
4. settings：`request_timeout < lease`
5. Worker 每轮总量不超过 batch size，默认关闭，停机可中断轮询等待
6. Worker 异常降级健康状态并有界退避；崩溃会使 readiness 降级

## 与路线 A 的关系

路线 A 先纠正“通用自动修复引擎”话术；路线 B 再把**真实状态机缺陷**补上，使“受审批修复计划”在崩溃场景下可审计、可收口。
