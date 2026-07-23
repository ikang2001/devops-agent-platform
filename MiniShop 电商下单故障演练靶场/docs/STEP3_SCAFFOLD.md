# Step 3：代码骨架

## 1. 目录结构

```text
app/
  main.py
  config.py
  models.py
  services.py
  faults.py
  metrics.py
  logging.py
  routers/
tests/
runbooks/
prometheus/
alertmanager/
scripts/
docs/
```

## 2. 核心对象

| 对象 | 作用 |
|---|---|
| CheckoutRequest | 下单入参 |
| PaymentRequest | 支付入参 |
| InventoryRequest | 库存入参 |
| FaultControlRequest | 故障控制入参 |
| FaultRecord | 故障状态记录 |
| FaultState | 内存故障仓储 |

## 3. 方法边界

| 方法 | 职责 |
|---|---|
| `checkout` | 编排库存、支付、通知 |
| `reserve_inventory` | 模拟库存扣减和数据库超时 |
| `pay` | 模拟支付成功或 500 |
| `send_notification` | 模拟通知发送 |
| `FaultState.enable` | 开启故障 |
| `FaultState.reset` | 清空故障 |

## 4. 骨架设计原则

路由层只负责接收请求，业务编排放在 service 层，故障状态放在 faults 层，指标和日志作为公共能力独立出来。这样后续拆微服务时，可以把对应 service 和 router 平移出去。
