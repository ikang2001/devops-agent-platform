# Step 6 产品化资产

本目录描述建立在 Step 4 后端和 Step 5 部署资产之上的治理与产品层。

## 运行时边界

| 类别 | 内容 |
|---|---|
| 已接入运行时并可测试 | `/console`、不可变 RCA feedback API/storage、single-feedback read-only evaluation-candidate export、Workspace 管理 API、HTTP MCP Adapter、平台 remediation plan API、MiniShop 白名单修复沙箱、在线 Dataset Release 双审核状态机 |
| 离线可测试（Implemented and testable offline） | 显式审批的候选策展、静态评测 runner、fail-closed 基线/候选报告门禁，只有 eligible for human release review 才能继续 |
| 示例/蓝图（Example / blueprint only） | Prompt Registry YAML、Feature Flag YAML、评测夹具、RAG 治理文档 |

`src/` 不会加载示例 Prompt Registry、Feature Flags 或 RAG 文档（Not loaded by runtime）。反馈持久化、只读候选导出、Workspace API 和在线 Dataset Release 审核/发布状态机是真实运行时能力，并有单元/API 测试。Automatic curation、自动匿名化、定时评测、组织级发布和 Prompt/Flag 灰度仍未接线；导出的候选仍带 `review_required: true`。

## 文件说明

- `console-workflows.md`：Ops Console 页面和工作流边界。
- `feedback-evaluation-loop.md`：单条反馈候选导出及人工策展要求。
- `prompt-registry.example.yml`、`feature-flags.example.yml`：版本化治理示例，运行时不加载。
- `evaluation-dataset.example.yml`、`evaluation-candidate.example.json`：非敏感评测和候选示例。
- `evaluation-curation-review.example.yml`：策展器使用的 synthetic 隐私复核输入。
- `curate_evaluation_candidate.py`：fail-closed 离线策展器，人工改写后创建不可覆盖的新数据集文件。
- `publish_evaluation_dataset.py`：离线双审核发布器，校验 domain/privacy 两个不同审核人和来源 SHA-256。
- `evaluation-release-approvals.example.yml`：离线发布审核清单示例。
- `run_evaluation.py`、`compare_evaluation_reports.py`：离线评测与基线/候选门禁，不调用 LLM。
- `rag-governance.md`：知识库与历史事件治理规则；runtime 当前是 lexical/SQL 检索，不是向量 RAG 产品。
- `remediation-approval.md`：高风险修复审批、回滚和 MiniShop 沙箱边界。

在线 Dataset Release API 的入口、状态机和幂等/审计约束见：
`src/devops_agent_platform/interfaces/http/routes/dataset_releases.py`、
`src/devops_agent_platform/application/services/dataset_release_service.py`。

## 对外说明边界

可以说明：Ops Console、反馈持久化、只读候选导出、显式离线策展、报告门禁、Workspace API、HTTP MCP、Jira/ServiceNow 适配器代码、平台 remediation 计划、固定 HTTP 控制器、MiniShop 修复沙箱和在线双审核 Dataset Release 已在本地实现并测试。

仍然不要宣称：

- 真实外部 Provider、Ticketing 或 MCP 已交付；
- 通用生产自动修复；
- Prompt Registry、Feature Flag 或向量 RAG 产品已接线；
- 反馈自动学习闭环、自动匿名化或定时评测已上线；
- 组织级生产数据集治理已经完成。

reference staging 的本地报告仍必须标注 `synthetic=true`、`production_acceptance=false`，目标环境签字和运行时接线通过后才能升级结论。
