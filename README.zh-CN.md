# oh-my-multica

[![CI](https://github.com/xiaohei-info/oh-my-multica/actions/workflows/ci.yml/badge.svg)](https://github.com/xiaohei-info/oh-my-multica/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-1.0.0-blue)](https://github.com/xiaohei-info/oh-my-multica)
[![License](https://img.shields.io/badge/license-MIT-green)](./LICENSE)

[English](README.md) | [简体中文](README.zh-CN.md)

**构建在 [Multica](https://github.com/multica-ai/multica) 之上的生产级 AI 软件交付系统。**

[Multica](https://github.com/multica-ai/multica) 把 Claude Code、Codex 等 Coding Agent 统一接入
工作空间、issue、任务队列和本地 runtime。Agent 可以像团队成员一样接受任务、报告进度和阻塞，
团队也可以统一管理运行机器与可复用 Skill。它为多 Agent 协作解决了任务分配、生命周期、运行时
调度和状态追踪等基础问题。

Multica 更多关注的是 Agent 的执行与协作，但不会替软件项目定义完整的工程交付过程，例如需求怎样进入设计、
如何形成可执行验收标准、多个开发任务如何按依赖并行、什么证据足以证明实现正确、谁来独立评审，
以及何时允许合并和如何从失败中恢复。

oh-my-multica 基于 Multica 优秀的机制设计，在其之上实现了更加完整的软件工程交付控制层，
把一个需求推进为经过设计、开发、验证、评审、合并和最终验收的软件变更。

**Multica 作为一套完整的 Agent Runtime 任务平台管理 Agent 如何工作，
而 oh-my-multica 在其之上管理软件如何完成交付。**

oh-my-multica 要解决的核心问题是：**如何让多个 Coding Agent 在尽量少的人工介入下，
把一个需求完整地设计、实现并交付为生产级软件系统，而不是停留在代码生成、原型或 Demo，并很认真的告诉你已经完成所有功能可以交付。**

> **oh-my-multica 把生产级复杂软件交付的组织门槛降到最低。** 当目标和验收标准明确后，
> 设计、拆解、开发、验证、评审和验收都可以交给可扩展的 Agent Team。影响交付吞吐量的主要资源
> 变成两项：机器数量决定 Agent 的开发并发度，Token 预算决定可以投入多少推理、实现、复测和返工。

## 为什么需要 oh-my-multica

Coding Agent 已经很会写代码。困难通常出现在代码之外：需求在长对话中逐渐漂移，多个 Agent
修改了相互冲突的部分，测试结果只存在于一段自述中，评审者相信作者的总结，或者一个运行数小时
的循环悄悄停止，却没有留下可继续执行的状态。

增加 Agent 数量不会自动解决这些问题。生产级交付需要一个独立于 LLM 的控制系统，负责保存事实、
约束边界、验证结果、推进状态，并在无法自动判断时把决定交还给人。

### 与多 Agent 协作开发产品有什么不同

| 方案                                                                                                                                    | 协作方式                                                                                                                              | 主要解决的问题                                                                           | 应用场景                                                                                                        |
| --------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| 单个 Coding Agent                                                                                                                       | 一个 Agent 在单次会话或工作区中完成任务                                                                                               | 提高单项编码任务的完成速度                                                               | 小型 Bug、局部重构、脚本、原型和边界清晰的功能修改                                                              |
| [Codex App](https://openai.com/index/introducing-the-codex-app/) / [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams) | 人类或 lead Agent 协调多个独立 session、thread 或 worktree                                                                            | 并行开发、上下文隔离和交互式任务分工                                                     | 任务已经拆清楚，开发者愿意持续监督、协调和整合多个 Agent 的结果                                                 |
| [Factory Missions](https://docs.factory.ai/features/missions/overview)                                                                   | Droid 与用户共同制定 feature、milestone 和成功标准，再由 Agent Orchestrator 动态调度 Worker 与 Validator                              | 自主推进大型、多功能、长时间运行的开发 Mission                                           | 仓库已有较高 Agent Readiness 和可脚本化用户 QA，并接受由 Agent Orchestrator 动态调整计划和恢复执行              |
| [OpenAI Symphony](https://openai.com/index/open-source-codex-orchestration-symphony/)                                                    | issue tracker 是控制面，每个 issue 对应独立 Agent workspace，后台服务持续调度和重启 Coding Agent                                      | 消除人工管理大量 Coding Agent session 的负担                                             | 已有成熟 backlog、自动化测试和仓库 Harness，最终保留 Human Review 等交接状态的团队                              |
| [MetaGPT](https://github.com/FoundationAgents/MetaGPT)                                                                                   | 产品经理、架构师、项目经理和工程师等 LLM 角色按照 SOP 交换软件产物                                                                    | 把自然语言需求转化为用户故事、设计、API、文档和代码仓库                                  | 绿地生成、多 Agent 工作流研究，以及愿意自行接入 Git、PR、CI、合并和验收链路的项目                               |
| **oh-my-multica**                                                                                                                        | **强模型负责设计、拆解和质量判断；确定性 Loop 将大量开发与测试工作拆成独立可验证节点，交给高性价比 Worker 模型并行执行；Harness 用 contract、Guide、Sensor 和 evidence 约束结果** | **把大量繁琐开发工作受控地下沉给高性价比模型，在控制 Token 成本的同时，提高复杂工程的开发并发度、交付吞吐量和完成可信度** | **已有远程 Git 仓库，任务规模大且可以按 contract 拆分，希望扩大 Agent 并发，同时保持生产级验证、评审和最终验收标准的项目** |

oh-my-multica 的规模化方式不是给每个任务都使用旗舰模型。高能力模型承担设计、规划和质量判断，
高性价比模型承担数量最多、Token 消耗最大的开发与测试节点。Loop 跟踪每个节点的依赖和状态，Harness
提供行动前约束与行动后反馈；未通过 contract、验证、评审或最终验收的结果会进入返工，不能进入交付链。

Factory Missions 与 oh-my-multica 都能规划、并行开发和执行用户侧 QA，功能范围高度重叠。分界是
过程状态和恢复决策由谁掌握。Factory 的 Orchestrator 本身是 Agent，暂停、重新规划和恢复主要通过
继续与它对话完成。oh-my-multica 则让确定性程序保存交付事实，Agent 只执行有边界的设计、开发、
评审和验收任务。MetaGPT 的核心是把软件公司 SOP 具象化为多角色 LLM 协作；oh-my-multica 的核心是
把远程 Git 仓库中的工程变更推进到
可验证、已合并并通过最终验收的状态。

## 适合谁

- 已经重度使用 AI Coding，希望从亲自盯住每一次对话，转向管理目标、约束和结果的开发者。
- 希望用尽量少的人工接力，持续产出可部署、可维护软件服务的个人、创业团队和工程团队。
- 编程经验有限，但愿意把目标和验收结果说清楚，希望 Agent 按完整架构设计与实施流程完成项目的人。
- 需要同时使用多个 Agent 或多台运行机器，又不希望任务状态散落在终端、聊天记录和个人记忆中的团队。

oh-my-multica 不适合一次性的代码片段生成，也不会替代业务决策。它最有价值的场景，是任务足够复杂、
交付质量值得被明确验证，并且你希望把重复监督交给系统。

## 如何开始

> 前置条件：oh-my-multica 必须运行在已经初始化 Git 并配置了可推送远程仓库的
> 项目中。请从目标项目的仓库根目录启动；使用 Multica engine 时，配置与 manifest 默认通过
> `origin/main` 同步，执行 Agent 通过分支、Pull Request 和合并完成交付。

### For Human

只需要关注三件事：配置可用的 Agent 团队、描述目标、处理系统无法替你决定的问题。

```bash
omac init
omac plan create --name <feature> --goal "<你希望交付的结果>"
```

`plan create` 会推进设计方案、验收文档和 manifest DAG。流程完成后，直接执行输出中给出的
“下一步”命令，让确定性 Loop 接管开发与交付。你可以通过平台 issue 或 `omac web` 查看进度。

### For Agent

请先执行命令查看 guide 指引：

```bash
omac guide workflow
```

在目标项目的仓库根目录中工作：

1. 先运行 `omac init --check`。配置缺失时，按照错误输出完成声明式配置，不调用人类交互向导。
2. 根据用户目标创建新计划，或接续已经存在的 manifest。
3. 执行命令输出中给出的精确“下一步”，不要猜 manifest 路径、命令参数或当前阶段。
4. 保持 `dag run` 在前台运行，直到返回 exit 0 或 exit 20。
5. 只有 exit 0 且 manifest 已收敛时才能报告交付完成。exit 20 时加载 `omac guide recovery`；
   需要改变目标、范围或风险接受程度时，把决定交还给 Human。

## Agent Team 配置最佳实践

oh-my-multica 在 [`src/omac/agents/`](./src/omac/agents) 中提供了 planner、orchestrator、worker、
reviewer、acceptor，以及 architect、backend、frontend、pm 等内置模板。你可以直接使用这些模板，
也可以借鉴其中的 Instructions、职责边界和 Skill 配置来组建自己的 Agent Team。

不需要给所有角色都使用最昂贵的模型。更合理的做法是按照决策影响、任务风险和 Token 消耗分配模型：

| 任务类型   | 典型角色                             | 推荐模型                                                 | 配置理由                                                                                                                                                            |
| ---------- | ------------------------------------ | -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 设计与规划 | planner、architect、orchestrator     | GPT、Claude 的旗舰模型，或性能相当的其他第一梯队模型     | 调用次数相对少，但设计和拆解错误会被所有下游任务放大                                                                                                                |
| 评审与验收 | reviewer、acceptor                   | GPT、Claude 的次级旗舰模型，或性能相当的其他第二梯队模型 | 保持独立判断和评审质量，同时控制复跑与验收成本                                                                                                                      |
| 开发与测试 | worker、backend、frontend 等执行角色 | 高性价比商业模型、成熟开源模型或其他第三梯队模型         | 任务数量、并发度和 Token 消耗最大，清晰 contract 与验证门可以约束执行结果，**不用担心低性能模型会交付不符合预期或者超出边界的结果！这正是我们要解决的问题！** |

## 核心设计：Loop Engineering × Harness Engineering

**oh-my-multica 是 Loop Engineering 与 Harness Engineering 在生产级软件交付场景中的工程化实现。**
Loop 负责持续读取事实、消费反馈、推进工作，并判断下一步与停止条件；Harness 负责把目标、上下文、
工具、约束、验证和评审编码成每一轮执行都必须遵守的环境。二者结合后，Agent 可以自主完成需要
推理的工作，但不能自行定义交付事实、跳过质量门或宣布整个项目完成。

### Loop Engineering：把反馈、执行与完成条件连接成闭环

[Anthropic《Building Effective Agents》](https://www.anthropic.com/engineering/building-effective-agents)
将 Agent 描述为：LLM 根据环境反馈反复使用工具，每一步都从环境获取事实，再决定继续执行、纠正、
请求人工判断或在满足停止条件时结束。Anthropic 同时区分了 workflow 与 agent：前者由程序预先定义
执行路径，后者由模型动态决定如何完成任务。在
[Claude Agent SDK 的官方实践](https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk)中，
这条反馈循环被进一步概括为 **gather context → take action → verify work → repeat**。

[OpenAI 的 Agent Improvement Loop](https://developers.openai.com/cookbook/examples/agents_sdk/agent_improvement_loop)
进一步把 traces、反馈、评测、Harness 调整、实现和再验证连接成一个可运行的改进循环；
[OpenAI 的 Harness Engineering 实践](https://openai.com/index/harness-engineering/)则把大型目标拆成设计、
开发、评审和测试等边界清晰的工作单元，并让反馈持续返回执行过程，直到验证与评审真正通过。

这些实践的共同点不是让 Agent “多跑几次”，而是构造一个能够持续完成
**观察事实 → 决定下一步 → 执行动作 → 验证结果 → 消费反馈 → 判断是否结束**的闭环。

按照 Anthropic 的分类，oh-my-multica 的外层控制面是确定性 workflow，DAG 节点内部才是自主 agent。
这是有意的混合设计：模型负责设计、拆解、编码、评审和验收等需要推理的任务；程序负责保存状态、
计算依赖、控制并发、执行质量门和决定整个交付是否收敛。

```text
reconcile → result collection（collect_results）→ 证据与交付门 → ready nodes → dispatch → converged / exit 20
```

| Loop Engineering 要素 | oh-my-multica 的落地 |
| --------------------- | -------------------- |
| 从环境读取事实 | `reconcile` 对齐 manifest 与平台工单；Git、结构化 evidence、review report，以及按配置启用的 Pull Request 和 CI 都是可检查事实 |
| 把目标拆成可推进的小步 | 设计方案与验收定义先转成 contract-driven manifest DAG，再按依赖和 `max_parallel` 计算 ready nodes |
| 在边界内自主执行 | 每个 Agent 只收到当前节点的 task、context、contract、authority 和最小 `guide_refs`，自行决定节点内部如何完成 |
| 根据反馈纠正 | `collect_results` 消费验证、CI、Reviewer 与 merge 结果；失败进入有界返工，无法自动处理的失败进入显式恢复 |
| 跨会话持续运行 | 状态持久化在 manifest、平台与 Git 中；新的 Human 或 Controller Agent 可以从同一事实继续，而不依赖上一轮上下文记忆 |
| 明确停止条件 | 仍有运行节点就继续；需要决策时返回 exit 20；所有节点收敛并通过最终验收后才返回 exit 0 |

OpenAI 的 Agent Improvement Loop 主要描述持续改进 Agent 与 Harness 的“外循环”；oh-my-multica
当前直接落地的是生产软件交付的“内循环”：每轮都把执行结果变成证据和反馈，再决定推进、返工、
停止或请求决策。Loop 不靠某个监督 Agent “记得继续”，也不会因为上下文重置而失去交付状态。

### Harness Engineering：把工程判断编码进环境

仅有循环还不够。一个错误目标可以被循环执行得又快又稳定。Harness Engineering 关注模型之外的
整个工作环境：知识如何提供、架构如何约束、结果如何验证、错误如何反馈、状态如何保存。
[Anthropic 关于长期运行 Agent Harness 的实践](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
强调增量推进、持久化工程产物以及跨上下文恢复；OpenAI 则强调把仓库建设成 Agent 可读的事实系统，
并将测试、验证、评审、反馈和恢复机制编码进环境，而不是依赖提示词或人工盯守。

[Harness Engineering](https://martinfowler.com/articles/harness-engineering.html) 可以从两个维度理解：
Guide 在 Agent 行动前提供前馈约束，Sensor 在行动后提供反馈；Computational 依靠确定性程序判断，
Inferential 依靠模型完成语义分析。成熟 Harness 通常会组合使用四个象限，并由一个可信的 Loop
消费这些信号，决定继续、返工、停止或请求人工决策。

```mermaid
flowchart TB
    subgraph GUIDES[Guide · 行动前]
        direction LR
        CG[Computational Guide<br/>manifest schema · DAG 依赖<br/>authority · 精确命令]
        IG[Inferential Guide<br/>设计与验收文档 · AGENTS.md<br/>Role Guide · Skills]
    end

    AGENT[有边界的 Agent 任务<br/>设计 · 拆解 · 开发 · 评审 · 验收]

    subgraph SENSORS[Sensor · 行动后]
        direction LR
        CS[Computational Sensor<br/>evidence schema · tests · CI<br/>PR · merge · reconcile]
        IS[Inferential Sensor<br/>独立设计评审 · Reviewer<br/>Acceptor 用户流程验收]
    end

    LOOP[确定性 Loop<br/>result collection · gate · dispatch<br/>retry / exit 20 / done]
    HUMAN[Human / Controller Agent]

    CG --> AGENT
    IG --> AGENT
    AGENT --> CS
    AGENT --> IS
    CS --> LOOP
    IS --> LOOP
    LOOP -->|通过或有界返工| AGENT
    LOOP -->|无法自动决定| HUMAN
```

可编辑源文件：[Harness 四象限](docs/diagrams/oh-my-multica-harness-quadrants.drawio)。

### oh-my-multica 在四个象限中做了什么

|                          | Computational：确定性、快速、可重复                                                                           | Inferential：语义判断、成本更高                                                          |
| ------------------------ | ------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| **Guide：行动前**  | manifest schema 与 lint、DAG 依赖、contract 必填字段、scope 与 authority、精确下一步命令                      | 设计与验收文档、`AGENTS.md`、Role Guide、Skills、上游产物和按任务披露的 `guide_refs` |
| **Sensor：行动后** | evidence schema、verification commands、lint、type check、tests、CI、PR/merge 状态、manifest 与平台 reconcile | 设计和 DAG 独立评审、Reviewer 语义审查、Acceptor 按用户 flow 做最终验收                  |

Computational Sensor 通过，并不代表软件已经完成；它只能证明可程序化检查的部分成立。Inferential
Sensor 负责判断需求、架构、风险和用户结果是否对齐。oh-my-multica 的 Loop 同时读取两类结果：通过
才推进，失败进入有界返工，无法自动判断时返回 exit 20，所有节点合并并通过最终验收后才返回 exit 0。

### 其他协作产品如何使用 Harness

下面比较的是各产品的默认重心，不是经过定制后的能力上限。

| 方案                                | Guide：行动前                                                                 | Sensor：行动后                                                                    | 谁负责关闭反馈回路                                                  |
| ----------------------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Codex App / Claude Code Agent Teams | 仓库说明、Skills、工具权限、worktree 和任务上下文                             | 仓库测试、diff、Agent 自检与人工 Review                                           | Human 或 lead Agent 持续观察并决定下一步                            |
| Factory Missions                    | feature、milestone、成功标准、Skills、hooks 和项目配置                        | Validator Worker、可脚本化用户 QA 与 Mission 状态                                 | Agent Orchestrator 动态重规划；卡住时由 Human 通过对话介入          |
| OpenAI Symphony                     | issue 状态机、仓库内`WORKFLOW.md`、独立 workspace 和运行策略                | 仓库测试、guardrail、Agent 结果和 Human Review 状态                               | 后台服务负责调度与重启，Coding Agent 推进 issue，Human 完成最终交接 |
| MetaGPT                             | 产品、架构、项目管理和工程角色的 Prompt、SOP 与上游产物                       | 角色之间的语义反馈，以及具体工作流自行加入的测试或检查                            | LLM 角色工作流负责继续生成和修订产物                                |
| **oh-my-multica**             | **版本化设计、验收定义、contract、manifest DAG、Role Guide 和权限边界** | **结构化 evidence、独立复跑、CI、Reviewer、merge gate 和 final acceptance** | **确定性 CLI 持有状态机；Agent 不得凭自述改变完成状态**       |

Factory Missions 已经覆盖四个象限中的大量能力。oh-my-multica 进一步把 Guide 和 Sensor 产生的信号
纳入程序掌握的交付状态机。MetaGPT 侧重通过 SOP 组织 LLM 角色；
Symphony 侧重让 issue 持续获得 Agent 执行；oh-my-multica 侧重定义并执行“什么证据足以让一个生产级
软件变更继续推进，以及什么时候整个交付真正结束”。

oh-my-multica 把上述 Loop 与 Harness 原则落实为围绕生产软件交付的 CLI 协议、状态机和证据模型：
Harness 产生前馈约束与反馈信号，确定性 Loop 消费这些信号并推进状态，Agent 则在明确边界内完成
最适合模型处理的推理和执行工作。

## oh-my-multica 在 Multica 之上增加了什么

| 机制             | oh-my-multica 的做法                                                         | 解决的问题                           |
| ---------------- | ---------------------------------------------------------------------------- | ------------------------------------ |
| 确定性控制反转   | CLI 持有主循环，Agent 是有终点的单次执行者                                   | 防止监督 Agent 跑偏、遗忘或提前退出  |
| 契约化计划流水线 | 设计方案 → 验收文档 → manifest DAG，阶段之间有 machine gate 和 review gate | 防止需求、设计、拆解和实现各说各话   |
| 可验证 DAG       | 每个节点都有依赖、owner、reviewer、acceptance、验证命令和集成门              | 让并行建立在边界上，而不是碰运气     |
| 独立质量裁决     | worker 不自审自放行；reviewer 独立复跑；acceptor 按 flow 做最终验收          | 避免把作者自述当成事实               |
| 结构化证据       | verification、review report、acceptance results 都有 schema 和提交门         | 让“通过”可以被程序检查和后续追溯   |
| 交付收口         | 可配置 CI、PR 合并和总控验收；失败回到有界返工                               | 避免把“代码写完”误认为“已经交付” |
| 持久化与恢复     | 状态保存在 manifest 与平台；重跑先 reconcile；失败返回 exit 20               | 中断后继续，而不是重新提示一遍       |
| 平台适配边界     | pipeline 只依赖`WorkItemStore` 与 `AgentRuntime`                         | 后续接入其他任务平台时不改交付流程   |

## 整体架构

oh-my-multica 不替代 Multica，也不替代 Coding Agent。它位于调用者与执行平台之间，负责把软件工程事实
转换成可执行状态，再通过统一接口使用 Multica 的任务与运行时能力。

```mermaid
flowchart TB
    subgraph ENTRY[调用入口]
        H[Human]
        C[Controller Agent / CI]
        W[只读 Web]
    end

    subgraph OH_MY_MULTICA[oh-my-multica · 生产级交付控制层]
        PLAN[计划流水线<br/>设计 · 验收 · DAG 拆解]
        LOOP[确定性 Loop<br/>collect_results · ready_nodes · dispatch]
        STATE[工程事实<br/>contract · manifest · AGENTS.md]
        GATE[质量与交付门<br/>evidence · CI · review · merge · acceptance]
        RECOVERY[恢复与决策<br/>reconcile · exit 20 · retry/accept/abandon]

        PLAN --> STATE
        STATE --> LOOP
        LOOP --> GATE
        GATE --> LOOP
        LOOP --> RECOVERY
    end

    subgraph PORTS[平台中立接口]
        STORE[WorkItemStore]
        RUNTIME[AgentRuntime]
    end

    subgraph PLATFORM[执行与协作底座]
        MULTICA[Multica<br/>workspace · issue · agent · skill · runtime]
        AGENTS[Coding Agent Runtimes<br/>Claude Code · Codex · ...]
        REPO[Git Repository / PR]
        CI[CI / Tests]
    end

    H --> PLAN
    C --> PLAN
    C --> LOOP
    W --> LOOP
    LOOP --> STORE
    LOOP --> RUNTIME
    STORE --> MULTICA
    RUNTIME --> MULTICA
    MULTICA --> AGENTS
    AGENTS --> REPO
    REPO --> CI
    CI --> GATE
```

可编辑源文件：[整体架构图](docs/diagrams/omac-architecture.drawio)。

### 架构边界

- pipeline 与 CLI 只能调用 `WorkItemStore` 和 `AgentRuntime`，不能直接执行平台 CLI。
- Multica、GitHub 及未来其他平台的差异封装在 engine adapter 内。
- Web 层只解析参数、调用同一 command function，并原样返回 JSON；Human、Agent 和 Web
  看到的是同一套事实。

## 从需求到交付

下面的泳道展示标准路径。设计、验收、拆解和开发都可以发生有界返工；系统只有在证据满足合同后
才推进状态。无法自动处理的失败不会被吞掉，而是以 exit 20 和下一步命令交还调用者。

```mermaid
sequenceDiagram
    autonumber
    actor Human as Human / Controller
    participant CONTROL as oh-my-multica Loop
    participant PO as Planner / Orchestrator
    participant Worker as Worker
    participant Reviewer as Reviewer
    participant Platform as Multica / Git / CI
    participant Acceptor as Acceptor

    Human->>CONTROL: 提交目标或设计文档
    CONTROL->>PO: 生成设计方案与项目规则
    PO-->>CONTROL: 提交结构化产物
    CONTROL->>Reviewer: 独立评审设计
    alt 评审拒绝且未耗尽
        Reviewer-->>PO: 返回 blockers
        PO-->>CONTROL: 修订后重新提交
    else 设计通过
        CONTROL->>PO: 生成验收文档并拆解 manifest DAG
    end

    loop 直到 DAG 收敛
        CONTROL->>CONTROL: 执行 result collection 并计算 ready nodes
        par 并行开发 ready nodes
            CONTROL->>Platform: 创建/分配 work item
            Platform->>Worker: 唤醒 Agent Runtime
            Worker->>Platform: 推送 PR 与 verification evidence
        end
        Platform-->>CONTROL: 返回任务、PR 与 CI 状态
        CONTROL->>CONTROL: 校验证据与 CI 门
        CONTROL->>Reviewer: 转派同一 work item，独立复跑
        alt review / CI / merge 失败且未耗尽
            Reviewer-->>Worker: 结构化反馈并返工
        else 节点通过
            CONTROL->>Platform: 合并并将节点收口为 done
        else 无法自动决定
            CONTROL-->>Human: exit 20 + 结构化报告 + 可复制命令
        end
    end

    CONTROL->>Acceptor: 按 acceptance flows 做最终端到端验收
    alt 全部 flow 通过
        Acceptor-->>CONTROL: acceptance results = pass
        CONTROL-->>Human: exit 0 · 交付完成
    else 发现缺口
        Acceptor-->>PO: 失败 flow 与证据
        PO-->>CONTROL: 追加增量修复节点，重新进入 DAG Loop
    end
```

可编辑源文件：[执行泳道图](docs/diagrams/omac-execution-flow.drawio)。

## “面向生产级”具体意味着什么

oh-my-multica 不承诺任何 Agent 生成的代码天然可以上线。生产质量取决于需求是否正确、合同是否完整、
验证命令是否有效、CI 是否配置，以及 reviewer 和 acceptor 是否具备足够能力。

oh-my-multica 提供的保证更务实：这些关键条件会成为流程中的显式事实和检查点，而不是藏在人脑或聊天记录里。

| 生产交付要求           | oh-my-multica 中的落点                                                 |
| ---------------------- | ---------------------------------------------------------------------- |
| 需求不漂移             | design problem / non-goals / flows 与 acceptance flow id               |
| 架构可维护             | 核心数据所有权、模块边界、跨模块契约、项目级`AGENTS.md`              |
| 改动不破坏既有行为     | contract 的 source of truth、non-goals、integration gates 与兼容性要求 |
| 结果可以复现           | verification commands、env setup、结构化 evidence                      |
| 作者不能自证正确       | worker 与 reviewer 分离，最终由 acceptor 按用户旅程验收                |
| 代码真正进入交付链     | PR、CI、merge 与 final acceptance 可纳入完成条件                       |
| 长任务可以中断续跑     | manifest / work item 持久化、幂等 tick、reconcile                      |
| 自动化不能越权替人决定 | 有界返工；超出边界统一 exit 20                                         |

## 安装

前置条件：

- Python 3.10 或更高版本。
- 目标项目已经初始化 Git、至少存在一次提交，并配置了可推送的 `origin` 远程仓库；
  使用 Multica engine 时默认通过 `origin/main` 同步 oh-my-multica 状态。
- 使用 `pipx` 隔离安装 oh-my-multica 的 `omac` CLI。
- 使用 Multica engine 时，[安装 Multica CLI](https://github.com/multica-ai/multica/blob/main/CLI_INSTALL.md)
  并完成 `multica login`。

```bash
git clone https://github.com/xiaohei-info/oh-my-multica.git
cd oh-my-multica
pipx install .

omac --version
```

仓库内置 mock engine，不依赖外部平台，适合本地验证、CI 和首次试跑。

## 文档入口

- `omac guide workflow`：从计划到交付的稳定工作流。
- `omac guide role <name>`：planner、orchestrator、worker、reviewer、acceptor 的职责边界。
- `omac guide artifact <name>`：design、acceptance、manifest、evidence 的产物合同。
- `omac guide recovery`：exit 20 后的恢复协议。
- `omac <command> --help`：当前版本的命令合同与完整参数。
- [CHANGELOG.md](./CHANGELOG.md)：用户可见的版本变化。

README 负责解释项目是什么、为什么存在以及如何开始。执行中的精确事实永远以
`omac work show`、Guide 和命令帮助为准。

## 开发与验证

```bash
pip install -e .
pip install pytest
python3 -m pytest tests/
```

`live` 测试需要已经登录的 Multica 环境。项目变更只有在代码、测试和必要文档同时完成，且完整
测试通过后才算交付。

## 参与贡献

欢迎通过 issue 讨论问题和设计，通过 Pull Request 提交改进。涉及行为变化的提交需要同时提供
回归测试，并保持退出码、术语、engine 接口和 Web 数据边界向后兼容。提交前请运行完整测试。

## License

[MIT](./LICENSE)
