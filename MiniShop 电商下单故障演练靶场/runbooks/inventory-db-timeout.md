# Inventory 数据库超时排查手册

## 故障现象

- `/checkout` 返回 `CHECKOUT_DOWNSTREAM_FAILURE`。
- `inventory-service` 日志出现 `DB_TIMEOUT`。
- 指标 `minishop_inventory_db_timeout_total` 增长。

## 可能原因

- inventory 数据库访问超时。
- 数据库连接池耗尽。
- 慢查询或锁等待。
- 演练环境中开启了 inventory db timeout fault。

## 推荐查询

- 查看 `minishop_fault_enabled{service_name="inventory-service",fault_type="db_timeout"}`。
- 查看 `minishop_inventory_db_timeout_total`。
- 查询 `error_code=DB_TIMEOUT` 的日志。
- 通过 `trace_id` 串联 checkout 和 inventory 日志。

## 处理建议

- 演练环境可调用 `/faults/reset` 关闭故障。
- 真实环境应检查数据库连接数、慢查询、锁等待和连接池配置。
