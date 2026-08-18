# Historical Knowledge Retrieval

知识文档分为 Historical Incident、Postmortem、Runbook、Known Error 和 Resolution Note，并有 Draft/In Review/Approved/Published/Archived 生命周期。在线检索只接受 `APPROVED` 或 `PUBLISHED`，且严格按 tenant 过滤。

第一版使用 lexical 词项、服务匹配和 error fingerprint 加权；内存和 SQLAlchemy 检索适配器
均只返回 `APPROVED` / `PUBLISHED` 文档。结果以 `KNOWLEDGE` 参考证据返回，明确标注“历史参考，
不是本次事实”，不会把历史结果直接升级为 `CONFIRMED`。`KnowledgeRetrieverPort` 与
`EmbeddingPort` 为后续 FTS/vector 接入保留边界。
