# Change Event 到 RCA 闭环

本文说明平台已经实现的变更事件接入、持久化、只读检索和 RCA Evidence 链路。
这里描述的是仓库代码与本地 Compose 已验证能力，不代表任何目标环境已经完成生产签字。

## 闭环概览

```text
CI/CD / 发布系统
    → POST /api/v1/change-events（HMAC）
    → ChangeEvent + change_event.received Outbox（同一事务）
    → Incident 显式启动 RCA
    → metrics.query@v1
    → changes.query@v1
    → logs.query@v1
    → traces.query@v1
    → runbooks.retrieve@v1
    → CHANGE / METRIC / LOG / TRACE / RUNBOOK Evidence
    → 候选 RCA Report
```

变更记录只是时间线证据，不是根因结论。发布回归必须再由运行时指标、日志或链路证据
支持；模型不能仅凭“最近发生过部署”确认根因。

## 接入契约

入口为 `POST /api/v1/change-events`，复用生产 Webhook 的 HMAC-SHA256 鉴权和时间窗口：

- `X-DevOps-Agent-Timestamp`
- `X-DevOps-Agent-Signature: sha256=<hex>`
- 签名正文是未经重排的原始 HTTP body

领域模型覆盖租户、来源、外部事件 ID、服务、资源、变更类型、状态、前后版本、操作者、
摘要、metadata、开始时间和完成时间。时间必须带时区；完成时间不能早于开始时间。
metadata 必须是严格 JSON，经统一脱敏和规范化后最多 16 KiB；Outbox 不复制自由 metadata。

## 幂等与事务

幂等键是 `(tenant_id, source, external_event_id)`：

- 首次相同键写入：返回 `ACCEPTED`；
- 相同键、相同规范业务载荷重试：返回原 `change_event_id` 和 `DUPLICATE`；
- 相同键、不同载荷：返回冲突，不覆盖旧事实；
- 并发唯一约束竞争：写事务回滚后只读恢复一次，相同载荷仍按重复请求返回。

`ChangeEvent` 与 `change_event.received` Outbox 在同一个 Unit of Work 中提交，避免数据库
已写入但事件未记录。请求哈希基于脱敏、规范化后的业务字段，不包含 trace ID 和接入时间。

## `changes.query@v1` 安全边界

工具不接受 LLM 提供的服务名、SQL 或任意时间范围。服务名从可信 Incident 派生，窗口由
服务端固定为 Incident 前 30 分钟到后 10 分钟。调用方只能传有界 `max_results`。

查询结果必须同时满足：

- 同租户、同服务、位于固定窗口；
- 按 `(started_at, change_event_id)` 稳定倒序；
- 不重复、不越过 `limit + 1` 容量边界；
- 输出最多 60 KiB，超限时显式标记 `possibly_truncated`；
- 没有相关变更时返回正常空结果，不把空窗口当作工具失败。

工具权限标签为 `changes:read` 和 `tenant:observe`。工作流只把经过脱敏的稳定字段投影为
`EvidenceType.CHANGE`，摘要包含变更类型、资源、版本、状态和相对 Incident 的分钟偏移，
不会把 metadata 送入 LLM 摘要。

## 数据库迁移

- `20260817_0029_create_change_events.py`：创建 Change Event 表、租户幂等唯一约束和
  `(tenant_id, service_name, started_at)` 查询索引。
- `20260817_0030_add_change_evidence_type.py`：把 `CHANGE` 加入 Evidence Check Constraint，
  同时保持 `INCIDENT_HISTORY` 与领域枚举一致。

降级 `0030` 前若数据库已有 `CHANGE` Evidence，旧约束会拒绝恢复；生产回滚必须先审计
并处理这类行，不能把迁移可生成误解为带数据回滚一定成功。

## MiniShop 验证

`deployment-regression` 场景在注入故障前通过同一 HMAC 入口写入
`payment-service v1 → v2` 成功部署，随后要求以下 Ground Truth：

```text
CHANGE + METRIC + LOG + TRACE
```

本地 Docker E2E 已验证四个场景全部通过，且普通 `payment-error` 不会被部署记录错误归因。
运行器按告警标题、OPEN 状态和本轮时间窗口精确选择 Incident，避免同服务相邻场景竞态。
该结果使用隔离 PostgreSQL、Redpanda、Prometheus、Loki、Tempo、Alertmanager 和确定性
LLM Stub，只证明本地工程闭环，不证明 Real LLM 准确率或 staging/production 可用性。

## 关键代码

- `src/devops_agent_platform/domain/models/change_event.py`
- `src/devops_agent_platform/application/services/change_event_service.py`
- `src/devops_agent_platform/interfaces/http/routes/change_events.py`
- `src/devops_agent_platform/tools/handlers/change_events.py`
- `src/devops_agent_platform/infrastructure/adapters/sqlalchemy/change_event_repository.py`
- `ops/minishop-e2e/run_e2e.py`
