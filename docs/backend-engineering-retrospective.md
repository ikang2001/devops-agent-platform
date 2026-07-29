# 后端工程化开发复盘汇总

> 随 MiniShop 端到端 RCA 闭环开发持续更新，只记录真实发生或有证据支持的事件。

## 1. 基本信息

| 项目 | 内容 |
|---|---|
| 项目/需求 | MiniShop 到 DevOps Agent 的端到端 RCA、场景 Manifest 与代码导读 |
| 技术栈与环境 | Python 3.12、FastAPI、PostgreSQL、Kafka/Redpanda、Prometheus、Loki、Tempo、Docker Compose |
| 开始时间 | 2026-07-18 |
| 完成时间 | 2026-07-29 |
| 当前结论 | 三场景 Docker RCA 闭环、A/B/C0/C1 路线、反馈候选下载、显式离线策展和评测报告门禁均已完成本地验证 |
| 相关版本/提交 | 独立 Git 仓库 `main` 基线，提交信息见本仓库 `git log` |

## 2. 结果摘要

- 交付内容：三份机器可读 Manifest/Ground Truth、MiniShop 可观测与告警接入、
  真实 Compose RCA 验收、演练管理员认证、逐模块代码导读，以及按单条人工反馈
  导出机器可读评测候选，以及要求显式批准、隐私复核、人工改写和 Evidence
  重映射的离线策展工具，以及只允许进入人工发布评审的离线评测报告门禁。
- 已完成验证：平台全量 `1711 passed, 9 skipped`；MiniShop 全量
  `37 passed`（均使用 `-W error`）；E2E 静态资产 `3 passed`；Compose 配置展开通过；三场景真实
  Docker E2E 全部通过。
- 端到端证据：`checkout-latency`、`inventory-db-timeout`、
  `payment-error` 均为 `SUCCEEDED`，且具备 METRIC、LOG、TRACE、RUNBOOK
  四类 Evidence。
- 遗留风险：真实生产控制器、外部凭据和目标环境验收不在仓库中；本地测试已在 `-W error` 下通过。
- 工程基线：根平台与 MiniShop 分别提交 `uv.lock`，CI 和镜像均使用
  `uv 0.11.31` 的 locked 模式，Ruff 固定为 `0.15.22`；项目已建立独立
  Git `main` 基线。

## 3. 阶段摘要

| 阶段 | 完成内容 | 验证证据 | 新增事件数 | 遗留事项 |
|---|---|---|---:|---|
| Step 1 需求分析 | 定义闭环、Manifest、教程三项交付及成功标准 | 现有源码、配置和两套测试基线 | 1 | 无 |
| Step 2 架构设计 | 选择原生 Alertmanager Relay、兼容 Telemetry、演练专用认证和 Compose 验收 | 现有端口/适配器边界 | 1 | 无 |
| Step 3 代码骨架 | 新增 Alertmanager Mapper、Relay、Agent Alert Client 与路由 | Relay 定向测试 6 项通过 | 1 | 无 |
| Step 4 增量实现 | Alertmanager Relay、兼容指标/日志、分服务 Trace、Tempo Ground Truth、演练认证、代码导读和固定调查策略接线 | C0/C1 定向测试 `190 passed`；MiniShop `37 passed` | 2 | 无 |
| Step 5 测试排错 | 修复 FastAPI 生命周期、Buildx 路径、tmpfs 权限、Tempo Trace ID、锁定环境打包和策略解析测试假设 | 平台当前 `1711 passed, 9 skipped`；MiniShop `37 passed` | 7 | 无 |
| Step 6 整合运维 | 真实 Compose 三场景验收、锁定镜像验证、独立 Git 基线、平台受审修复、reclaim Worker、C0/C1 文档收口、反馈候选下载、离线策展、静态评测、报告门禁和版本发布守护 | `artifacts/results.json` 中 `passed: true`；反馈/策展/评测/版本定向回归与全量 pytest 通过 | 24 | 无 |

## 4. 事件索引

| ID | 阶段 | 类型 | 标题 | 状态 | 结论 |
|---|---|---|---|---|---|
| DEV-001 | Step 1 | 踩坑 | 当前目录没有独立 Git 历史 | 已确认 | 不执行教程技能要求的自动 commit，避免纳入上级无关文件 |
| DEV-002 | Step 2 | 难点 | 演练认证不能削弱生产 OIDC 边界 | 已解决 | 采用默认关闭且生产环境强制拒绝的演练认证适配器 |
| DEV-003 | Step 3 | 犯错 | 首个组合补丁上下文匹配失败 | 已解决 | 拆分为新增文件和小范围更新补丁，验证没有部分写入 |
| DEV-004 | Step 4 | 踩坑 | 多文件补丁不会自动创建缺失父目录 | 已解决 | 先创建 E2E 目录，再继续小批次补丁 |
| DEV-005 | Step 5 | 难点 | FastAPI 移除 add_event_handler 导致测试收集失败 | 已解决 | 改用 lifespan 关闭 Trace Provider，MiniShop 37 项全绿 |
| DEV-006 | Step 5 | 踩坑 | 默认 Python 环境未安装项目开发依赖 | 已解决 | 按两个项目各自的 `.[dev]` 声明安装并完成全量回归 |
| DEV-007 | Step 5 | 难点 | Buildx Bake 无法处理非 ASCII 工作区会话头 | 已解决 | E2E 脚本固定使用经典构建器，保留中文工作区 |
| DEV-008 | Step 5 | 难点 | 非 root 容器无法写入默认 tmpfs | 已解决 | 为各镜像按实际 UID/GID 声明 tmpfs 所有权 |
| DEV-009 | Step 5 | 难点 | Tempo 搜索返回省略前导零的 Trace ID | 已解决 | 有界接受并规范化 16/128 位 Trace ID，补回归测试 |
| DEV-010 | Step 6 | 踩坑 | Git 中文路径默认转义破坏 PowerShell 审计 | 已解决 | 独立仓库关闭 `core.quotepath` 后重新执行候选文件审计 |
| DEV-011 | Step 5 | 难点 | 锁定环境暴露根项目缺少显式打包元数据 | 已解决 | 补 `build-system` 与 `src` 包发现，升级资产测试为 locked 门禁 |
| DEV-012 | Step 6 | 难点 | 并发控制台实现产生重复静态资源边界 | 已解决 | 保留 `static/ops-console` 作为唯一资源目录并用控制台 API 测试兜底 |
| DEV-013 | Step 5 | 犯错 | 供应商适配器首轮静态检查命中行宽 | 已解决 | 按 Ruff 精确位置重排表达式，不改变业务语义 |
| DEV-014 | Step 5 | 犯错 | 根项目虚拟环境不能代替 MiniShop 锁定环境 | 已解决 | 改用 MiniShop 独立 `uv run --locked` 测试 |
| DEV-015 | Step 5 | 难点 | httpx2 的 Python 下限与 MiniShop 声明范围冲突 | 已解决 | 使用 Python marker 保留 3.9 锁解析兼容 |
| DEV-016 | Step 5 | 犯错 | RCA 反馈测试夹具漏传报告执行代次 | 已解决 | 补齐共享测试夹具的 `execution_attempt` |
| DEV-017 | Step 6 | 犯错 | 并发增量产生两套 remediation 模型和重复标识符 | 已解决 | Ruff F811 捕获后统一保留单套领域模型与仓储映射 |
| DEV-018 | Step 6 | 犯错 | 迁移数量精确断言在新增计划表后失效 | 已解决 | 将迁移数量从 27 更新为 28，并避免脆弱的精确数量契约扩散 |
| DEV-019 | Step 6 | 犯错 | 新增文本 dataclass 后旧测试硬编码 28 导致全量失败 | 已解决 | 改为动态扫描并保留下限断言 |
| DEV-020 | Step 6 | 难点 | 修复风险、回滚和效果不能来自 HTTP 正文 | 已解决 | 改为部署控制的 JSON 动作目录，创建请求只收动作键、目标和证据 |
| DEV-021 | Step 6 | 犯错 | OIDC `nbf` 负向测试使用收集期时间导致全量运行后失效 | 已解决 | 改为测试执行时动态生成未来时间 |
| DEV-022 | Step 4 | 犯错 | 新增调查策略导出后导入顺序未通过 Ruff | 已解决 | 调整公共导入顺序并通过定向 Ruff |
| DEV-023 | Step 5 | 犯错 | 调查策略测试误判解析 API 的空白规范化语义 | 已解决 | 统一复用解析器并拆分 API/Settings 边界测试 |
| DEV-024 | Step 6 | 踩坑 | 全仓 Ruff 捕获 Remediation 接线中的超长中文注释 | 已解决 | 拆分注释并通过全仓 Ruff |
| DEV-025 | Step 6 | 犯错 | 首轮 Runtime 增量把审计 Worker done callback 放进相邻条件块 | 已解决 | 复读完整启动段并补独立 Worker 崩溃隔离测试 |
| DEV-026 | Step 6 | 难点 | 两类 stale 查询会让单轮真实回收量达到配置上限两倍 | 已解决 | 交错候选并按整轮总 batch size 截断 |
| DEV-027 | Step 6 | 犯错 | 反馈评测导出测试新增领域模型导入时未保持 Ruff 排序 | 已解决 | 仅调整导入顺序，业务测试无需修改 |
| DEV-028 | Step 6 | 犯错 | 评测候选查询与 HTTP 路径的内部空白校验不一致 | 已解决 | 应用查询补齐全空白拒绝，防止非 HTTP 调用绕过身份边界 |
| DEV-029 | Step 6 | 犯错 | 收尾只读审计使用了错误参数和未确认文件路径 | 已解决 | 改用受支持参数并先通过 `rg --files` 确认路径 |
| DEV-030 | Step 6 | 犯错 | 新增失败关闭测试漏导入异常类型 | 已解决 | 补 `ResourceNotFound` 导入并由 Ruff 守门 |
| DEV-031 | Step 6 | 犯错 | 策展设计检索使用未转义正则和猜测路径 | 已解决 | 停止复合正则并先用 `rg --files` 确认真实路径 |
| DEV-032 | Step 6 | 犯错 | 首个策展工具补丁中的换行参数破坏补丁语法 | 已解决 | 核对无部分落盘后拆分小补丁并移除非必要参数 |
| DEV-033 | Step 6 | 犯错 | 策展工具 `Iterator` 导入不符合 Ruff UP035 | 已解决 | 改从 `collections.abc` 导入并通过最窄门禁 |
| DEV-034 | Step 6 | 犯错 | 产品文档换行破坏精确字符串守护 | 已解决 | 统一 `Automatic curation` 短语并重跑资产测试 |
| DEV-035 | Step 6 | 踩坑 | 临时 CLI 验证批次因包含清理操作被策略拒绝 | 已解决 | 去掉清理步骤，以唯一临时路径完成只读结果验证 |
| DEV-036 | Step 6 | 犯错 | 最终审计再次使用未转义复合正则 | 已解决 | 改用 `Select-String -SimpleMatch` 完成审计 |
| DEV-037 | Step 6 | 犯错 | 评测门禁文档改写破坏治理术语守护 | 已解决 | 恢复 `Feature Flags` 明确表述并重跑产品资产组合回归 |
| DEV-038 | Step 6 | 犯错 | 报告门禁误拒绝 runner 的缺失响应结果 | 已解决 | 识别全组件失败的缺失响应语义并补 runner→门禁回归测试 |
| DEV-039 | Step 6 | 犯错 | GitHub Release 审计使用不受支持字段 | 已解决 | 改用当前 `gh` 支持字段确认 v0.3.0 Release 已发布 |
| DEV-040 | Step 6 | 踩坑 | 发布产物校验误拒绝 uv 生成的隐藏文件 | 已解决 | 忽略隐藏管理文件，继续严格校验两个非隐藏构建产物 |

## 5. 事件详情

### DEV-001：当前目录没有独立 Git 历史

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-18 / Step 1 |
| 模块 | 工作区与文档流程 |
| 分类 | 踩坑 |
| 状态 | 已确认 |
| 背景与目标 | 逐模块教程技能要求 spec、plan、正文逐阶段提交 |
| 现象与证据 | `git status --short --branch` 显示上级仓库 `No commits yet on main`，并包含多个项目级未跟踪目录 |
| 影响 | 自动提交可能把用户无关文件纳入历史，且无法取得可靠 HEAD 锚点 |
| 排查过程 | 核对工作目录、Git 状态和教程技能硬门禁 |
| 根因 | 当前项目目录不是具有独立 HEAD 的 Git 工作树 |
| 解决方案 | 不创建提交；使用单文件代码导读作为安全降级，并在最终结果中明确说明 |
| 涉及位置 | `docs/`、上级 Git 工作树 |
| 验证证据 | 未执行任何 `git add` 或 `git commit` |
| 残余风险 | 教程源码链接不能锚定不可变 commit |
| 预防措施 | 后续为本项目初始化独立仓库后，再启用 commit 锚定的教程维护流程 |

### DEV-002：演练认证不能削弱生产 OIDC 边界

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-18 / Step 2 |
| 模块 | 管理员认证与 RCA 启动 |
| 分类 | 难点 |
| 状态 | 已解决 |
| 背景与目标 | 自包含 E2E 需要管理员配置工具权限、发布 Runbook 并启动 RCA |
| 现象与证据 | 现有管理 API 默认 fail-closed，完整 OIDC 又需要额外身份服务 |
| 影响 | 直接绕过认证会破坏已有安全边界；引入完整 IdP 会显著增加演练复杂度 |
| 排查过程 | 检查 `AdministratorAuthenticatorPort`、OIDC 装配和生产配置校验 |
| 根因 | 演练需要受控管理员身份，但当前只有生产 OIDC 适配器 |
| 解决方案 | 新增显式开关的演练认证器：默认关闭、固定租户、常量时间 Token 比较、生产环境禁止、与 OIDC 互斥 |
| 涉及位置 | `infrastructure/auth`、`Settings`、`bootstrap/runtime.py` |
| 验证证据 | 演练认证器、Settings 与 Runtime 定向测试 `173 passed`；Ruff 静态检查通过 |
| 残余风险 | 只允许用于本地演练，不能进入生产配置 |
| 预防措施 | 启动期组合校验、弱密钥拒绝、生产环境硬拒绝和 README 醒目标识 |

### DEV-003：首个组合补丁上下文匹配失败

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-18 / Step 3 |
| 模块 | MiniShop Alertmanager Relay |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 背景与目标 | 一次补丁同时新增 Relay 文件并修改配置、入口、依赖和脚本 |
| 现象与证据 | `apply_patch verification failed`，未匹配 `pyproject.toml` 依赖段 |
| 影响 | 第一轮补丁未落地，未造成代码污染 |
| 排查过程 | 使用 `Test-Path` 核实新文件均不存在，确认补丁整体回滚 |
| 根因 | 把多个独立文件编辑绑定在一个大补丁中，任一上下文差异都会使整体失败 |
| 解决方案 | 先新增独立文件，再按配置、入口、依赖分别应用小补丁 |
| 涉及位置 | MiniShop `app/`、`pyproject.toml`、`scripts/send_agent_alert.py` |
| 验证证据 | Relay 定向测试 `6 passed` |
| 残余风险 | 无代码残留；后续仍需全量测试 |
| 预防措施 | 跨多个既有文件时优先使用小补丁，每组修改后立即运行定向验证 |

### DEV-004：多文件补丁不会自动创建缺失父目录

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-19 / Step 4 |
| 模块 | `ops/minishop-e2e` 运维资产 |
| 分类 | 踩坑 |
| 状态 | 已解决 |
| 现象与证据 | 首批 `apply_patch` 已创建 MiniShop Dockerfile，但因 `ops/minishop-e2e` 不存在而在后续文件处失败 |
| 影响 | 补丁发生部分落盘，若直接重放会与已创建 Dockerfile 冲突 |
| 排查过程 | 分别用 `Test-Path` 检查 Dockerfile 和目标目录，确认前者存在、后者不存在 |
| 根因 | 补丁工具能新增文件，但不会为后续文件自动创建缺失父目录 |
| 解决方案 | 先创建 `ops/minishop-e2e/artifacts` 父目录，再重放不含 Dockerfile 的剩余补丁 |
| 验证证据 | `docker compose ... config --quiet` 返回 0 |
| 预防措施 | 新增多层目录时先显式建立目录，并在补丁失败后检查每个目标是否部分落盘 |

### DEV-005：FastAPI 生命周期 API 兼容回归

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-19 / Step 5 |
| 模块 | MiniShop OpenTelemetry 生命周期 |
| 分类 | 难点 |
| 状态 | 已解决 |
| 现象与证据 | MiniShop pytest 在收集 3 个模块时均报 `FastAPI` 不存在 `add_event_handler` |
| 影响 | 新增 Trace 关闭逻辑使整个 MiniShop 测试集无法收集 |
| 排查过程 | 错误稳定指向 `app/main.py:18`，确认当前 FastAPI 版本已要求 lifespan 模式 |
| 根因 | Trace 初版沿用了已移除的事件注册 API |
| 解决方案 | 使用 `asynccontextmanager` lifespan，在应用退出的 `finally` 中幂等关闭四个 Tracer Provider |
| 涉及位置 | `MiniShop 电商下单故障演练靶场/app/main.py` |
| 验证证据 | MiniShop 全量 `37 passed`，仅保留既有 TestClient 弃用告警 |
| 残余风险 | 后续升级到 httpx2 时需迁移当前 TestClient 用法 |
| 预防措施 | 生命周期能力优先使用 FastAPI lifespan，并把应用导入测试纳入最小回归 |

### DEV-006：默认 Python 环境未安装项目开发依赖

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 5 |
| 模块 | 演练管理员认证测试 |
| 分类 | 踩坑 |
| 状态 | 已解决 |
| 现象与证据 | 首次定向 pytest 在收集 `test_runtime.py` 时报告 `ModuleNotFoundError: aiokafka`，并提示未知 `asyncio_mode` |
| 影响 | 测试未进入认证功能断言，无法形成红绿验证 |
| 排查过程 | 检查当前解释器、虚拟环境和已安装包，确认工作区没有独立 venv，Anaconda 环境缺少 `aiokafka` 与 `pytest-asyncio` |
| 根因 | 当前机器环境此前未按根项目 `pyproject.toml` 安装完整开发依赖 |
| 解决方案 | 执行 `python -m pip install -e ".[dev]"`，按项目声明安装运行与测试依赖 |
| 验证证据 | 依赖安装后同一组测试进入断言，先得到 6 个预期红测，再实现并达到 `173 passed` |
| 残余风险 | 开发依赖未锁定精确版本，新版 Ruff 的格式结果与既有文件风格存在差异 |
| 预防措施 | 后续增加锁文件或受控工具版本，并优先使用项目独立虚拟环境 |

### DEV-007：Buildx Bake 无法处理非 ASCII 工作区会话头

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 5 |
| 模块 | `ops/minishop-e2e/run-e2e.ps1` |
| 分类 | 难点 |
| 状态 | 已解决 |
| 现象与证据 | Docker Desktop 构建报 `x-docker-expose-session-sharedkey` 含 non-printable ASCII，错误只在带中文的项目绝对路径出现 |
| 影响 | Compose 配置正确，但 Agent 和 MiniShop 镜像无法进入构建阶段 |
| 排查过程 | 分别关闭 Compose Bake 与 BuildKit，确认 `COMPOSE_BAKE=false` 不足以覆盖当前 Docker Desktop 默认行为，而 `DOCKER_BUILDKIT=0` 可稳定构建 |
| 根因 | Buildx 会话元数据把非 ASCII 工作区路径写入仅允许 ASCII 的 HTTP 头 |
| 解决方案 | E2E PowerShell 入口同时设置 `COMPOSE_BAKE=false` 与 `DOCKER_BUILDKIT=0`，使用经典构建器 |
| 验证证据 | 两个镜像均成功构建，随后完整三场景 E2E 通过 |
| 残余风险 | 经典构建器速度较慢，后续 Docker 修复该路径编码问题后可恢复 BuildKit |
| 预防措施 | 自动化脚本必须在真实工作区路径运行一次，不能只在纯 ASCII 临时目录验证 |

### DEV-008：非 root 容器无法写入默认 tmpfs

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 5 |
| 模块 | `ops/minishop-e2e/docker-compose.yml` |
| 分类 | 难点 |
| 状态 | 已解决 |
| 现象与证据 | Redpanda 无法创建 `pid.lock`，Prometheus 无法创建 `queries.active`，Tempo 无法创建 blocks 目录，均报告 `Permission denied` |
| 影响 | 基础设施容器提前退出，真实告警和 RCA 链路无法启动 |
| 排查过程 | 对照各容器运行 UID/GID 与 tmpfs 默认 root 所有权，逐个验证写入目录 |
| 根因 | Compose 的 tmpfs 挂载覆盖镜像目录后恢复成 root 所有权，而镜像按非 root 用户运行 |
| 解决方案 | 在各 tmpfs 声明中设置匹配镜像运行用户的 `uid`、`gid` 和 `mode` |
| 验证证据 | Redpanda、Prometheus、Alertmanager、Loki、Tempo 全部稳定启动，迁移容器退出码为 0 |
| 残余风险 | 升级镜像若改变运行 UID/GID，需要同步复核 Compose |
| 预防措施 | 非 root 镜像新增 tmpfs 时同时验证目录所有权和写入健康检查 |

### DEV-009：Tempo 搜索返回省略前导零的 Trace ID

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 5 |
| 模块 | `infrastructure/observability/tempo.py` |
| 分类 | 难点 |
| 状态 | 已解决 |
| 现象与证据 | 首个真实 Workflow 中 metrics、logs 成功而 traces 失败；Tempo `/api/search` 返回一条 31 位十六进制 `traceID` |
| 影响 | 单条摘要校验失败使整个 traces 工具失败，Workflow 无法生成四类 Evidence |
| 排查过程 | 直接执行两条 TraceQL，逐字段检查每条摘要，确认 31 位 ID 补一个前导零后用 31 位和 32 位形式均可从 Tempo 取回同一 Trace |
| 根因 | Tempo 搜索 JSON 对以零开头的 Trace ID 省略了前导零，而客户端只接受精确 16/32 位 |
| 解决方案 | 仍只接受最多 32 位十六进制字符，并按 64/128 位宽恢复前导零后输出规范化小写 ID |
| 验证证据 | Tempo 客户端 `20 passed`；真实三场景 Workflow 全部 `SUCCEEDED`，TRACE Evidence 齐全 |
| 残余风险 | Tempo API 版本升级后需继续用真实响应做契约测试 |
| 预防措施 | 外部观测 API 除 Mock 契约测试外，至少保留一条真实版本兼容验收 |

### DEV-010：Git 中文路径默认转义破坏 PowerShell 审计

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 6 |
| 模块 | 独立 Git 仓库初始化与提交前审计 |
| 分类 | 踩坑 |
| 状态 | 已解决 |
| 现象与证据 | `git ls-files --others` 把中文路径输出成带引号的八进制转义，PowerShell `Test-Path` 连续报告 `Illegal characters in path` |
| 影响 | 首轮候选文件大小与敏感文件统计漏掉中文目录，不能作为可靠提交依据 |
| 排查过程 | 对比 Git 原始输出与磁盘真实路径，确认文件未损坏，问题只发生在命令行显示编码层 |
| 根因 | Git 默认 `core.quotepath=true` 会转义非 ASCII 路径，返回值不是 PowerShell 可直接访问的路径 |
| 解决方案 | 在独立仓库设置 `core.quotepath=false`，重新统计 571 个候选条目并复核敏感文件、归档和数据库文件 |
| 验证证据 | 重新审计得到 570 个实际文件、约 5.67 MiB；真实 `.env` 与临时目录均被忽略，未发现私钥或云访问密钥形态 |
| 残余风险 | 其他终端若覆盖本地 Git 配置，中文路径输出可能再次被转义 |
| 预防措施 | 中文仓库的自动审计脚本显式使用 `git -c core.quotepath=false` 或设置仓库级配置 |

### DEV-011：锁定环境暴露根项目缺少显式打包元数据

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 5 |
| 模块 | `pyproject.toml`、CI、Docker 与运维资产测试 |
| 分类 | 难点 |
| 状态 | 已解决 |
| 现象与证据 | 首次 `uv run pytest -q` 有 3 个失败：Alembic 子进程无法导入 `devops_agent_platform`，另两个测试仍写死旧 pip/python 命令 |
| 影响 | 锁文件能解析依赖，但根项目本体没有进入隔离环境；资产测试也会错误阻止 locked 工具链迁移 |
| 排查过程 | 对比 MiniShop 与根 `pyproject.toml`，确认根项目缺少 `build-system` 和 `src` 包发现；检查失败断言后确认其验证的是命令文本而非门禁语义 |
| 根因 | 旧环境依赖 pytest `pythonpath` 与 editable pip 的隐式行为，打包边界未被独立验证；资产测试过度耦合实现字符串 |
| 解决方案 | 增加 setuptools 构建元数据与 `where = ["src"]`，CI 改用 `uv sync --locked`，测试改为验证锁定、Ruff、pytest、Alembic 和镜像安装语义 |
| 验证证据 | 定向资产测试通过，锁定环境平台全量 `1606 passed, 9 skipped`，MiniShop `37 passed`，锁定版三场景 Docker E2E 通过 |
| 残余风险 | 真实生产控制器、外部凭据和目标环境验收仍需组织环境完成 |
| 预防措施 | 新 Python 项目从第一天同时验证 `uv sync --locked`、子进程导入、wheel 构建和容器安装 |

### DEV-012：并发控制台实现产生重复静态资源边界

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 6 |
| 模块 | 运维控制台与 FastAPI 静态资源路由 |
| 分类 | 难点 |
| 状态 | 已解决 |
| 现象与证据 | 控制台实现期间工作区同时出现 `interfaces/http/console` 与 `interfaces/http/static/ops-console` 两套资源，后者已有包资源配置和 API 测试 |
| 影响 | 若继续分别接线，`/console` 首页、资源 URL 与打包清单会互相不一致 |
| 排查过程 | 对照 `git status`、资源时间戳、页面引用、JavaScript API 链路和并发新增测试，确认后者已覆盖事故、RCA、工单与安全头 |
| 根因 | 同一产品增量存在并发写入，路由与静态资源落在不同目录约定 |
| 解决方案 | 保留已有完整控制台资源，删除本次未接线的重复资源；显式白名单路由统一服务 `index.html`、`app.js`、`styles.css`，并补 CSP、`X-Frame-Options` 和禁止缓存 |
| 验证证据 | `tests/api/test_ops_console.py` 2 passed；Ruff 通过；`node --check app.js` 通过 |
| 残余风险 | 浏览器中的真实 API 交互仍需随完整本地栈做一次视觉回归 |
| 预防措施 | 多执行单元并发编辑前先声明文件所有权；发现陌生改动时先合并契约，不覆盖未提交内容 |

### DEV-013：供应商适配器首轮静态检查命中行宽

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 5 |
| 模块 | ServiceNow 工单适配器 |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | 首轮 Ruff 报 `servicenow.py:63 E501 Line too long (90 > 88)`，同轮 9 个行为测试均通过 |
| 影响 | 逻辑正确但不满足仓库静态门禁，不能进入下一集成步骤 |
| 排查过程 | 根据 Ruff 精确行号确认是密码控制字符校验的单行生成过长，不涉及业务语义 |
| 根因 | 编写复合生成表达式时未按项目 88 字符限制提前换行 |
| 解决方案 | 只重排该生成表达式，不改变校验条件 |
| 验证证据 | 修复后重新运行同一 Ruff 与定向测试 |
| 残余风险 | 无 |
| 预防措施 | 新增 Python 文件后先运行局部 Ruff，再继续跨模块装配 |

### DEV-014：根项目虚拟环境不能代替 MiniShop 锁定环境

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 5 |
| 模块 | MiniShop TestClient 兼容性检查 |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | 使用根项目 `.venv` 运行 MiniShop 全量测试时，4 个测试模块在收集阶段报告缺少 `opentelemetry` |
| 影响 | 测试未进入兼容性告警定位，输出不能用于判断 MiniShop 回归 |
| 排查过程 | 对照根项目与 MiniShop 的独立 `pyproject.toml`、`uv.lock`，确认两者运行依赖集合不同 |
| 根因 | 错把 monorepo 根虚拟环境当成所有子项目的共享测试环境 |
| 解决方案 | 改用 MiniShop 目录下的 `uv run --locked` 执行测试，不向根环境临时补装子项目依赖 |
| 验证证据 | 使用子项目锁定环境重新运行测试并单独检查警告摘要 |
| 残余风险 | 手工执行者仍可能选错解释器 |
| 预防措施 | 根项目和 MiniShop 文档、CI 命令始终显式带 `uv run --project` 或在各自目录执行 |

### DEV-015：httpx2 的 Python 下限与 MiniShop 声明范围冲突

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 5 |
| 模块 | MiniShop 测试客户端依赖迁移 |
| 分类 | 难点 |
| 状态 | 已解决 |
| 现象与证据 | 首次 `uv lock` 无解：MiniShop 声明 `Python >=3.9`，而 `httpx2>=2.7` 要求 `Python >=3.10` |
| 影响 | 直接加入无条件开发依赖会破坏项目已经声明的 Python 3.9 解析范围 |
| 排查过程 | 读取 uv 求解器给出的 marker 分支，确认当前 3.12 可安装，失败仅来自必须同时覆盖的 3.9 分支 |
| 根因 | 新一代测试客户端抬高了 Python 最低版本，子项目兼容声明尚未同步 |
| 解决方案 | 保留 `requires-python >=3.9`，仅在 `python_version >= '3.10'` 时安装 `httpx2`；3.9 继续解析兼容的旧依赖组合 |
| 验证证据 | 重新生成锁文件，并在 Python 3.12 锁定环境运行 37 个测试及警告检查 |
| 残余风险 | Python 3.9 分支不会使用 httpx2，但也不会解析到要求 httpx2 的最新 Starlette 组合 |
| 预防措施 | 新开发依赖加入多 Python 项目前，先核对 `Requires-Python` 并让锁工具覆盖所有 marker 分支 |

### DEV-016：RCA 反馈测试夹具漏传报告执行代次

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 5 |
| 模块 | RCA 反馈持久化增量 |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | 共享回归中 4 个反馈服务测试均在 `build_report()` 报 `RCAReport.__init__()` 缺少 `execution_attempt` |
| 影响 | 测试未进入反馈清洗、幂等、状态和历史断言 |
| 排查过程 | 对照现有 `RCAReport` 领域构造契约和其它报告测试，确认生产模型要求记录生成报告所属执行代次 |
| 根因 | 新测试夹具复制报告字段时遗漏了已有必填字段 |
| 解决方案 | 在唯一共享夹具中补 `execution_attempt=1`，不修改反馈服务逻辑 |
| 验证证据 | 重新运行反馈、Runtime、Settings、迁移、连接器和控制台共享回归 |
| 残余风险 | 无 |
| 预防措施 | 新领域夹具优先复用项目 builder；手写构造时从当前模型签名核对必填字段 |

### DEV-017：并发增量产生两套 remediation 模型和重复标识符

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 6 |
| 模块 | 平台级 Remediation 领域模型、数据库模型与仓储 |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | 全仓 Ruff 捕获 `F811` 重复定义，定位到 remediation 增量中出现两套同名模型或标识符 |
| 影响 | 如果放过，会让导入目标不稳定，测试和运行时可能引用到不同版本的计划模型 |
| 排查过程 | 按 Ruff 行号检查领域模型、数据库模型、映射器、仓储和 `__init__` 导出链路 |
| 根因 | 并发增量在相同职责边界内重复创建类型，缺少落盘后的一次统一命名审计 |
| 解决方案 | 统一保留单套 remediation 领域模型、数据库模型、映射器和仓储导出，删除重复标识符 |
| 验证证据 | 修复后全仓 Ruff 通过；后续定向 remediation 测试通过 |
| 残余风险 | 后续多模块并行编辑仍可能再次引入同名导出冲突 |
| 预防措施 | 新领域模块落盘后立刻运行 Ruff，并用 `rg` 检查同名 class、repository、identifier 导出 |

### DEV-018：迁移数量精确断言在新增计划表后失效

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 6 |
| 模块 | Alembic 迁移资产测试 |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | 新增 `20260723_0028_create_remediation_plans.py` 后，旧测试仍按 27 个迁移文件做精确断言 |
| 影响 | 迁移链本身正确，但资产测试误判新增迁移为失败 |
| 排查过程 | 对比 `migrations/versions` 实际文件数、测试断言和 Alembic head 线性检查 |
| 根因 | 测试把迁移数量作为稳定事实硬编码，而迁移数量本身会随功能增长正常变化 |
| 解决方案 | 将当前预期推进到 28，并保留更重要的单 head、线性链和 PostgreSQL 离线编译检查 |
| 验证证据 | 迁移资产定向测试通过；后续定向回归 `188 passed` |
| 残余风险 | 后续仍需在新增迁移时同步资产测试语义 |
| 预防措施 | 迁移测试优先验证链路性质，少用会随功能增长变化的精确数量 |

### DEV-019：新增文本 dataclass 后旧测试硬编码 28 导致全量失败

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 6 |
| 模块 | Step 4 completion 资产测试 |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | 新增 7 个文本类 dataclass 后，旧测试硬编码 `== 28` 导致全量测试失败 |
| 影响 | 测试没有发现真实质量问题，却阻断了新增合法领域对象 |
| 排查过程 | 对比失败断言、实际扫描到的 dataclass 数量和新增模块职责 |
| 根因 | 完成度测试把“至少具备若干 dataclass 契约”写成了精确数量 |
| 解决方案 | 改为动态扫描加下限断言 `>= 28`，保留架构完成度信号但允许功能增长 |
| 验证证据 | `tests/unit/ops/test_step4_completion.py` 更新后定向测试通过 |
| 残余风险 | 下限断言只能防止能力倒退，不能表达每个新增领域对象的业务正确性 |
| 预防措施 | 资产完成度测试使用下限、存在性和契约性质组合，不对增长型集合写精确数量 |

### DEV-020：修复风险、回滚和效果不能来自 HTTP 正文

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-24 / Step 6 |
| 模块 | 平台级 Remediation API、Service、Policy、Action Catalog、Ops Console |
| 分类 | 难点 |
| 状态 | 已解决 |
| 现象与证据 | 初版创建计划可从 HTTP 或页面传入 `risk`、`rollback_action_key`、`expected_effect`，存在低报风险、伪造回滚动作和伪造效果说明的边界问题 |
| 影响 | 如果调用方或 LLM 可影响这些字段，审批者看到的风险与回滚信息不再可信 |
| 排查过程 | 审查创建命令、DTO、服务构造计划、执行请求、策略校验、环境变量和控制台表单 |
| 根因 | 把受部署控制的动作元数据与用户创建请求混在同一输入面 |
| 解决方案 | 新增 JSON 动作目录；创建请求只接受 `action_key`、`target`、`evidence_ids`；目录派生风险、效果和回滚动作；执行前重检目录漂移、RCA 审计证据、租户、维护窗口和 kill switch；控制台不再提供任意命令文本 |
| 验证证据 | `ruff check src tests migrations ops` 通过；remediation 目录、服务、API、运行时和仓储定向测试通过；`node --check app.js` 与 Ops Console API 测试通过 |
| 残余风险 | 仓库只提供固定 HTTP 控制器适配器和 MiniShop 沙箱；真实生产控制器、凭据、工作负载身份与目标验收仍需组织环境完成 |
| 预防措施 | 把风险、回滚、效果和目标白名单视为部署配置，不从 HTTP、页面或 LLM 响应接收；新增动作必须先补目录测试和目标环境 dry-run/rollback 验收 |

### DEV-021：OIDC `nbf` 负向测试使用收集期时间导致全量运行后失效

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-24 / Step 6 |
| 模块 | OIDC 管理员认证测试 |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | 最终全量 pytest 第一次在 75% 处失败：`claims_overrides3` 未抛出 `AuthenticationRequired` |
| 影响 | 业务认证器正确，但负向测试在全量耗时超过 1 分钟时会把未来 `nbf` 等到变成有效时间 |
| 排查过程 | 查看参数化测试，确认 `datetime.now(UTC) + timedelta(minutes=1)` 在 pytest 收集阶段求值，而不是在测试执行时求值 |
| 根因 | 时间敏感 claim 使用过短的固定相对窗口，且窗口起点绑定到测试收集时间 |
| 解决方案 | 将 `exp`/`nbf` 参数改成 lambda，在测试执行时动态生成；`nbf` 未来窗口扩大到 10 分钟 |
| 验证证据 | OIDC 定向测试通过；最终根平台全量 `1606 passed, 9 skipped` |
| 残余风险 | 其它长耗时全量测试若在收集期生成相对时间，仍可能出现同类脆弱性 |
| 预防措施 | 时间负向测试使用执行期时间、注入时钟或足够大的窗口，避免依赖全量测试执行速度 |

### DEV-022：新增调查策略导出后导入顺序未通过 Ruff

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-26 / Step 4 |
| 模块 | C1 调查策略公共导出 |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | C0/C1 首轮定向 Ruff 检查报 `agent/__init__.py:3:1 I001 Import block is un-sorted or un-formatted` |
| 影响 | 仅阻断静态检查，未进入运行时行为验证 |
| 排查过程 | 对照 Ruff 给出的导入块，确认 `investigation_policy` 应排在 `llm_report_generator` 前 |
| 根因 | 手工追加公共导出时没有按模块路径排序 |
| 解决方案 | 仅调整两个导入块顺序，不改变导出名称或运行时语义 |
| 验证证据 | C0/C1 定向 Ruff 通过；pytest `190 passed` |
| 残余风险 | 后续新增公共导出仍可能重复出现机械排序问题 |
| 预防措施 | 修改 `__init__.py` 后先运行最窄 Ruff 检查，再进入全量测试 |

### DEV-023：调查策略测试误判了解析 API 的空白规范化语义

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-26 / Step 5 |
| 模块 | C1 调查策略解析与单元测试 |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | C0/C1 定向测试 `189 passed, 1 failed`；`test_unknown_or_dynamic_policy_is_rejected[fixed_default ]` 未抛异常 |
| 影响 | 新增测试错误阻断回归，且暴露 `build_plan_for_policy` 与 `parse_investigation_policy` 对空白处理不一致 |
| 排查过程 | 检查失败参数与解析实现，确认解析 API 会先 `strip()`，而 Settings 作为部署配置边界仍严格拒绝首尾空白 |
| 根因 | 测试把规范化输入误当未知策略，同时计划构造函数重复了另一套解析逻辑 |
| 解决方案 | `build_plan_for_policy` 统一复用解析函数；测试改为验证 API 规范化空白、未知/动态策略仍失败；Settings 脏配置负向测试保持不变 |
| 验证证据 | C0/C1 定向 Ruff 通过；pytest `190 passed` |
| 残余风险 | API 解析与部署配置边界的严格程度不同，需要在文档中明确 |
| 预防措施 | 对归一化函数分别测试“可规范化输入”和“不可接受输入”，避免把配置边界规则误套到内部 API |

### DEV-024：全仓 Ruff 捕获 Remediation 接线中的超长中文注释

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-26 / Step 6 |
| 模块 | Remediation Runtime 装配 |
| 分类 | 踩坑 |
| 状态 | 已解决 |
| 现象与证据 | C0/C1 全量回归中 pytest 已通过，但全仓 Ruff 报 `bootstrap/runtime.py:759 E501 Line too long (93 > 88)` |
| 影响 | 不影响运行时行为，但阻断仓库统一静态门禁 |
| 排查过程 | 根据 Ruff 行号定位到路线 B 接线注释，确认只是注释超过项目 88 字符行宽 |
| 根因 | 先前定向检查未覆盖整个 `bootstrap/runtime.py` 的既有未提交变更 |
| 解决方案 | 将注释拆为两行，不修改租约或超时参数 |
| 验证证据 | `uv run pytest -W error -q` 为 `1625 passed, 9 skipped`；`uv run ruff check .` 通过 |
| 残余风险 | 定向 lint 仍可能遗漏同一工作区内其它未提交文件 |
| 预防措施 | 模块定向检查之后必须再跑一次 `uv run ruff check .` 作为最终仓库门禁 |

### DEV-025：Runtime 增量接线的相邻条件块错位

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-26 / Step 6 |
| 模块 | `bootstrap/runtime.py` |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | 首轮补丁把 Audit Retention 的 done callback 放进了 reclaim Worker 的条件块；代码复读时发现，尚未进入提交 |
| 影响 | 只启用审计清理时异常任务不会被及时消费，且可能在只启用 reclaim 时访问空任务 |
| 根因 | 在结构相似的相邻启动块间使用了过宽补丁上下文 |
| 解决方案 | callback 回到审计 Worker 自己的条件块，并补 reclaim 独立启动、崩溃和 readiness 隔离测试 |
| 验证证据 | Runtime/Worker 定向 `47 passed`；全量 `1656 passed, 9 skipped` |
| 预防措施 | 生命周期接线后复读完整 start/readiness/stop 四段，不只看局部 diff |

### DEV-026：双 stale 查询突破整轮批量上限

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-26 / Step 6 |
| 模块 | `RemediationApplicationService.reclaim_stale` |
| 分类 | 难点 |
| 状态 | 已解决 |
| 现象与证据 | execution 与 rollback 查询都使用 `limit=50`，旧实现串行处理后单轮最多写 100 条 |
| 影响 | 部署配置不能真实约束单轮事务数量，积压时会放大数据库写压力 |
| 根因 | 仓储查询上限被误当成应用用例的整轮上限 |
| 解决方案 | 两类候选交错，按 `reclaim_stale(limit)` 的成功收口总数截断，并将 limit 固定为 1–1000 |
| 验证证据 | service 参数边界、Worker batch 透传、全量回归均通过 |
| 残余风险 | 不提供跨状态全局过期时间排序；当前优先保证两类状态公平和总量有界 |

### DEV-027：反馈评测导出测试导入顺序错误

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-29 / Step 6 |
| 模块 | `tests/unit/application/test_rca_feedback_service.py` |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | 首轮定向 Ruff 报 `I001 Import block is un-sorted or un-formatted`，同批 20 个业务测试全部通过 |
| 影响 | 仅阻断静态门禁，不影响反馈导出运行时语义 |
| 根因 | 手工加入 `Evidence` 与 `RCAReport` 导入时没有按完整模块路径排序 |
| 解决方案 | 将 `domain.models.evidence` 放在 `domain.models.rca_report` 前，不改生产代码或测试行为 |
| 验证证据 | 修正后重新运行最窄 Ruff 与反馈/Evidence/API 测试 |
| 预防措施 | 新增跨领域测试夹具后先检查完整导入块，再运行定向测试 |

### DEV-028：评测候选查询漏拒绝身份字段内部空白

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-29 / Step 6 |
| 模块 | `GetRCAFeedbackEvaluationCandidateQuery` |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | 文档与边界测试补齐后的定向回归为 `1 failed, 47 passed`；`feedback_id=rcf 001` 未抛 `AppValidationError` |
| 影响 | HTTP 路径会拒绝内部空白，但直接构造应用查询时可绕过同一身份格式边界 |
| 排查过程 | 对照 `FeedbackPath` 的 `^[^\s\x7f]+$` 与项目已有身份查询校验，确认新增查询只检查了首尾空白和 ASCII 控制字符 |
| 根因 | 将 `value.strip()` 误当成完整空白校验，遗漏字符串中间的空格或其它 Unicode 空白 |
| 解决方案 | 在新查询的三个身份字段上增加 `character.isspace()` 检查，保持 HTTP 与应用服务边界一致 |
| 验证证据 | 修复后反馈服务、仓储、认证、API 与产品资产定向回归 `48 passed`，Ruff 通过 |
| 残余风险 | 仓库其它历史命令或查询可能仍采用较宽的首尾空白规则，本次不做无关重构 |
| 预防措施 | 新增路径身份查询时同时测试首尾空白、内部空白、控制字符，并对照 HTTP Path 正则 |

### DEV-029：收尾只读审计命令包含错误参数和猜测路径

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-29 / Step 6 |
| 模块 | PowerShell 收尾差异审计 |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | 首次命令使用不存在的 `Select-Object -Head`；修正后另一组合命令又引用不存在的 `domain/models/workflow.py` |
| 影响 | 两次只读组合审计非零退出；未修改代码、数据或 Git 状态，也不影响项目运行时 |
| 排查过程 | 根据 PowerShell 参数错误改用 `-First`；再用 `rg --files` 确认真实文件为 `domain/models/workflow_run.py` |
| 根因 | 没有先验证 PowerShell 参数和猜测的领域模型路径，就把它们放进并行组合命令 |
| 解决方案 | 改用已支持的 `Select-Object -First`，并只读取 `rg --files` 返回的确定路径 |
| 验证证据 | 后续夹具、Workflow 与 Evidence 模型审计命令均以退出码 0 完成 |
| 残余风险 | 无项目代码风险；错误组合命令会制造无意义噪声并掩盖同批其它只读输出 |
| 预防措施 | 对不确定路径先单独运行 `rg --files`，组合命令只使用已确认路径和参数 |

### DEV-030：新增失败关闭测试漏导入异常类型

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-29 / Step 6 |
| 模块 | `tests/unit/application/test_rca_feedback_service.py` |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | 新增反馈与 Workflow 错配测试后，最窄 Ruff 报 `F821 Undefined name ResourceNotFound` |
| 影响 | 静态门禁阻止测试执行；生产代码和运行时行为未受影响 |
| 排查过程 | 查看测试文件异常导入块，确认已有 `AppValidationError`、`ConflictError`，但遗漏新断言使用的 `ResourceNotFound` |
| 根因 | 增加异常分支断言时只写了 `pytest.raises`，没有同步补全显式导入 |
| 解决方案 | 在既有异常导入块加入 `ResourceNotFound`，不修改测试语义或生产代码 |
| 验证证据 | Ruff 与反馈服务最窄测试重跑 |
| 残余风险 | 无运行时风险；新增异常断言仍需由最窄 lint 先行捕获此类机械错误 |
| 预防措施 | 新增 `pytest.raises` 异常类型后立即检查导入块，并先运行目标测试文件 Ruff |

### DEV-031：策展设计检索使用未转义正则和猜测路径

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-29 / Step 6 |
| 模块 | 评测策展资产现状检索 |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | 两次 `rg` 复合正则因未转义括号报 `unclosed group`；随后读取猜测的 `domain/redaction.py` 又报路径不存在 |
| 影响 | 三批只读组合命令非零退出；未修改代码、数据或 Git 状态 |
| 排查过程 | 拆开查询后用 `rg --files` 定位真实脱敏实现为 `tools/sanitization.py`，再只读取确认存在的文件 |
| 根因 | 在 PowerShell/正则双重转义场景中拼装了不必要的复合表达式，并再次跳过路径确认 |
| 解决方案 | 停止复合正则，改用固定关键词或确定文件路径；不确定路径必须先查文件索引 |
| 验证证据 | 后续控制台、测试与脱敏实现读取均以退出码 0 完成 |
| 残余风险 | 无项目运行时风险；组合搜索失败会制造审计噪声 |
| 预防措施 | 搜索含括号的字面量使用固定字符串或逐项查询，读取前先确认路径 |

### DEV-032：策展工具大补丁中的换行参数破坏补丁语法

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-29 / Step 6 |
| 模块 | 离线评测候选策展工具 |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | 首个新增文件补丁在 `newline` 参数处报 `invalid hunk`，三个目标文件均未创建 |
| 影响 | 首次实现未落盘，但没有产生部分文件或覆盖已有改动 |
| 排查过程 | 用 `Test-Path` 分别确认工具、候选示例和审核示例均不存在，定位为补丁字符串转义问题 |
| 根因 | 把包含转义换行的可选参数放入超长组合补丁，渲染后破坏补丁行结构 |
| 解决方案 | 移除非必要 `newline` 参数，并拆成工具、示例、测试三个小补丁 |
| 验证证据 | 三类文件后续分别创建成功，最窄测试进入执行 |
| 残余风险 | 无；Windows 默认文本换行不影响 YAML 语义 |
| 预防措施 | 新增大文件和示例资产分批补丁，避免在补丁字符串中嵌套非必要转义字符 |

### DEV-033：策展工具 Iterator 导入不符合 Python 3.11 风格

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-29 / Step 6 |
| 模块 | `ops/product/curate_evaluation_candidate.py` |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | 首轮最窄 Ruff 报 `UP035 Import from collections.abc instead: Iterator`，测试尚未运行 |
| 影响 | 仅阻断静态门禁，不影响平台运行时 |
| 排查过程 | 按 Ruff 精确行号检查导入，确认 `Any` 保留在 `typing`，`Iterator` 应移到 `collections.abc` |
| 根因 | 编写新脚本时沿用了旧版 typing 导入习惯 |
| 解决方案 | 仅调整导入来源，不改变策展逻辑 |
| 验证证据 | Ruff 通过；策展工具测试 `10 passed` |
| 残余风险 | 无 |
| 预防措施 | Python 3.11 新脚本优先从 `collections.abc` 导入容器协议，并先跑最窄 Ruff |

### DEV-034：产品文档换行破坏精确字符串守护

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-29 / Step 6 |
| 模块 | `ops/product/README.md` 与产品资产测试 |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | 策展组合测试 `1 failed, 22 passed`；守护断言找不到连续的 `Automatic curation` |
| 影响 | 只阻断文档资产测试；策展工具、控制台和运行时行为未受影响 |
| 排查过程 | 对照失败输出确认 README 将短语拆成行尾 `Automatic` 与下一行 `curation/anonymization` |
| 根因 | 更新产品边界文案时保留了精确字符串守护，却没有保持被守护短语连续 |
| 解决方案 | 将文案统一为连续的 `Automatic curation or anonymization`，不放宽边界断言 |
| 验证证据 | 产品资产与策展组合测试重跑 |
| 残余风险 | 文档精确字符串断言对换行敏感，但能防止关键非自动化边界被误删 |
| 预防措施 | 修改受资产测试保护的关键边界短语时同步检查完整字符串 |

### DEV-035：临时 CLI 验证批次因包含清理操作被策略拒绝

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-29 / Step 6 |
| 模块 | 离线策展 CLI 真实命令验证 |
| 分类 | 踩坑 |
| 状态 | 已解决 |
| 现象与证据 | 首批 PowerShell 命令包含临时文件 `Remove-Item`，整批在执行前被策略拒绝 |
| 影响 | 该批 CLI、读取与清理均未执行；没有修改仓库或外部状态 |
| 排查过程 | 查看拒绝信息确认是组合命令中的清理操作触发策略，而非策展脚本报错 |
| 根因 | 将验证和非必要清理绑定在同一命令批次，扩大了操作语义 |
| 解决方案 | 去掉清理步骤，使用系统临时目录中的唯一文件名执行 CLI 并只读检查结果 |
| 验证证据 | CLI 返回 `version: v2`、`sample_count: 3`，输出文件显示 `privacy: anonymized` |
| 残余风险 | 系统临时目录保留一份不含真实数据的合成示例输出，由操作系统临时文件策略清理 |
| 预防措施 | 验证命令只包含验证所需动作；清理不是成功条件时不要与核心命令绑定 |

### DEV-036：最终审计再次使用未转义复合正则

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-29 / Step 6 |
| 模块 | 策展工具最终差异审计 |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | 已记录 DEV-031 后，最终审计仍使用含 `open("x"` 的复合 `rg` 表达式，再次报 `unclosed group` |
| 影响 | 该批只读审计非零退出；文件和全部已通过门禁均未改变 |
| 排查过程 | 直接识别为同类正则转义错误，不再修改表达式，改用固定字符串查询 |
| 根因 | 没有把 DEV-031 的预防动作立即应用到同一轮后续命令 |
| 解决方案 | 使用 PowerShell `Select-String -SimpleMatch` 分别核对文档、策展和控制台契约 |
| 验证证据 | 后续审计确认 `open("x")`、人工批准/隐私复核、内部 ID 拒绝和控制台非自动化提示均存在 |
| 残余风险 | 无项目运行时风险 |
| 预防措施 | 本任务后续不再使用动态复合正则；固定字面审计统一使用 `-SimpleMatch` |

### DEV-037：评测门禁文档改写破坏治理术语守护

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-29 / Step 6 |
| 模块 | `ops/product/feedback-evaluation-loop.md` |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | 产品链组合回归为 `1 failed, 33 passed`；资产测试要求的 `Feature Flags` 治理术语被改成单数连字符写法 |
| 影响 | 文档治理边界守护失败；评测门禁代码和其余 33 项测试未失败 |
| 排查过程 | 根据 pytest 精确断言读取文档，确认只是术语改写而非实现或边界缺失 |
| 根因 | 同步新门禁文案时没有保留已有资产测试守护的明确复数术语 |
| 解决方案 | 恢复 `Feature Flags` 表述，并保持门禁不会修改或加载该示例的说明 |
| 涉及位置 | `ops/product/feedback-evaluation-loop.md`、`tests/unit/ops/test_step5_step6_assets.py` |
| 验证证据 | 重跑策展、评测 runner、报告门禁和 Step 6 资产组合测试 |
| 残余风险 | 无运行时风险 |
| 预防措施 | 文档大段改写后先运行已有资产守护，再扩大全量门禁 |

### DEV-038：报告门禁误拒绝 runner 的缺失响应结果

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-29 / Step 6 |
| 模块 | `ops/product/compare_evaluation_reports.py` |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | 全仓门禁通过后的代码复读发现：`run_evaluation.py` 对完全缺失响应返回全部组件失败，但 `forbidden_claims_found` 可为空；首版门禁要求失败布尔值必须对应非空明细，会把合法 `FAIL` 报告当成无效输入 |
| 影响 | 缺失候选响应本应生成机器可读 `FAIL`，却会提前返回输入错误且不产出决策 |
| 排查过程 | 对照 runner 的 `response is None` 分支与门禁样本一致性校验，确认两者契约存在差异 |
| 根因 | 首版一致性校验只考虑了存在响应时的明细语义，没有覆盖 runner 的缺失响应哨兵结果 |
| 解决方案 | 将全部四项组件失败识别为缺失响应结果；仍从样本重算全部比例，并为 runner→门禁链路补真实回归 |
| 涉及位置 | `ops/product/compare_evaluation_reports.py`、`tests/unit/ops/test_evaluation_report_gate.py` |
| 验证证据 | 新增 `test_missing_candidate_response_is_a_valid_failed_report` 并重跑产品链与全量 pytest |
| 残余风险 | 报告没有单独的 `response_present` 字段，只能从 runner 的全失败结果识别；该结果始终失败，不会放宽晋级门禁 |
| 预防措施 | 对比工具必须复用或逐分支覆盖上游报告生成器的完整输出契约，包括缺失样本路径 |

### DEV-039：GitHub Release 审计使用不受支持字段

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-29 / Step 6 |
| 模块 | GitHub Release 发布后审计 |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 现象与证据 | `gh release view` 拒绝 `isLatest` 字段；同批次的 `gh release create` 已成功完成 |
| 影响 | 只读审计非零退出；Tag 和已发布 Release 未受影响 |
| 排查过程 | 用 `gh release view` 返回的可用字段清单修正查询，并先列出 Release 防止重复创建 |
| 根因 | 混用了 `gh release list` 与 `gh release view` 的 JSON 字段集合 |
| 解决方案 | 使用 `tagName/name/isDraft/isPrerelease/publishedAt/url/targetCommitish` 重新审计 |
| 涉及位置 | GitHub Release `v0.3.0` |
| 验证证据 | Release 非草稿、非预发布，页面为 `releases/tag/v0.3.0` |
| 残余风险 | 无仓库运行时风险 |
| 预防措施 | 调用 `gh --json` 前按当前子命令支持字段构造查询，不跨子命令复用字段 |

### DEV-040：发布产物校验误拒绝 uv 生成的隐藏文件

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-29 / Step 6 |
| 模块 | `scripts/check-release-version.py` |
| 分类 | 踩坑 |
| 状态 | 已解决 |
| 现象与证据 | `uv build --out-dir <新目录>` 成功生成 `0.3.1` wheel/sdist，同时自动创建 `.gitignore`；首版校验器报告 `unexpected=['.gitignore']` |
| 影响 | 正确构建被版本门禁误拒绝；没有错误产物被接受或发布 |
| 排查过程 | 核对构建成功行和校验器实际文件集合，确认唯一额外项为 uv 的隐藏管理文件 |
| 根因 | 校验器把输出目录全部文件都当成发布产物，没有区分隐藏管理文件 |
| 解决方案 | 只对非隐藏文件执行精确集合校验；仍要求且只允许匹配版本的 wheel 和 sdist |
| 涉及位置 | `scripts/check-release-version.py`、`tests/unit/ops/test_release_version.py` |
| 验证证据 | 测试夹具加入 `.gitignore`；真实 `uv build` 后重新运行门禁 |
| 残余风险 | 其它隐藏文件不会作为发布资产校验；CI/Release 上传逻辑仍只引用显式 wheel/sdist |
| 预防措施 | 对工具生成目录做契约测试时覆盖工具自身的隐藏元数据，不用手工目录假设代替实跑 |

## 6. 分类汇总

### 踩坑

- DEV-001：无独立 Git HEAD 时不能安全执行自动文档提交。
- DEV-004：新增多层目录前先建父目录，补丁失败后检查部分落盘。
- DEV-006：运行测试前确认项目开发依赖已安装到当前解释器。
- DEV-010：Git 中文路径审计前关闭 `core.quotepath` 转义或显式按原样输出。

### 犯过的错误

- DEV-003：首个组合补丁粒度过大，因单个上下文不匹配而整体失败。
- DEV-013：供应商适配器复合表达式首轮未满足 88 字符行宽。
- DEV-014：根项目虚拟环境不能替代 MiniShop 的独立锁定依赖环境。
- DEV-015：新增依赖必须与项目声明的全部 Python marker 分支兼容。
- DEV-016：新测试夹具必须跟随当前领域模型的必填构造契约。
- DEV-017：并发增量落盘后必须统一清理重复模型和导出。
- DEV-018：迁移数量会随功能增长变化，测试不应只依赖精确计数。
- DEV-019：增长型 dataclass 集合应使用动态扫描和下限断言。
- DEV-021：时间负向测试不能用收集期短窗口作为稳定失败条件。
- DEV-022：公共导出变更后应立即检查导入排序。
- DEV-023：解析 API 的规范化语义与部署配置的严格校验应分别测试。
- DEV-024：定向 lint 之后仍需全仓门禁覆盖其它未提交增量。
- DEV-025：向相邻 Worker 接线块插入代码后，要立刻复读完整启动顺序，避免 done callback 挂错任务。
- DEV-037：治理文档改写要保留资产测试守护的明确产品术语。
- DEV-038：下游报告校验要覆盖上游生成器的缺失响应哨兵语义。

### 主要难点

- DEV-002：在自包含演练和生产安全边界之间建立明确隔离。
- DEV-005：用当前 FastAPI lifespan 管理 OpenTelemetry Provider 关闭。
- DEV-007：非 ASCII 工作区需要验证 Docker 构建会话元数据兼容性。
- DEV-008：tmpfs 挂载必须匹配非 root 容器的实际 UID/GID。
- DEV-009：外部 Trace API 的 ID 序列化需要规范化兼容层。
- DEV-011：锁定环境必须同时验证依赖和项目本体的标准打包元数据。
- DEV-012：并发前端增量必须先统一资源目录、路由和打包契约。
- DEV-020：修复动作的风险、回滚和效果必须由部署控制目录提供。
- DEV-026：两个各自有 limit 的 stale 查询不能直接串行处理，否则单轮真实写入量会变成配置的两倍。

## 7. 可复用解决经验

| 关联事件 | 问题模式 | 推荐解法 | 适用前提 | 不适用条件 | 验证方法 |
|---|---|---|---|---|---|
| DEV-001 | 自动化流程依赖 Git HEAD，但工作区没有可靠历史 | 停止自动 commit，保留未提交产物并说明限制 | 用户文件安全优先 | 已有独立、干净仓库 | `git status` 与提交历史检查 |
| DEV-002 | 本地 E2E 需要管理身份 | 显式演练认证开关 + 生产硬拒绝 | 仅本地、固定租户演练 | 生产或共享环境 | 配置负向测试 + 认证单测 |
| DEV-007 | Docker 构建上下文路径含非 ASCII | 在入口脚本显式选择已验证的构建后端 | 本地隔离演练 | 依赖 BuildKit 专属特性 | 在原始绝对路径执行真实构建 |
| DEV-008 | 非 root 镜像挂载 tmpfs 后失去写权限 | 按镜像 UID/GID 声明 tmpfs 所有权 | UID/GID 固定的镜像版本 | 动态或随机 UID | 容器启动日志 + 写目录健康检查 |
| DEV-009 | 外部系统省略标识符前导零 | 有界校验后恢复协议规定宽度 | 仍可确定 64/128 位语义 | 无法确定原始位宽 | Mock 边界测试 + 真实 API 查询 |
| DEV-010 | Git 输出含中文路径 | 关闭 quotePath 后再把输出交给文件系统工具 | 仓库允许本地 Git 配置 | 必须保持转义日志格式 | 候选数、文件数与磁盘统计交叉核对 |
| DEV-011 | 依赖锁存在但项目本体未安装 | 补标准构建元数据并在空 venv 验证子进程导入 | Python 包采用 src layout | 纯脚本且不作为包安装 | `uv sync --locked` + Alembic/CLI 子进程测试 |
| DEV-026 | 执行与回滚 stale 查询各自有上限，但业务要求整轮总量有界 | 两类候选交错后按总 batch size 截断，Worker 每轮只调用一次应用服务 | 两类状态可用同一 fence 收口 | 必须按全局时间严格排序 | 服务边界测试 + Worker batch 透传测试 |

## 8. 关键技术决策

| 决策 | 候选方案 | 最终选择 | 选择依据 | 代价/风险 |
|---|---|---|---|---|
| Alertmanager 接入 | 修改平台 canonical DTO / Alertmanager Relay | MiniShop 内置 Relay | 保留平台现有 HMAC 与扁平告警契约 | Relay 需要独立测试和超时处理 |
| 管理员认证 | 完整 IdP / 绕过认证 / 演练认证器 | 受控演练认证器 | 自包含且不破坏生产默认 | 必须严格防止生产启用 |
| 场景数据 | Markdown / JSON Manifest | 版本化 JSON Manifest + Pydantic 校验 | 无新增 YAML 解析依赖，便于测试和工具消费 | 人工编辑略显冗长 |
| 修复动作安全边界 | HTTP 正文传完整计划 / LLM 生成动作文本 / 部署目录派生 | 部署控制 JSON 动作目录派生风险、效果、回滚和目标白名单 | 审批者看到的风险和回滚路径必须可信，执行前可检测目录漂移 | 新增动作需要同步目录文件、测试和目标环境验收 |
| 过期修复租约调度 | 外部 cron / HTTP 管理写接口 / Runtime 内 Worker | 默认关闭的 Runtime Worker | 复用应用服务、生命周期、readiness 和监控模式，不扩大 HTTP 写面 | 多副本会并发扫描，依赖版本 fence 消解竞争；仍需 staging 验收 |
| 人工反馈进入评测治理 | 自动合并并写数据集 / 离线手工复制 / 单条反馈只读候选导出 | 独立权限下的单条候选导出，固定 `review_required=true` | 保留审核人结论边界，先建立安全数据出口，再做策展和真实评测 | 仍需人工匿名化、策展、版本化与批准；不能声称自动学习闭环 |
| 评测候选策展 | 在线自动审批 / 任意脚本追加 / 显式离线审核清单 | 候选下载 + fail-closed 离线 CLI，只创建 `vN+1` 新文件 | 不让程序自行推断匿名化事实，保留人工授权和不可覆盖版本边界 | 尚无在线多审核人签字、集中存储/发布或真实模型定时评测 |
| 评测报告门禁 | 直接信任汇总字段 / 读取 Prompt Registry 自动灰度 / 纯离线报告比较 | 绑定同一有序数据集、从样本重算比例并执行硬下限和零回退 | 防止报告错配或汇总篡改；`PASS` 只进入人工发布评审 | 报告尚无延迟或独立不安全建议计数，真实变体生成与定时评测仍未接线 |

## 9. 技术债清偿状态

| 编号 | 原问题 | 状态 | 清偿方案 | 验证 |
|---|---|---|---|---|
| DEBT-001 | 项目没有独立 Git HEAD | 已解决 | 在项目根目录初始化独立 `main` 仓库，提交前排除 `.env`、虚拟环境、缓存和临时输出 | `git rev-parse --show-toplevel` 指向当前项目；基线提交后工作区干净 |
| DEBT-002 | 开发依赖和 Ruff 未精确锁定 | 已解决 | 根平台与 MiniShop 各自提交 `uv.lock`；CI/Docker 使用 `uv 0.11.31 --locked`；Ruff 固定 `0.15.22` | 两个 lock check、隔离环境全量测试、Ruff、锁定版 Docker E2E 均通过 |
| DEBT-003 | Git Release 与根平台包/API/部署版本漂移 | 已解决 | `pyproject.toml` 作为唯一版本源；锁文件、FastAPI、Kubernetes 示例统一为 `0.3.1`，Tag CI 校验版本与 wheel/sdist | 版本定向测试、真实 `uv build`、Tag 工作流和全量门禁 |
| DEBT-004 | GitHub Release 页面没有可下载的 Python 包和独立校验和 | 已解决 | Tag CI 暂存已校验的 wheel/sdist；测试、镜像、SBOM 和漏洞门禁全部通过后，以最小写权限发布包与 `SHA256SUMS`；同名不同内容失败关闭 | 发布器单测、工作流守护测试，以及 `v0.3.2` Tag 的真实发布 CI |

## 10. 最终复盘

### 做得好的地方

- 从 Manifest 标准答案倒推指标、日志、Trace、告警和报告断言，避免只验证 Workflow 状态。
- 保留平台 HMAC、权限、租户、Outbox、Kafka 和租约边界，没有为演练绕过生产核心契约。
- 每个真实容器故障都先保留原始证据，再做最小修改并重跑完整链路。

### 可以改进的地方

- 应更早在包含中文的真实绝对路径执行 Docker 构建，静态 Compose 展开无法发现 Buildx 会话头问题。
- 应在项目开始时创建隔离虚拟环境并锁定工具版本，避免全局 Anaconda 包冲突和 Ruff 漂移。
- Tempo Mock 响应应尽早加入前导零被省略的真实样本。

### 下次直接复用的经验

- 端到端 RCA 验收至少同时检查：Incident、Workflow、Invocation、四类 Evidence、报告引用和 Ground Truth。
- 本地固定 Token 必须默认关闭、与 OIDC 互斥、生产硬拒绝，不能用“仅演示”代替安全边界。
- 非 root 可观测栈使用 tmpfs 时，把 UID/GID 验证纳入 Compose 资产测试和启动验收。

### 验证与已知限制

- 已完成验证：平台 `1711 passed, 9 skipped`，MiniShop `37 passed`，
  E2E 资产 `3 passed`，Ruff check 和 Compose config 通过。
- 已完成真实链路：三份 Manifest 均在重建后的 Docker 环境得到
  `SUCCEEDED` Workflow 与四类 Evidence，结果文件 `passed: true`。
- 已完成工程基线：独立 Git `main` 仓库、两个 `uv.lock`、锁定 CI 与锁定
  Docker 安装路径均已验证。
- 已知限制：仓库没有真实生产控制器凭据、Kubernetes/SSH/cloud/shell 写适配器或外部供应商验收环境。
