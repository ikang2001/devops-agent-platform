# Step 5：测试验证

## 1. 测试目标

测试不是只证明接口能跑，而是证明故障靶场具备演示闭环所需的关键行为。

## 2. 测试用例

| 类型 | 场景 | 预期 |
|---|---|---|
| 正常流程 | 调用 `/checkout` | 返回订单、支付、通知和 trace_id |
| 支付故障 | 开启 payment error | `/payment/pay` 返回 500 |
| 库存故障 | 开启 inventory db timeout | `/checkout` 返回下游失败 |
| 延迟故障 | 开启 checkout latency | `/checkout` 响应时间升高 |
| 故障恢复 | 调用 `/faults/reset` | `/faults` 返回空列表 |
| 指标验证 | 调用 `/metrics` | 包含约定指标名 |
| Agent 接入 | 构造告警 payload | 包含 `external_event_id` 和兼容字段 `fingerprint` |

## 3. 运行命令

```powershell
cd "C:\Users\jwk\Desktop\llm\DevOps 智能排障 Agent 平台\MiniShop 电商下单故障演练靶场"
python -m pip install uv==0.11.31
uv sync --locked --extra dev
uv run pytest
```

## 4. 排错思路

如果测试失败，先看响应状态码，再看响应体里的 `error_code`，最后看控制台 JSON 日志里的 `trace_id` 和 `fault_type`。不要直接改代码，要先确认是哪一层行为不符合预期。
