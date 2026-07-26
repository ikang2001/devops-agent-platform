# 路线 A：诚实叙事改造

## 目标

把仓库对外表述从“已产品化的自适应智能排障 Agent”纠正为：

**生产导向的 Incident / 受控 RCA 作业编排平台（固定计划 + 治理边界）**

避免面试与 README 夸大：自适应多轮、Prompt/Feature Flag/RAG 已上线、通用自动修复等。

## 改了什么

| 文件 | 调整 |
| --- | --- |
| `README.md` | 标题与定位改为 Controlled RCA Pipeline；Step5/6 用诚实边界表 |
| `STEP6_PRODUCTIZATION.md` | 拆成“已实现”与“Example Or Blueprint Only / Not Loaded By Runtime” |
| `缺少内容.md` | 完成度单一真相源：已实现 / 蓝图 / 仍需 / 禁止话术 |
| `ops/product/*` | 标注示例 YAML、治理草案未接线 |
| `项目面试文档.md` | 一句话、30s/1min/3min、禁止话术与 Step5/6 问答 |
| `tests/unit/ops/test_step5_step6_assets.py` | 断言边界短语，不再断言“local product loop implemented” |

## 为什么

代码侧强项是：Outbox、租约、幂等、脱敏、固定只读工具链、审批边界。
弱项是：默认固定四步、LLM 只做候选摘要、Step6 多数资产未进 `src/` 运行时。

叙事必须跟代码一致，否则面试追问会穿帮。

## 验收

- 资产测试通过，且包含 `Example Or Blueprint Only` / `Not Loaded By Runtime` 等边界串
- `缺少内容.md` 与 README / STEP6 / 面试文档不互相矛盾
- 禁止话术列表可被面试文档直接引用

## 明确不做什么

- 不借路线 A “补实现” Prompt Registry / Feature Flags / RAG
- 不把本地 E2E 说成生产验收
- 不把固定计划包装成自适应 Agent

## 相关后续

- 路线 B：修复执行租约 → [`route-b-remediation-lease.md`](route-b-remediation-lease.md)
- 路线 C0/C1：部分失败降级与可配置调查策略（默认仍非自由 Agent）
