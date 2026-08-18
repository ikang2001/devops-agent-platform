# Bounded Dynamic Investigation

`bounded_dynamic_v1` 保留固定策略兼容性，模型只能返回 `StepDecision`（工具意图），不能携带可执行 SQL、Shell 或任意查询。`InvestigationPolicyValidator` 在后端校验 allowlist、只读风险、注册版本、操作者、服务拓扑范围、重复调用和预算，并从 Incident 状态生成真实 payload。

`InvestigationState` 记录已完成/失败步骤、Evidence、候选根因、工具次数、LLM 次数和 checkpoint 版本。`InMemoryInvestigationCheckpoint` 与 `serialize/deserialize` 支持 Worker 崩溃后的安全恢复；线上接入应复用现有 WorkflowRun Lease/Heartbeat/Fence。

没有 Evidence 或预算耗尽时不会生成假 RCA，结果只能是未确定/部分报告。该实现是本地可验证策略引擎，不等同于已连接真实 LLM 的生产动态 Agent。
