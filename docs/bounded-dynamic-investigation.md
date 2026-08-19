# Bounded Dynamic Investigation

`bounded_dynamic_v1` 已接入 RCA Consumer Runtime；固定策略仍保持兼容。模型只能返回 `StepDecision`（工具意图），不能携带可执行 SQL、Shell 或任意查询。`InvestigationPolicyValidator` 在后端校验 allowlist、只读风险、注册版本、操作者、服务拓扑范围、重复调用和预算，并从 Incident 状态生成真实 payload。

启动时使用 `DEVOPS_AGENT_RCA_INVESTIGATION_POLICY=bounded_dynamic_v1`，并通过 `rca_dynamic_*`
Settings 控制最大步骤、总时长、单工具调用次数、Evidence 数量和 LLM 次数。Runtime 会注册
Metrics、Logs、Traces、Change、Topology、Knowledge、Runbook 七个只读工具；Planner 只决定
下一步意图，执行层复用 `ControlledAgentWorkflow` 的权限、超时、审计、Evidence 和脱敏边界。

`InvestigationState` 记录已完成/失败步骤、Evidence、候选根因、工具次数、LLM 次数和 checkpoint 版本。
Runtime 使用 `SQLAlchemyInvestigationCheckpoint` 持久化状态，按 `(tenant_id, incident_id, checkpoint_version)`
拒绝过期 Worker 覆盖；`InMemoryInvestigationCheckpoint` 仅保留给快速单元测试。跨 Worker 的生产恢复
仍需目标环境故障注入，并复用现有 WorkflowRun Lease/Heartbeat/Fence，不能把本地测试当作生产证据。

没有 Evidence、Planner 失败或预算耗尽时不会生成假 RCA，结果只能是未确定/部分报告。Topology
或 Knowledge 失败会保留已收集 Evidence 并降低报告置信度。该实现是本地可验证策略引擎；真实
LLM、多 Worker 恢复和生产观测栈仍需外部验收。
