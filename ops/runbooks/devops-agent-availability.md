# DevOps Agent Availability Runbook

## Instance Down

**信号**：`DevOpsAgentInstanceDown`

1. 在部署平台确认实例是否重启、驱逐、OOM 或探针失败。
2. 检查同一 `job` 的其他实例，判断是单实例故障还是整体故障。
3. 查看实例退出前日志，按 `trace_id`、异常类型和时间窗口定位根因。
4. 检查 `/healthz`；若进程存活，再检查 `/readyz` 和数据库网络。
5. 仅在确认进程不可恢复后执行滚动重启，禁止同时重启全部副本。

**升级条件**：两个以上实例不可用，或业务入口已无健康副本时，立即升级为
平台 P1 事故。

## Runtime Component Down

**信号**：`DevOpsAgentRuntimeComponentDown`

1. 根据 `component` 确定是 Runtime 还是数据库不可用。
2. 查询 `/readyz` 和 `/metrics`，确认状态持续时间与影响实例范围。
3. 数据库异常时检查连接数、慢查询、锁等待、网络和最近变更。
4. Runtime 异常时检查启动回滚、Worker 退出和资源关闭日志。
5. 不要通过放宽 readiness 掩盖依赖故障；恢复依赖后确认实例自动重新就绪。

## High Error Ratio

**信号**：`DevOpsAgentHighErrorRatio`

1. 在 Dashboard 按 `status_class`、路由和发布时间确认错误开始时间。
2. 检索同一时间窗口的 `ERROR` 日志，按异常类型聚合，避免逐条阅读。
3. 对照最近部署、数据库迁移、Kafka 和第三方依赖状态。
4. 若错误由新版本引入，优先停止继续发布并按变更策略回滚。
5. 回滚后持续观察两个记录窗口，确认5xx比例恢复。

## High P95 Latency

**信号**：`DevOpsAgentHighP95Latency`

1. 对比请求量、P50/P95/P99，区分整体变慢与少量长尾。
2. 检查数据库连接池等待、慢查询、锁竞争和外部依赖超时。
3. 检查 CPU、内存、事件循环阻塞和 Worker 是否争用同一进程资源。
4. 优先限制高成本入口或扩容，不要直接无限增大超时时间。
5. 记录变更前后延迟分位数，确认缓解措施真实生效。
