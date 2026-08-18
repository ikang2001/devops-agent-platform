# MCP Adapter 与 Workspace

MCP 调用路径固定为 Tool Intent → ToolRegistry → Permission/Risk/Schema/Timeout → `MCPToolAdapter` → Approved Server。未知 Server 默认拒绝，写/高风险工具拒绝，结果有大小边界、敏感字段脱敏和审计事件。仓库提供 `DemoReadonlyMCPServer` 作为本地合同测试。

Workspace 建立在 tenant 之上，保存 Prometheus/Loki/Tempo target、Knowledge scope、Investigation policy、允许工具、LLM provider policy 和 retention；不会重写已有租户体系。Workspace 已有 SQLAlchemy repository、租户隔离查询和迁移测试。
