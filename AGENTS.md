# AGENTS.md — oh-my-multica（omac）工程架构语言

任何在本仓库开发的 Agent，动手前先读完本文件。本文件规定统一的架构词汇、边界和规则：
只读它也不应犯架构错误。设计的"为什么"见 [`docs/design/README.md`](docs/design/README.md)
（下文写作"设计 §N"，代码注释按同一编号引用）。两者冲突时以代码与 CI 守护为准，并在 §7 登记。

标注 **（CI 强制）** 的规则由 `lint-imports`（`pyproject.toml` 中的 contracts）或
`tests/test_architecture.py` + `tests/_support/arch_rules.py` 检查；其余是评审要求。

---

## 1. 项目定位

`omac`（PyPI 包名 `oh-my-multica`，CLI `omac`）是建在 Multica 之上的**通用软件交付控制层**。
它面向用"多个 Coding Agent"交付软件的团队和上层 Agent：把一个目标规划成节点 DAG，
经工作项平台把每个节点派给 Agent，收集结果，用机器检查与独立评审把关，合并，
循环到 DAG 收敛且验收文档通过。它要解决的是：Agent 能写代码，但需求漂移、自审自放行、
"测试通过"只是一句话、长时间循环悄悄停住且不可恢复。

**非目标**

- 不是 Agent 运行时，也不是任务平台：Agent 的执行、排队、机器管理归 Multica。
- 不在产品里写项目策略或单次事故修复：`src/omac` 只放对所有项目都成立的通用机制（硬规则 4）。
- Agent 不决定控制流：下一步做什么由确定性代码决定（§5.5）。
- Web 不提供写操作（只读看板）。
- 不读写 1.x 单文件 manifest；不提供 `mock` 引擎（2.0 已删除）。

## 2. 业务模型

### 2.1 核心实体

| 实体 | 是什么 | 位置 | 谁拥有 / 谁能改 |
|---|---|---|---|
| 配置 Config | 引擎、workspace、project、角色、重试预算等 | `.omac/config.yaml`，`core/config.py` | 人（`omac init`、`omac config set`）；优先级 文件 < `OMAC_*` 环境变量 < 命令行参数 |
| Manifest（DAG） | `meta` + 节点定义 | `.omac/<name>.yaml`，`core/manifest.py::Manifest` | 只在 plan / amendment 时由 omac 改写 |
| 节点 Node | 定义（`id`、`worker`、`blocked_by`、`reviewer`、`gate`、`contract`…）+ 运行时状态 | `core/manifest.py::Node` | 定义见上；运行时状态**只由 omac Loop 写**（单写者锁） |
| 节点状态文件 | 每节点 6 个运行时事实：`work_item_id`、`status`、`merged`、`merged_at`、`merge_request_state`、`recovery_marker` | `.omac/<name>.state/<node>.yaml`（`_RUNTIME_KEYS`） | omac Loop |
| 契约 Contract | 目标、验收、非目标、验证命令、集成门、`pr_base`、覆盖率门 | `core/manifest.py::Contract`，派单时下发到工作项 | orchestrator 产出，经评审与 lint |
| 工作项 Work item | 平台上的一个 issue，承载一个完整阶段（产出、评审、回退往返都在同一条上） | `core/workitem.py::WorkItem` | omac 经 `apply` 写；Agent 只经 `omac work submit|block` 写 |
| 任务类型 TaskKind | `plan`、`acceptance`、`decompose`、`amendment`、`develop`、`final-acceptance` | `core/taskmeta.py` | — |
| 任务阶段 TaskPhase | `authoring`、`review`、`confirmation`（评审是阶段，不是独立 issue） | `core/taskmeta.py` | omac |
| Run | Agent 在平台上的一次执行 | `core/workitem.py::AgentRunObservation`，`engines/runtime.py` | 平台；omac 只 `wake` / `cancel` / 观察 |
| 拉取请求 Pull request | Worker 的代码交付 | `engines/codehost.py::CodeHost` | Worker 开 PR；合并只由 omac 经 `RequestMerge` 发起 |
| 证据 Evidence | verification、review report、verdict、验收结果；有 schema | `core/evidence.py` | Agent 产出；omac 校验 |
| 评审结论 Verdict | `pass` / `pass-with-nits` / `reject`，只对其 subject digest 有效 | `core/evidence.py` | Reviewer（不能是产出者本人） |
| 验收文档 | 业务流与证明它们的证据 | `.omac/<name>.acceptance.yaml`，`core/acceptance*.py` | planner 产出；acceptor 执行 |
| 修订 Amendment | 运行中改 DAG：绑定基线 manifest digest 的已评审提案 + 账本 | `core/amendment.py`，`pipeline/amendment.py` | `omac dag amend propose|accept` |
| 机制角色 | `planner`、`orchestrator`、`worker`、`reviewer`、`acceptor`（`pipeline/dispatch.py::KIND_ROLE`） | `src/omac/guide/roles/` | 配置在 `roles` 中映射到具体 Agent |
| Agent 模板 | 通用能力画像（architect、backend-eng…），**不是**机制角色 | `src/omac/agents/` | `omac init` 用它创建 Agent |

关系：一个 Manifest 有多个 Node；`blocked_by` 构成 DAG。一个 Node 对应至多一个当前工作项
（`work_item_id`）；一个工作项有多个 Run，按 Kind × Phase 自描述；develop 工作项关联一个 PR。

### 2.2 节点状态机（manifest 侧，`Node.status`）

```
todo ──派单──► in_progress ──(配置了 CI)──► ci_check ──► in_review ──pass──► merging ──观察到 MERGED──► done
                   ▲                          │ CI 失败          │ reject              │ 合并冲突
                   └──────── 回到 Worker（retry.ci / retry.review / retry.merge / retry.worker，有界计数）
任何进行中状态 ── 预算耗尽 / 结果未知 / 漂移 / 上游失败连坐 ──► blocked ──► exit 20
blocked ── omac node retry ──► todo        blocked ── omac node abandon ──► abandoned
blocked / in_review ── omac node accept ──► done        上游修复后被连坐的下游 ──► todo（unblock）
```

- **就绪节点**：`status == todo` 且每个 `blocked_by` 都是 `done` 或 `abandoned`（`core/graph.py::ready_nodes`）。
- **收敛**：没有进行中、`blocked`、`failed` 节点；`done`、`abandoned`、`cancelled` 计为收敛。
- 平台映射：`pipeline/delivery.py::MANIFEST_TO_PLATFORM_STATUS`（`ci_check→IN_PROGRESS`，`merging→IN_REVIEW`）；
  平台侧 `WorkItemStatus` 为 `TODO/IN_PROGRESS/IN_REVIEW/DONE/FAILED/BLOCKED`。
- 合并子状态机 `merge_request_state`：`None → intent → requested →` 观察到 PR MERGED（写 `merged`、`merged_at`）。
  `intent` 之后绝不发第二次合并请求；结果未知则 blocked，由 `omac node retry` 显式决定。
- 写状态的只有 Loop（经 Effect，见 §3.4）和人的显式决策命令（`omac node retry|abandon|accept|accept-nits`）。

### 2.3 编写型任务（plan / acceptance / decompose / amendment）的阶段机

`pipeline/decide_task.py`：显式转移表 `(Stage, Fact) → (下一 Stage, effects)`，
终态只有 `STOPPED` 与 `DONE`；`OPEN → PRODUCE → MACHINE_GATE → REVIEW_* → … → DONE`，
预算用尽走 `MACHINE_GATE → BLOCK_BUDGET → STOPPED`。改动任何一行都要同步 `tests/test_decide_task.py`。

### 2.4 端到端流程

1. **init**：`omac init` 写配置，选择或创建平台 project，可选地从 `src/omac/agents/` 模板创建 Agent。
2. **plan**：`omac plan create|confirm|resume` 依次跑 plan（设计）→ acceptance（验收文档）→ decompose（manifest），
   每步都是"产出 → 机器门 → 有界评审"；`workflow.human_in_loop` 为真时等人确认；最后一起提交 manifest 与验收文档。
3. **dag run / tick**（一轮 tick）：
   `reconcile → 结果收集（collect_results）→ 证据 / CI / 合并门 → 就绪节点 → 派单 → 收敛或 exit 20`。
   `dag run` 循环 tick；`dag tick` 只跑一轮（exit 0/10/20）。每轮先观察平台与 manifest，再决策。
4. **Agent 侧**：`omac work show <issue>`（实例事实包 + `guide_refs`）→ 干活 → `omac work submit <issue> …`；
   需要人决策时 `omac work block`；读上游交付用 `omac work read`。
5. **交接**：Worker → Reviewer 由持久化意图串起（`worker_handoff`、`reviewer_run_baseline`、`delivery_identity`）；
   Run、验证附件、PR head 三者对齐才算一次交付；reject 后必须移动 PR head。
6. **验收与修订**：DAG 收敛且需要验收时，acceptor 按验收文档跑外层循环（`acceptance.max_rounds`，与 `retry` 无关）；
   失败流产生增量拆解并入 manifest。运行中改 DAG 用 `omac dag amend propose|accept`。

## 3. 系统架构

### 3.1 分层图

```
 人 / 脚本 / 上层 Agent                         Coding Agent（在平台 Run 内）
   │ omac <noun> <verb>      omac web             │ omac work show|read|submit|block
   ▼                           ▼                  ▼
 web ─────────────────────► cli（组装根：参数解析、退出码、engine_from_config）
                               ▼
                           pipeline（Loop：decide_* 纯决策 → Plan[Effect] → apply 执行）
                               ▼
                           engines（端口 WorkItemStore / AgentRuntime / CodeHost；适配器 multica / github）
                               │                     └──► Multica 平台（multica CLI、curl 兜底）、GitHub（gh）
                               ▼
                           core（领域模型、manifest I/O、校验、证据、图、gitsync）──► 本地 git、.omac/
                               ▼
                           i18n → errors
```

### 3.2 依赖方向（CI 强制）

只能向下依赖：`web → cli → pipeline → engines → core → i18n → errors`。

- 函数级（惰性）import 同样计入；不能用惰性 import 绕过分层，要把代码挪到正确的层。
- 具体适配器 `omac.engines.multica`、`omac.engines.github` 只能被 `engines` 工厂导入（contract `adapters-via-factory`）。
- CLI 只通过 `engines.engine_from_config` 组装引擎；`create_engine` 只在 `engines/` 内调用（规则 `engine_assembly`）。
- 唯一永久例外：`omac.cli.commands.web -> omac.web.server`（`omac web` 启动服务）。`ignore_imports` 基线只能缩小，目前为空。
- `decide_*` 模块不得 import `omac.engines` 或 `subprocess`（`decide_imports`）。

### 3.3 模块职责与边界

| 模块 | 职责 | 边界（不能做） |
|---|---|---|
| `web/`（`api.py`、`server.py`、`static/`） | 只读看板；路由解析参数、调用命令函数、原样返回 JSON | 不读 manifest、不调引擎、不转换数据、不吞异常 |
| `cli/`（`main.py`、`commands/<noun>.py`、`exit_codes.py`、`output.py`） | 名词-动词命令树、参数校验、退出码、组装引擎 | `sys.exit` 只在 `cli/main.py`；不直接写端口（`init_cmd.py` 向导除外） |
| `pipeline/` | Loop：reconcile、结果收集、门、派单、交付、验收、plan、amendment | 端口写只在 `apply.py`；不执行平台 CLI |
| `pipeline/decide_*.py` | 纯决策：`Observation` 进，`Plan` 出 | 无 I/O，不碰引擎与子进程 |
| `pipeline/apply.py` | 唯一把 Effect 变成端口调用的地方 | 不重试、不吞异常 |
| `engines/store.py`、`runtime.py`、`codehost.py` | 三个端口（抽象接口，措辞平台中立） | 不写平台专有说明 |
| `engines/multica.py`、`github.py` | 适配器：唯一执行平台 CLI、解析平台输出的地方 | 不含业务决策 |
| `engines/models.py` | 引擎层数据类型（`EngineConfig`、`AgentInfo`…），不是端口 | — |
| `core/` | 领域模型、manifest I/O 与锁、lint、证据 schema、图算法、git 状态同步、日志 | 不知道任何平台 |
| `i18n.py` | `ui(english, chinese)`、`t(key)`、语言解析（默认英文） | — |
| `errors.py` | `OmacError` 层级，与退出码一一对应 | — |
| `guide/`、`agents/` | 打包的、机器消费的双语指南与 Agent 模板 | 只写机制（交什么、什么格式），不写项目策略 |
| `agent_templates.py` | 发现与校验内置 Agent 模板（供 `omac init`） | 见 §7 第 6 条 |

### 3.4 Observation → Plan → apply（Loop 的唯一形状）

决策函数读冻结的 `Observation`，返回 `Plan`（有序的冻结 `Effect` 元组 + 结果名）；
外壳"读 → 决策 → `apply(plan, ports, state)`"，一次一个效果、严格按序、每个效果一个结果。

| Effect | 端口 |
|---|---|
| `UpdateStatus`、`UpdateMetadata`、`AddComment`、`Assign`、`ClearAssignment`、`ResetReview`、`PrepareReviewCycle`、`CreateWorkItem`、`SetNodeContract`、`SetAuthoringIdentity`、`NormalizeConfirmedMerge` | `WorkItemStore` |
| `Wake`、`AcknowledgeAssignment`、`DispatchReviewer`、`CancelRun` | `AgentRuntime` |
| `RequestMerge` | `CodeHost` |
| `SetNode`、`SaveState` | manifest（先内存，再原子落盘） |

一个决策若中间必须插一次读取，就在读取处拆成两次 `apply`；读取是显式阶段，不能藏进 Effect（设计 §7.3.1）。

### 3.5 外部系统的唯一接入点

| 外部系统 | 唯一接入点 |
|---|---|
| Multica（工作项、Run、Agent、附件） | `engines/multica.py`（`multica` CLI；精确 UUID 的 `curl` 只作有文档说明的兜底） |
| GitHub / 代码托管（PR 观察、检查、合并） | `engines/github.py`；配置的 `ci.check_command`、`merge.command` 只在 `CodeHost` 内执行 |
| 本地 git | `core/gitsync.py`、`core/repository_files.py`，以及白名单中的 `pipeline/plan.py`、`cli/commands/init_cmd.py` |
| `bash -n`（命令语法预检） | `core/review_preflight.py` |

子进程白名单是 `tests/_support/arch_rules.py::SUBPROCESS_ALLOWED`；平台 CLI（`multica`、`gh`、`curl`）只允许在 `engines/` 内（CI 强制）。

### 3.6 数据与状态存放

| 数据 | 位置 | 规则 |
|---|---|---|
| 配置 | `.omac/config.yaml`（提交到 git） | 派单前自动同步到 main |
| DAG 定义 | `.omac/<name>.yaml`（git） | 只在 plan / amendment 时变化；`meta` 中未知键原样保留 |
| 节点运行时状态 | `.omac/<name>.state/<node>.yaml`（git） | 首行 `# sha256: <正文摘要>`；单文件 ≤ 64 KiB；增量原子写 |
| 验收文档 | `.omac/<name>.acceptance.yaml`（git） | 与 manifest 一起提交 |
| 效果意图、交接、verdict、证据 | 平台工作项的 metadata 与附件 | 大载荷存附件，manifest 只留引用 + 摘要 |
| manifest 写锁 | `$TMPDIR/omac-manifest-<hash>.lock`（`fcntl.flock`） | 本机单写者 |
| 多机同步 | `origin/main`（`core/gitsync.py`） | 每轮 tick 后提交；不同机器推进不同节点，改不同文件 |

## 4. 统一架构词汇表

代码标识符、错误信息、指南、提示词、测试名和文档都必须用下列术语，**不允许同义异名**。

| 术语（中 / 英） | 定义 | 代码对应 |
|---|---|---|
| Loop / 交付循环 | 确定性控制流：观察 → 决策 → 执行，直到收敛或需要决策 | `pipeline/loop.py`、`cli/commands/dag.py` |
| tick / 一轮 | Loop 的一次完整推进 | `omac dag tick`、`loop.py` |
| reconcile / 对账 | 读进行中节点与平台事实，修复 manifest/平台漂移；低频全量审计 | `loop.py`、`pipeline/reconcile_audit.py` |
| 结果收集 / result collection | 逐个推进进行中节点、收回其结果 | `loop.py::collect_results`（禁用 harvest） |
| 进行中节点 / running nodes | 状态在 `in_progress`、`ci_check`、`in_review`、`merging` 的节点 | `loop.py::RUNNING_STATUSES`（禁用 in-flight、在飞） |
| 就绪节点 / ready nodes | `todo` 且所有上游 `done`/`abandoned` | `core/graph.py::ready_nodes`（禁用 frontier） |
| 收敛 / converged | 无进行中、blocked、failed 节点 | 退出码 0；`EVT_CONVERGED` |
| 派单 / dispatch | 为就绪节点创建或复用工作项并唤醒 Agent | `pipeline/dispatch.py` |
| 门 / gate | 机器检查：lint、证据 schema、CI、合并观察、覆盖率 | `core/lint.py`、`core/evidence.py`、`pipeline/delivery.py` |
| 回退 / rework（bounce） | 有界、计数地把交付退回产出者 | `retry.worker|ci|review|merge`，`WorkItem.bounces` |
| 显式决策 / decision | 只能由人或调用方做的选择，exit 20 + 结构化报告 | `NeedsDecision`、`omac node retry|abandon|accept|accept-nits` |
| 观察 / Observation | 决策读取的冻结事实 | `pipeline/decide_effects.py::Observation` |
| 计划 / Plan | 有序的 Effect 元组 + 结果名 | `decide_effects.py::Plan`、`validate_plan` |
| 效果 / Effect | 一个端口调用或 manifest 写入的值对象 | `decide_effects.py`，执行于 `pipeline/apply.py::apply` |
| 意图 / intent | 风险效果之前先持久化的"我将要做 X" | `merge_request_state="intent"`、`worker_handoff` |
| 端口 / port | 平台中立的抽象接口 | `WorkItemStore`、`AgentRuntime`、`CodeHost` |
| 适配器 / adapter | 端口的平台实现 | `engines/multica.py`、`engines/github.py` |
| 引擎 / engine | 每个端口一个适配器的组合 | `engines.Engine`、`engine_from_config` |
| 工作项 / work item | 平台 issue，承载一个完整阶段 | `core/workitem.py::WorkItem` |
| Run | Agent 的一次执行 | `AgentRunObservation` |
| 交接 / handoff | Worker 与 Reviewer 之间由持久化意图串起的轮转 | `taskmeta.WorkerHandoffIntent`、`ReviewerRunBaseline` |
| 交付身份 / delivery identity | Run + 验证附件 + PR head 对齐后封存的交付 | `taskmeta.DeliveryIdentity` |
| 评审主题摘要 / subject digest | verdict 所针对的交付摘要 | `review_subject_digest`、`PrepareReviewCycle` |
| 契约 / contract | 节点的目标、验收、非目标、验证命令、门 | `core/manifest.py::Contract` |
| 证据 / evidence | 有 schema 的验证、评审、验收产物 | `core/evidence.py` |
| 恢复标记 / recovery marker | 平台上有待消费恢复事实的轻量标记 | `Node.recovery_marker` |
| 修订 / amendment | 运行中对 DAG 的已评审变更 | `omac dag amend`、`core/amendment.py` |
| 机制角色 / role | planner、orchestrator、worker、reviewer、acceptor | `dispatch.py::KIND_ROLE` |
| Agent 模板 / agent template | 通用能力画像，不是机制角色 | `src/omac/agents/`、`agent_templates.py` |
| 运维工具 / operator tooling | 针对单次事故、写在包外、用完即删的脚本 | 不进 `src/omac` |

## 5. 工程规范与约束

### 5.1 硬规则（评审硬停）

1. **平台边界**（CI 强制）。`multica`、`gh` 等平台 CLI 只在 `engines/multica.py`、`engines/github.py` 中执行。
   `pipeline`、`core`、`cli`、`web` 不执行它们，也不依赖它们的命令语法或 Agent 运行时转录格式
   （例如在会话日志里匹配 `["multica", "issue", ...]`）。需要某个事实，就给端口加语义方法，由适配器返回结构化数据。
   本地工具只限现有的 `git`、`bash -n`；新增子进程调用点必须改 `SUBPROCESS_ALLOWED`，并在评审中说明理由。
2. **分层**（CI 强制）。见 §3.2。
3. **Web 只读**。路由层只解析参数、调用命令函数、原样返回 JSON。web 需要的数据若没有命令返回，
   先加带 `--output json` 的 CLI 命令，让人、Agent、web 看到同一份事实。
4. **产品内不写事故专用代码**（CI 强制）。`src/omac` 不得出现被捕获的标识符（工作项 / workspace / Run UUID、
   commit 或文件 SHA、具体节点名、URL）作为准入条件或特例；不得把 `node_id`、`dag_key`、`work_item.id`、
   `workspace_id` 与字符串字面量比较；不得出现具体项目名。单次事故的恢复写成包外运维工具，评审、执行、删除。
   通用机制（未知结果绝不重放、漂移即失败关闭）留在产品里，但不带事故常量。
5. **Manifest 状态**。每个 manifest 只有一个写者（`core/manifest.py::manifest_write_lock`）。
   运行时状态是每节点一个小文件；`save_manifest` 只重写内容变化的文件；不得新增重写整份状态
   或读取 1.x 单文件 manifest 的代码路径。`meta` 中未知键原样保留。不得在 manifest 中存放无界或大载荷
   （捕获的源文件、转录、归档），改存附件引用 + 摘要。

### 5.2 命名与目录约定

- 纯决策放 `pipeline/decide_<主题>.py`；Effect 类型只在 `decide_effects.py` 定义；执行只在 `apply.py`。
- 端口在 `engines/{store,runtime,codehost}.py`，方法名平台中立；平台专有说明写在适配器内部。
- CLI 是"名词 动词"：每个名词一个 `cli/commands/<noun>.py`，提供 `NAME`、`register(parser)`、`run(args)`。
- 节点 id 会变成文件名：只用字母、数字、`.`、`_`、`-`。
- 任何函数或方法不超过 150 行（`function_length`，CI 强制）。
- 测试文件 `tests/test_<主题>.py`；共享 helper 放 `tests/_support/`，测试文件之间不得互相 import（CI 强制）；
  测试名不得重复。需要真实服务的测试放 `tests/it/`。
- 术语见 §4；退出码见 §5.3。

### 5.3 错误处理与退出码契约（稳定，脚本可据此分支）

| 退出码 | 含义 | 异常 |
|---|---|---|
| `0` | DAG 收敛，且需要验收时最终验收已通过 | — |
| `1` | 通用错误 | `OmacError` |
| `2` | 平台或网络错误（含工作项不存在） | `PlatformError`、`WorkItemNotFoundError` |
| `3` | 认证错误（平台 CLI 未登录等） | `AuthError` |
| `5` | 校验失败（lint、证据 schema、未知引擎、1.x 状态） | `ValidationError` |
| `10` | 仍在推进：`dag tick`；`dag run` 达到 `--max-rounds`/`--max-minutes`。重跑同一命令即可 | — |
| `20` | 需要调用方决策，附结构化报告 | `NeedsDecision(message, report)` |

- 码值定义在 `cli/exit_codes.py`；业务层只 `raise` `errors.py` 中的子类，`sys.exit` 只出现在 `cli/main.py`。契约必须向后兼容。
- **报错即教学**：错误要说清缺什么、怎么修，并给出可复制的命令；参数错误时打印错误 + 该命令完整 help，
  让 Agent 错一次就能自我纠正。所有用户可见文本都经过 `omac.i18n.ui(english, chinese)`（默认英文）。
- exit 20 的 JSON 报告形如 `{failed_nodes, blocked_downstream, next_actions}`，`next_actions` 是可复制命令。

### 5.4 幂等、并发与超时

- **意图先于效果**：结果可能未知、且绝不能重放的效果（`RequestMerge`）前，计划里必须先有
  `SetNode(merge_request_state="intent")` 再 `SaveState`；`validate_plan` 拒绝不满足的计划。
- **未知结果绝不重放**：崩溃或结果未知后，下一轮先观察，不重复写；仍不能确认就 blocked + exit 20。
- **漂移即失败关闭**：平台事实与记录不符时停下并说明，不自动"修好"。
- **成功只看观察到的事实**：合并成功只由观察到的 PR 判定，不看命令退出码。
- **`apply` 不重试、不吞异常**；业务失败不自动重试，只有有界计数的回退（§2.2）。
- **并发**：manifest 写锁是非阻塞 `flock`，被占用时立即报错（exit 5）；锁随进程释放，没有租约 / TTL。
  同一工作项的 Worker 控制与 Reviewer 派单用本机锁（`worker_control_lock`、`reviewer_dispatch_lock`），
  持锁后仍必须做一次权威读取；这些锁不是平台级 CAS。多机经 `origin/main` 同步；推送冲突只在每个未推送提交
  都只涉及 manifest 定义与状态目录时才自动 rebase（不 rebase 用户业务提交），推送退避 10 s 起、上限 300 s。
- **超时与预算默认值**（`core/config.py` 等）：`poll_interval` 30 s；`max_parallel` 4；`ci.timeout_minutes`、
  `merge.timeout_minutes` 30；交接终态宽限 30 s（`loop.py`）；Reviewer 未提交宽限 30 s（`decide_reviewer.py`）；
  `retry.worker|ci|review|merge` 各 3（0 表示立即 blocked）；`retry.no_submit_runs` 2（必须 ≥ 1）；
  `acceptance.max_rounds` 3；`reconcile.full_scan_interval_ticks` 20、`full_scan_max_age_seconds` 1800。
  新的超时或预算必须可配置并写进设计 §6，不能散落魔法数。

### 5.5 确定性代码 vs 模型

| 必须由代码确定性完成 | 允许交给模型（Agent），但必须过机器门 |
|---|---|
| 控制流、状态转移、就绪 / 收敛判断 | 设计方案、验收文档、manifest 拆解（plan / acceptance / decompose） |
| 回退计数与预算、何时 blocked、exit 20 报告 | 代码实现与 PR、verification 证据 |
| 门：lint、证据 schema、CI 退出码、PR 观察、覆盖率 | 评审 verdict 与 report（只对其 subject digest 有效） |
| 合并请求的发起与结论、交付身份封存 | 验收结果、修订提案 |
| manifest / 平台写入（只经 `apply`） | — |

Agent 不决定控制流，不自审自放行；角色指南（`guide/`）与模板（`agents/`）只写机制，不写项目策略。

### 5.6 日志与可观测

- 两条输出线：**数据线**（stdout，命令 payload、汇总表，`cli/output.py`）和**进度线**（stderr，structlog 事件，`core/logsetup.py`）。进度永远不进 stdout。
- 事件名是机器契约：使用 `logsetup.EVT_*` 常量（`dispatch`、`verdict`、`node_done`、`needs_decision`…），不要内联新字符串；新增事件要加常量。
- 格式：`--log-format text|json`、`--json-logs`、`OMAC_LOG_FORMAT`；命令输出 `--output json|table`，Agent 与 web 用 JSON。
- `dag run --output json` 打印 `{state, done, running, failed, dispatched, report}`。

### 5.7 隐私与安全红线

- 不在代码、测试、fixture、日志、截图、文档中写入凭据、密钥、本机路径、真实 workspace / Agent / 平台 ID、真实邮箱。
- fixture 必须是合成数据：UUID 用占位形式，邮箱用 `example.com|org|test|invalid`（`test_fixtures_are_synthetic`，CI 强制）；测试不读仓库外路径。
- Agent 模板不得包含机器专属信息（绝对路径、本地服务名、模型 / 供应商设置、凭据）。
- 大载荷（转录、捕获源）只作为平台附件，并按摘要引用。
- smoke 工作流只允许 `schedule` 与 `workflow_dispatch` 触发，绝不对 PR 运行（CI 强制）。

### 5.8 依赖与外部调用

- 运行时依赖只有 `PyYAML`、`structlog`；Python ≥ 3.10，代码须在 3.10 与 3.12 下可用。新增运行时依赖须在 PR 中论证。
- 外部调用只经端口（§3.5）；新的平台行为进适配器，放在语义化端口方法后面。

### 5.9 明确禁止的做法

| 禁止 | 原因 |
|---|---|
| 在适配器外执行或拼接平台 CLI 参数 | 平台语法会泄漏到业务层，换平台要改 Loop |
| 在 `apply.py` 外写端口（CI 规则 `inline_effects`） | 崩溃一致性依赖效果顺序；散落的写入无法审计 |
| 在 `decide_*` 中做 I/O | 决策必须能脱离平台做表驱动测试 |
| 用惰性 import 绕过分层；扩大 `ignore_imports` 或 `TRANSITIONAL` | 基线只能缩小 |
| 为单次事故加特例或常量 | 产品只放通用机制（硬规则 4） |
| 重放结果未知的写入；从命令退出码推断合并成功 | 会产生重复 PR 合并或错误收敛 |
| 让 Agent 决定下一步、或让产出者评审自己 | 违背确定性 Loop 与独立评审 |
| 把转录、源文件等大载荷写进 manifest | manifest 要保持很小、可合并 |
| web 读 manifest、调引擎或改数据 | 人、Agent、web 必须看到同一份事实 |
| 削弱架构测试来让改动通过 | 守护规则就是本文件的执行面 |

## 6. 如何做改动

### 6.1 按改动类型的落点与顺序（先写测试）

| 改动 | 顺序 |
|---|---|
| 新的 Loop 决策 / 转移 | 1) 在 `tests/test_decide_*.py` 写表驱动用例 → 2) 在 `pipeline/decide_*.py` 加 `Observation` 与返回 `Plan` 的纯函数 → 3) 外壳（`loop.py`、`tasks.py`、`delivery.py`）读事实、调用决策、`apply` |
| 新的副作用 | 在 `decide_effects.py` 加冻结 `Effect` → 在 `apply.py::_RUNNERS` 加一行 → 写方法名加入 `tests/_support/arch_rules.py::WRITE_METHODS`（只增不减）→ 更新设计 §7.3.1 效果表 |
| 新的平台事实或写操作 | 端口抽象方法（`engines/store.py|runtime.py|codehost.py`，平台中立措辞）→ 适配器实现 → 用预制 CLI 输出做解析单测（只替换平台 CLI 调用）→ 需要真实平台行为的放 `tests/it` |
| 新的 CLI 命令 / 参数 | `cli/commands/<noun>.py` 的 `register`/`run` → 错误用 `OmacError` 子类 + `ui()` → 支持 `--output json` → 英文 help 加到 `cli/main.py` 的 `_EN_SUBCOMMAND_HELP`/`_EN_ARGUMENT_HELP` → `tests/test_cli_*.py` → 更新 README / 指南 |
| web 需要新数据 | 先加 CLI 命令（`--output json`），再在 `web/api.py` 加原样转发的路由 |
| 新配置键 | `core/config.py`（默认值 + 模块 docstring）→ 设计 §6 → 按 文件 < `OMAC_*` < 参数 的优先级解析 |
| 新的节点运行时事实 | 先确认它不能留在平台工作项上；能留就留。确需加入时改 `_RUNTIME_KEYS` 并说明对 64 KiB 与状态规模测试的影响 |
| 打包文档（`guide/**`、`agents/**`） | 中英两份同时改；跑 `tests/test_guide.py`、`tests/test_i18n.py`、`tests/test_agent_templates.py` |

**不能碰**：退出码含义、`--output json` 的既有字段、三个端口的名字与语义、`lint-imports` 的 `ignore_imports` 基线、
`TRANSITIONAL`、1.x 兼容（不加回）、`docs/dev/`（本地目录，Git 忽略，代码与本文件都不得引用）。

### 6.2 测试规则

1. **先写测试再实现**。新增或变更的可执行行为要覆盖主路径、边界和已知风险。
2. **CI 等价命令**：`python3 -m pytest tests/ -m "not live" -n 4 --dist loadfile`（需要 Docker；没有 Docker 时用
   `-m "not docker and not live"`）。CI 跑一个非 Docker 任务（Python 3.10、3.12）加三个 Docker 分片
   （见 [`docs/github-setup.md`](docs/github-setup.md)）。`live` 测试需要已配置的 Multica workspace，只在明确要求时跑（`-m live`）。
3. **测试保护行为，不保护文本**。不断言源码文本或 `inspect.getsource`（CI 强制），不测试替身本身，
   不重述常量（`assert X == set(X_MAP)`），只 mock 外部边界（平台 CLI、网络、时钟）。测试名重复即缺陷。
4. **fixture 是合成的**。无真实项目数据、本机路径、平台 ID、邮箱或凭据（见 `CONTRIBUTING.md`）；测试不读仓库外路径（CI 强制）。
5. **架构检查**在 `tests/test_architecture.py` 与 `lint-imports`；不得为让改动通过而削弱它们（CI 强制）。
6. 单元测试小而快是架构的结果（纯决策、端口在边界），不是靠指标强制；`integration`、`docker`、`live` 标记只用于选择运行范围。
7. 需要真实服务的测试放 `tests/it`（私有 Docker Multica，绝不静默跳过）。真实 Agent 的 smoke 只手动启动，不卡发布，不对 PR 运行。
8. 改打包文档（`src/omac/guide/**`、`src/omac/agents/**`）时跑 `tests/test_guide.py`、`tests/test_i18n.py`、`tests/test_agent_templates.py`。

### 6.3 验证（按改动类型）

- 源码、测试、构建 / 打包配置、schema 或可执行行为：CI 等价 pytest 全绿，且 `lint-imports` 通过。
  环境准备：`python -m pip install -e ".[test]" import-linter`。
- 纯文档（Markdown、静态图）：`git diff --check`，检查本地链接与资源，有工具时解析 / 渲染改动的格式；涉及打包文档时加跑 §6.2 第 8 条。
- 写完代码不算完成；完成需要测试、构建或实际命令输出这类客观证据。

### 6.4 评审与验收清单（Definition of Done）

- [ ] 改动在计划范围内。
- [ ] 遵守平台边界与分层；没有新的跨层惰性 import；`lint-imports` 基线没有扩大。
- [ ] 没有向 `src/omac` 加入事故专用常量或一次性恢复代码。
- [ ] 新增或变更的行为有回归测试；验证与改动规模相称（§6.3）。
- [ ] 用户可见文本经 `ui()`，错误带修复方法和可复制命令；术语符合 §4。
- [ ] 日志、截图、fixture 不含密钥或个人数据。
- [ ] 命令、行为或规则变化时，同步更新文档、指南、设计文档和本文件。

### 6.5 提交、分支与发布

- 从 `main` 拉主题分支；PR 按 `.github/pull_request_template.md` 写明问题、最小方案、验证命令与结果、风险与兼容性。
- 提交标题沿用仓库现有风格：英文，`<领域>: <一句话说明结果>`（如 `Tests: …`、`Architecture guard: …`、`CI: …`）。
- 设计文档在 `docs/design/README.md`（受 Git 跟踪），代码注释按节号引用；只引用仓库中存在的文档。
- 版本取自 `pyproject.toml`；标签格式 `v*.*.*`；`release.yml` 运行 `scripts/check_release.py`、构建并检查分发包；发布产物不得包含 `tests/fixtures`。

## 7. 已知取舍与历史包袱（不要当成规范）

1. **"进行中 / 终态"有多套定义**：`core/graph.py` 的 `RUNNING = {in_progress, in_review}`、`TERMINAL = {done, cancelled, abandoned}`；
   `pipeline/loop.py` 的 `RUNNING_STATUSES` 还包含 `ci_check`、`merging`，`TERMINAL_STATUSES` 还包含 `blocked`、`failed`；
   `pipeline/report.py` 另有 `TERMINAL_ALL`。新代码判断进行中节点以 `loop.RUNNING_STATUSES` 为准，不要再新建一套集合。
2. **`running_nodes` 标识符在代码中不存在**；术语表沿用设计 §10.2 的名字，代码里对应 `RUNNING_STATUSES`、`_running_observations`。
3. **`failed`、`abandoned`、`cancelled` 不在 `delivery.VALID_MANIFEST_STATUSES` 中**，不能经 `to_platform_status` 同步到平台；
   平台的 `cancelled` 由 Multica 适配器映射为 `BLOCKED`。
4. **`loop.py` 中仍有多处直接调用 `core.manifest.set_node`** 改节点状态，没有走 `SetNode` Effect；`inline_effects` 只拦端口写，不拦 manifest 写。
   新代码应在 Plan 中用 `SetNode` + `SaveState`。
5. **`AcknowledgeAssignment`** 已在 `apply.py` 中实现，但设计 §7.3.1 的效果表未列出，且 `acknowledge_assignment` 不在 `WRITE_METHODS` 里。
6. **`omac.agent_templates` 位于包根**，不属于任何 `lint-imports` 层，却 import 了 `engines.models`，分层契约管不到它。
7. **`lsof`**：旧 AGENTS.md 与设计 §3 把它列为允许的本地工具，但 `src/omac` 中已没有调用；`engines/models.py` 曾被列为端口，实际只是数据类型。
8. **`cli/commands/init_cmd.py`** 是 `inline_effects` 的豁免（交互式向导直接创建 project 和 Agent）；不要把它当成其他命令的范例。
9. **语言混用**：代码注释、docstring 和 argparse help 多为中文，英文 help 由 `cli/main.py` 的映射表替换；用户可见文本以 `ui()` 为准，默认英文。
10. **超大模块**：`pipeline/loop.py`（约 4.4k 行）、`engines/multica.py`（约 2.8k 行）、`pipeline/dispatch.py`、`pipeline/tasks.py`。
    只限制函数长度（150 行），不限制文件长度。新逻辑优先放进对应的 `decide_*` 或 `core` 模块。
11. **`Contract.acceptance`** 是保留的旧版全流程声明字段，新 manifest 用显式的责任语义（`core/acceptance_responsibility.py`）。
12. **`tests/README.md` 已过时**：仍提到不存在的 `e2e` 标记和 "mock adapters"；以本文件 §6.2 和 `pyproject.toml` 的标记为准。
13. **`.gitignore` 中的 `.orchestrator/`** 是旧的 manifest 位置，现行位置是 `.omac/`。
14. **待确认**：manifest 节点 `status` 何时会被写成 `cancelled`（未找到写入点，仅计入收敛）；分支命名是否有约定（目前只有主题分支惯例）。
