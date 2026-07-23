# DevOps Agent RCA Consumer Runbook

## Consumer Stopped

**信号**：`DevOpsAgentRCAConsumerStopped`

1. 检查 Runtime 严重日志，确认 Consumer 是启动失败、异常退出还是停机流程。
2. 检查 Kafka Broker、认证、消费组和源 Topic 权限。
3. 检查死信 Producer 是否启动失败；Consumer 会在死信通道不可用时拒绝启动。
4. 恢复后确认状态回到 `RUNNING`，并验证轮询时间戳持续推进。

## Consumer Stalled

**信号**：`DevOpsAgentRCAConsumerStalled`

1. 对比最近轮询时间、最近成功时间和连续失败次数。
2. 检查当前是否有长时间运行的 Agent、工具请求或数据库事务。
3. 检查线程池、事件循环、HTTP 连接池和数据库连接池是否耗尽。
4. 禁止直接提交 Kafka Offset；先确认当前消息的工作流租约与终态。
5. 若必须重启，使用优雅停机并确认当前执行已完成或租约可安全接管。

## No Successful Poll

**信号**：`DevOpsAgentRCAConsumerNoSuccessfulPoll`

1. 查看 `retried` 与 `dead_lettered` 累计值是否持续增加。
2. 检查 Kafka 拉取、数据库抢占、权限数据源及观测平台是否可用。
3. 若轮询持续发生但没有成功，按有限范围检查最近错误类型，禁止导出消息正文。
4. 确认指数退避生效，避免依赖故障期间形成紧密重试。
5. 恢复后确认最近成功时间戳更新，并观察重试速率回落。

## Repeated Failures

**信号**：`DevOpsAgentRCAConsumerRepeatedFailures`

1. 区分空轮询、可重试消息和 Consumer 自身异常；空轮询不应增加失败次数。
2. 检查失败是否集中于单条消息，并核对其 WorkflowRun 是否仍由当前 Worker 持有。
3. 契约错误应进入死信；基础设施暂时不可用应保留源 Offset 等待重试。
4. 不要通过降低权限校验、跳过 fencing 或强制 ACK 来消除告警。
5. 修复后确认连续失败归零，并抽查 Evidence、ToolInvocation 和报告事务一致性。

## Dead Letters Increasing

**信号**：`DevOpsAgentRCAConsumerDeadLettersIncreasing`

1. 抽样死信 Topic 的 envelope 元数据，禁止把原始观测数据、证据摘要或完整消息正文导出到排障聊天。
2. 检查 `reason_code`：契约错误通常需要兼容消费者或生产者修复，缺失工作流通常表示本地事务或人工数据修复异常。
3. 对比应用版本、消息 `schema_version`、`event_type` 和 `aggregate_type`，确认当前 Consumer 是否应该处理该消息。
4. 修复代码或数据后，先在隔离环境重放样本，再决定是否人工回放死信。
5. 回放后确认 `dead_lettered` 不再增长、`acknowledged` 增长，且同一 WorkflowRun 没有生成第二次执行事实。

常见 `reason_code` 处置：

| reason_code | 含义 | 优先动作 |
| --- | --- | --- |
| `INVALID_VALUE_TYPE` | Kafka value 不是 bytes，通常来自测试适配器或错误封装 | 检查 Consumer 适配层和消息反序列化边界，禁止直接回放 |
| `INVALID_MESSAGE_SIZE` | 原始消息为空或超过消费者容量上限 | 检查 Outbox/Producer 是否发送错误载荷，确认没有把大证据内容塞进事件 |
| `INVALID_JSON` | 原始消息无法按 JSON 解码 | 检查序列化版本、压缩/编码配置和 Topic 是否混入非业务消息 |
| `MESSAGE_CONTRACT_INVALID` | JSON 可解析，但 `rca.requested` 版本、聚合或字段校验失败 | 对比 `schema_version`、`workflow_run_id`、`incident_id`、`tenant_id` 和 `operator_id` |
| `WORKFLOW_NOT_FOUND` | Outbox 引用的本地 WorkflowRun 缺失 | 核对 Outbox 与 WorkflowRun 是否同事务提交，修复前不要回放 |

回放前检查清单：

1. 确认死信 `source_topic/source_partition/source_offset` 与原消息来源一致，避免跨环境回放。
2. 确认 `dead_letter_id` 没有被重复处理；同一源 offset 只允许一次人工处置结论。
3. 对 `MESSAGE_CONTRACT_INVALID`，优先发布兼容消费者或修复生产者，不能手改死信 JSON 绕过版本校验。
4. 对 `WORKFLOW_NOT_FOUND`，先恢复 `workflow_runs`、关联 Incident 和 Outbox 审计的一致性，再在隔离环境演练。
5. 若工作流已经是 `CANCELED`、`SUCCEEDED` 或 `FAILED`，不要回放启动执行；应确认旧消息会被正常 ACK。

## Lag Unavailable

**信号**：`DevOpsAgentRCAConsumerLagUnavailable`

1. 对比已分配分区数和已测量分区数，确认是未完成再均衡还是部分分区缺少高水位。
2. 检查 Kafka FetchResponse、Broker 连接和消费组分区分配是否稳定。
3. 检查 Lag 查询是否超过短超时或触发最大分区数保护。
4. 不要把不可用快照中的零值解释为没有积压。
5. 恢复后确认 `lag_source_up=1`，且已测量分区数等于已分配分区数。

## Lag High

**信号**：`DevOpsAgentRCAConsumerLagHigh`

1. 同时查看总 Lag、最大分区 Lag、处理速率和连续失败次数。
2. 若最大分区 Lag 接近总 Lag，优先检查消息键分布和单分区热点。
3. 若全部分区同步增长，检查工具调用、模型请求和数据库事务耗时。
4. 扩容前确认 Topic 分区数足以让更多 Consumer 获得分配。
5. 禁止直接跳过 Offset 降低 Lag；恢复必须保留消息、工作流和审计一致性。
