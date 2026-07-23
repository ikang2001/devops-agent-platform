# DevOps Agent Ticket Submission Consumer Runbook

## Consumer Stopped

**信号**：`DevOpsAgentTicketSubmissionConsumerStopped`

1. 检查 Runtime 启动日志，确认 Consumer 是配置缺失、Kafka 启动失败还是运行中异常退出。
2. 核对 `DEVOPS_AGENT_TICKET_SUBMISSION_CONSUMER_ENABLED` 与工单网关配置是否同时启用。
3. 检查 Kafka Broker、消费组、源 Topic 和死信 Topic 权限。
4. 检查 HTTP JSON TicketingGateway endpoint、Bearer Token 和网络出口是否可用。
5. 恢复后确认状态回到 `RUNNING`，最近轮询时间戳持续推进。

## Consumer Stalled

**信号**：`DevOpsAgentTicketSubmissionConsumerStalled`

1. 对比最近轮询时间、最近成功时间和连续失败次数。
2. 检查是否卡在单条消息处理、数据库事务、Kafka 提交 Offset 或外部工单 HTTP 调用。
3. 查看 HTTP 连接池、数据库连接池、事件循环和 Kafka 客户端是否耗尽或阻塞。
4. 不要直接提交 Kafka Offset；先确认本地 `TicketSubmission` 是否已记录终态。
5. 如需重启，使用优雅停机，确保当前消息要么完成终态写入，要么保留 Offset 等待重试。

## No Successful Poll

**信号**：`DevOpsAgentTicketSubmissionConsumerNoSuccessfulPoll`

1. 查看 `retried`、`dead_lettered` 和 `ignored` 是否持续增长。
2. 若 `retried` 增长，优先检查外部工单网关、数据库和运行时依赖。
3. 若 `dead_lettered` 增长，检查消息契约版本、Outbox payload 和本地提交记录是否一致。
4. 若只有空轮询，确认 Topic 是否确实没有待处理消息，并检查消费组分区分配。
5. 恢复后确认最近成功时间戳更新，连续失败次数归零。

## Repeated Failures

**信号**：`DevOpsAgentTicketSubmissionConsumerRepeatedFailures`

1. 区分可重试外部故障、数据库暂态故障和 Consumer 自身异常。
2. 检查错误是否集中在 `TICKETING_GATEWAY_UNAVAILABLE`，并核对 HTTP endpoint、DNS、证书和认证。
3. 检查是否有单条消息反复失败；如果有，按 `ticket_submission_id` 查询本地状态和审计事件。
4. 不要为了清告警把失败消息强制 ACK；这会造成外部工单缺失且难以审计。
5. 修复后观察退避是否回落、成功轮询时间是否推进、提交终态是否只写入一次。

## Dead Letters Increasing

**信号**：`DevOpsAgentTicketSubmissionDeadLettersIncreasing`

1. 抽样死信 Topic 的 envelope 元数据，禁止把完整工单内容导出到排障聊天或工单备注。
2. 检查 `reason_code`：契约错误通常需要发布兼容修复，缺失本地记录通常需要核对 Outbox 同事务写入。
3. 对比应用版本和消息 `schema_version`，确认消费者是否支持该版本。
4. 修复代码或数据后，先在隔离环境重放样本，再决定是否人工回放死信。
5. 回放前确认幂等键仍有效，避免重复创建外部工单。

常见 `reason_code` 处置：

| reason_code | 含义 | 优先动作 |
| --- | --- | --- |
| `INVALID_MESSAGE_SIZE` | 原始消息为空或超过消费者容量上限 | 检查上游 Outbox/Producer 是否发送了错误载荷，禁止直接回放 |
| `INVALID_JSON` | 原始消息无法按 JSON 解码 | 检查序列化版本、压缩/编码配置和 Topic 是否混入非本业务消息 |
| `MESSAGE_CONTRACT_INVALID` | JSON 可解析，但事件类型、版本、字段或本地事实校验失败 | 对比 `schema_version`、`ticket_submission_id`、`ticket_draft_id`、`workflow_run_id` 和 `draft_version` |
| `TICKET_SUBMISSION_NOT_FOUND` | Outbox 引用的本地提交请求缺失 | 核对数据库事务、数据修复记录和是否发生手工删除，修复前不要回放 |

回放前检查清单：

1. 确认死信 `source_topic/source_partition/source_offset` 与原消息来源一致，避免回放到错误环境。
2. 确认 `dead_letter_id` 没有被重复处理；同一源 offset 只允许一次人工处置结论。
3. 对 `MESSAGE_CONTRACT_INVALID`，优先做兼容消费者或数据修复，不能通过改死信 JSON 绕过版本校验。
4. 对 `TICKET_SUBMISSION_NOT_FOUND`，先恢复本地 `ticket_submissions` 和关联 `ticket_drafts` 的一致性，再在隔离环境演练。
5. 回放后观察 `dead_lettered` 不再增长，`acknowledged` 增长，外部工单网关没有重复创建同一业务工单。

## Lag Unavailable

**信号**：`DevOpsAgentTicketSubmissionConsumerLagUnavailable`

1. 检查 Consumer 是否已分配分区；没有分区时通常是消费组 rebalance、实例数超过分区数或订阅 Topic 错误。
2. 检查 Kafka 客户端是否能读取 `position` 和 `highwater`，确认 Broker 权限、网络和认证没有阻断元数据查询。
3. 对比 `assigned_partitions` 与 `measured_partitions`，如果已分配但未测量，优先检查单个分区 position 查询异常。
4. 不要把 Lag 不可用当成业务空闲；先恢复监控数据源，再判断是否需要扩容或回放消息。
5. 恢复后确认 `lag_source_up=1`，且 `assigned_partitions` 与 `measured_partitions` 一致。

## Lag High

**信号**：`DevOpsAgentTicketSubmissionConsumerLagHigh`

1. 先确认 Consumer 状态是否 `RUNNING`，最近成功轮询时间是否持续推进。
2. 如果 `retried` 同时增长，优先排查数据库、外部工单网关和可重试异常原因。
3. 如果 `dead_lettered` 同时增长，按死信 Runbook 抽样检查消息契约，不要直接扩大 Consumer 数量掩盖坏消息。
4. 如果处理速率正常但 Lag 仍增长，检查上游 Outbox 发布速率是否超过当前 Consumer 容量。
5. 扩容前确认 Topic 分区数足够；实例数超过分区数不会继续提升消费并发。
