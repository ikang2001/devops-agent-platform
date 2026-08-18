# Load / Chaos Harness

`ops/load/run_load.py` 输出 100、500、1000 alerts/min 的执行计划，未连接真实目标时所有性能观测保持 `null`。`ops/chaos/harness.py` 输出 Worker 崩溃、Kafka、PostgreSQL 和观测超时四个本地控制流用例，包含恢复时间、丢失工作流、重复完成、Fence 拒绝和 Outbox 恢复字段。

这些脚本用于验证报告契约和恢复路径，不能替代 staging 签字、真实 Kafka Lag、数据库池、CPU/内存或 LLM 成本数据。
