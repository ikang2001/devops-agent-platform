# Load / Chaos Harness

`python -m ops.load.run_load --output artifacts/load-plan.json` 会生成 100、500、1000 alerts/min 三档合同计划。没有目标地址时，HTTP P95、数据库连接池、Kafka Lag、CPU、内存等字段保持 `null`，不会伪造数字。

`python -m ops.chaos.harness` 运行四个合同场景：Worker 崩溃、Kafka 短暂不可用、PostgreSQL 短暂不可用和观测工具超时。合同模式只验证结构和安全不变量，耗时保持 `null`。

## Reference staging 真实容器注入

reference Compose 启动后，可执行：

```powershell
uv run python ops/chaos/run_reference_chaos.py `
  --output-directory ops/chaos/artifacts/reference-20260818
```

脚本会真实停止/杀掉 reference 容器，等待状态 API 暴露 `RUNNING` 或新的 execution attempt，再采集恢复时间、Outbox、状态损坏、partial 报告和旧代次 fence 探针。输出中的 `synthetic=true`、`production_acceptance=false` 是强制 provenance；它不是外部 staging 或生产签字。

最终示例证据：`ops/chaos/artifacts/reference-20260818/chaos-report.json`。
