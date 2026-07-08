# omac 整体工作流

omac 是确定性 CLI 驱动的多 Agent 并行开发编排:Loop 驱动 Agent,LLM 只做
被派发的、有终点的专家任务(planner / orchestrator / reviewer / worker / acceptor)。

## 标准路径

1. 一次性配置:
   - 人类入口:`omac init` 交互式选择 workspace、角色映射与 workflow 默认策略
   - agent/CI 入口:用 `omac config set ...` 写 `.omac/config.yaml`,然后只跑
     `omac init --check`;不要运行裸 `omac init`,它是人类交互式向导
2. `omac plan create --name <feature> [--goal 需求 | --doc 设计方案文档]` —— 设计方案 → 验收文档
   → 拆解,默认行为读 `config.workflow`,
   产出 `.omac/<feature>.yaml`( + `.acceptance.yaml`)
   - `--goal <需求>`:planner 据此编写设计方案(从需求出发的正道入口)
   - `--doc <设计方案文档>`:已有方案,跳过 planner 设计环节
   - **人机确认门(默认开)**:设计方案、验收两个环节产出后,先由你确认「是否满足需求」
     再进 reviewer 评审。可用 `workflow.human_in_loop=false` 关闭。通过标准 = 把该 issue 流转到 **done**(omac 识别到后翻回
     in_review 继续评审)。手动放行:`omac plan confirm --name <feature>`;
     无人值守入口用 `--no-confirm` 关闭。
   - **provenance**:验收 issue 引用计划 issue、拆解 issue 引用计划+验收 issue,
     manifest `meta.source_issues` 记录三个源头 —— 后续任务有分歧以源头 issue 为准。
3. `omac dag run .omac/<feature>.yaml` —— 确定性 loop:
   回收结果 → 计算就绪节点 → 派发,直到收敛;收敛后进入总控验收外层循环
   - exit 0:验收全部 pass,真正可交付
   - exit 20:无法继续推进,stdout 有结构化报告(含可执行的下一步命令)
4. exit 20 后:`omac dag status` 看全景 → `omac node show <key>` 看证据链
   → `omac node retry|accept|abandon` 决策 → 重跑 `omac dag run`(重跑即续跑)

## 阶段 × Agent 工作导航

| 阶段 | 主责 agent | 真实入口 | 成功交付 | 下一跳 |
|---|---|---|---|---|
| 初始化 | 人 / 入口 agent | 人:`omac init`;agent:`omac config set ...` → `omac init --check` | `.omac/config.yaml` 中 workspace、project、roles、workflow 可用 | `omac plan create` |
| 方案设计 | planner | `omac plan create --name <feature> --goal <需求>` | 设计方案 issue done,必要时经 `omac plan confirm` 放行 | reviewer 评审方案 |
| 验收定义 | planner | 同一条 `omac plan create` 流程 | 验收文档 issue done,端到端验收点明确 | reviewer 评审验收 |
| DAG 拆解 | orchestrator | `omac plan create` 内置拆解段或 `--doc <设计方案文档>` | `.omac/<feature>.yaml`,每节点 contract 可执行 | reviewer 评审 manifest |
| 派发执行 | 引擎 | `omac dag run <manifest>` | 就绪节点变为 work item,派给 worker | worker 读 `omac guide worker` |
| 结果回收 | 引擎 | `collect_results` | 证据过门、CI/merge/review 状态推进 | `review_dispatch` 或 `node_done` |
| 独立评审 | reviewer | `omac work show <issue-id>` → `omac work submit --verdict ...` | `pass` / `reject` / `pass-with-nits` report | done / 返工 / `needs_decision` |
| 总控验收 | acceptor | DAG 全 done 后由引擎触发验收外层循环 | 验收文档逐项 pass/fail | 通过 exit 0,失败可增量扩展 |
| 人工决策 | 人 / 入口 agent | `omac dag status` → `omac node show` → `omac node retry|accept|abandon` | blocked/failed/needs_decision 被显式处理 | 重跑 `omac dag run` |

`acceptance.max_rounds` 控制总控验收最多增量修复轮数;耗尽后返回 exit 20,
由上表「人工决策」处理。不要让 agent 猜下一步:每个阶段先看本表,再进入对应 guide。

## 入口形态(谁来跑 omac)

omac 有三种入口调用者,消费同一套 CLI:**人(终端)/ Agent(Claude Code、Multica agent 等)
/ Web UI**(设计 §1.4)。控制反转的边界必须分清:

- ✅ **agent 作为入口 = 启动确定性 loop 进程,并跑全流程** —— 不是只 `dag run`,而是
  `omac config set ...` → `omac init --check` → `omac plan create`(设计方案 / 拆 DAG)
  → `omac dag run`(驱动到收敛)整条链。
  loop 的决策逻辑在**代码**里跑(sync→decide→dispatch→sleep),agent 只是把进程拉起来,
  它的上下文**不参与每轮轮询** —— token 成本 ≈ 0,和人在终端敲完命令晾着等一样。
- ❌ **被否决的反模式** = 让 **agent 的推理上下文本身充当 while 轮询循环**(每轮 LLM 推理 = 一次 tick):
  太贵(token 随 DAG 时长线性涨)、不可靠(LLM 不擅长当 while 宿主,几轮就退出)。见设计 §1.2。

**判据**:loop 的**决策**在谁手里 —— 在确定性代码 = 对;在 agent 上下文 = 错。谁**启动**进程无所谓。

**承载约束**(对人和 agent 一视同仁):`plan create && dag run` 是长命进程,承载它的机器/会话要稳;
中断也不丢 —— 状态全在 manifest + 平台、循环幂等,重跑即续跑、支持跨机接力(可用 `--max-minutes` 分段)。

### agent 初始化协议

裸 `omac init` 是人类交互式向导。agent/CI 的 stdin 通常不是 TTY,运行裸 `omac init`
会 exit 5 并打印 `omac config set` 示例;正确路径是声明式写配置后体检:

```bash
omac config set engine mock
omac config set workspace mock-workspace
omac config set roles.planner alice
omac config set roles.orchestrator bob
omac config set roles.workers '["alice"]'
omac config set roles.reviewers '["charlie"]'
omac config set workflow.human_in_loop false
omac config set workflow.acceptance_doc true
omac config set workflow.goal_required true
omac init --check
```

`workflow.review`、`workflow.acceptance_doc`、`workflow.human_in_loop` 与
`workflow.goal_required` 是项目默认策略;`plan create/resume` 的现有 `--no-*`
参数只做单次临时关闭。

### issue 辨识边界(同一 project / 同一批 agent 混用时)

- **标题带 `[DAG:...]` 前缀** = omac 派发的执行任务 → 被派 agent 走 `omac work show/submit`。
- **无此前缀** = 普通 issue,按其 body 常规处理;body 若明确要求运行 omac 命令(如让 agent 作为
  入口跑 `omac plan create` / `omac dag run`),照 body 执行。`work show/submit` 只认前缀,不误伤。
- 手工建的、不想走 omac 的简单 issue:天然无前缀、无 bootstrap,不会进 omac 流程;
  想彻底隔离就**别提进 omac 专属 project**(一个 project = 一个 omac 编排实例)。

## 关键原则

- issue 的范围 = 一个完整阶段:产出、评审、回退都在同一条 issue 上,交接 = 转派
- 重试是显式决策,不自动发生
- 全部状态在 manifest + 平台,任意中断可恢复,支持跨机器接力
- 前置:runtime 机器需安装 omac(`pipx install omac`)与已登录的平台 CLI

## 执行编排(阶段 C — 跑引擎)

`omac dag run` 是**前台阻塞**进程 —— 内置主循环(回收结果 → 派发 → 轮询)
一直跑到 DAG 终态(全 done 或失败收口)才返回。你必须在当前轮以前台同步方式
把它跑到返回、并收集其退出结果,才算"在监督"。

- ❌ 不要放后台、不要 `&`、不要寄望"未来某轮再看"。本轮 turn 退出 = 本次任务结束,
  没有被收集的后台进程不会替你推进 DAG。
- ❌ 没有活跃的 `omac dag run` 在跑,就**禁止**在回复里说"继续监督/持续观察/等待完成"。
- ✅ 要么前台把 `omac dag run` 跑到返回再汇报;要么明确告诉用户"当前未在监督,要我启动/继续吗"。
- DAG 大、单轮久:仍前台跑,靠 `max-parallel` 控并发、靠日志看进度,而不是退出后口头声称在盯。

### 引擎自动行为(固定逻辑,不可配)

- **就绪节点计算**: `status=todo` 且 `blocked_by` 全 done → 就绪(可派)
- **失败隔离**: 某节点 failed → 其下游标记为 blocked → 不再派发
- **幂等重跑**: manifest 是唯一口径,对同一 manifest 多次执行:
  - 已 done 且有 work_item_id 的节点:精准取,0 新建
  - blocked/failed 的节点:重置为 todo 重试,复用原 work_item_id
  - 无 work_item_id 的节点:首次建 work item 并回填
- 失败处理与中断续跑是同一条路径:改完 manifest 重跑即可

### 观测(给编排者)

进度事件走 **stderr**(永不污染 stdout 数据线)。一批事件、两种形态:

| 场景 | 命令 | 事件形态 |
|---|---|---|
| 人看(交互终端) | `omac dag run` | 人类文本,默认 |
| 机器/CI/上层解析 | `omac dag run --json-logs` 或 `OMAC_LOG_FORMAT=json` | JSON-lines |

事件清单(每次状态跃迁里程碑,不刷 poll):
`dispatch`(派单) / `review_dispatch`(转 reviewer) / `verdict`(判决) /
`revision`(回退,带 `gate`: worker/ci/review/guard) / `node_done` / `node_failed` /
`human_gate_wait`(confirm 门干等人挪 issue —— 无此事件会看着像卡死) /
`cascade_blocked`(失败连坐) / `unblock`(上游修复解封) / `converged` / `needs_decision`。

上层编排器(Multica 跑 omac、CI)接管时 `2> events.jsonl` 重定向 stderr 即可获得可解析流。

### manifest 唯一口径

manifest 文件是全局唯一口径 —— 不依赖 checkpoint、Run 存储、event log 等自造存储。
节点状态直接写进 manifest 的 `status` 字段,经 git 流转。每轮 `tick` 落盘后回写(commit + push):
- manifest 有变更即 commit + push 一次(无变更幂等跳过)
- push 失败醒目告警但不中断编排(本机推进不阻塞,跨机口径可能滞后)

> **git 回写开关(`sync_enabled`)**:真实引擎(multica)**默认开** —— 隔离区 agent 只能 clone
> main、信息源只有远程仓库,`.omac` 状态必须上 main 才能被读到,这是架构刚需,不是可选项。
> mock 本地跑**默认关**(不碰业务仓库 git)。`OMAC_GIT_SYNC` 可显式覆盖(`=1` 强开 / `=0` 强关)。
>
> **派单前自动同步**:`plan create` / `dag run` 开跑前,真实引擎下自动把 `.omac/config.yaml`
> 同步到 `main`(脏就 commit+push,已提交没推就补推,幂等静默)—— config 是 omac 自有状态,
> 无需用户手动操作。只有两种无法自动修复时才硬报错:config 不存在(引导 `omac init`)、
> push 被远程拒(分叉,引导手动 `git pull --rebase`)。避免 agent 在隔离区里因读不到 config 神秘失败。

### reconcile(跨机器接力)

manifest 经 git 流转,可能是别的机器 commit 来的、带部分状态。Phase 2 启动时做一次
全局 reconcile:逐节点拿 `work_item_id` 去平台核对真实状态 vs manifest 记录,补齐 gap。
manifest 是口径,平台是实况,二者对齐后才往下跑。

## 收尾(Closeout)

> **收尾前置自检(防假监督)**:在汇报"完成/已收尾"前,先核对 manifest —— 若存在非终态节点
> (todo/in_progress/in_review)且当前没有活跃的 `omac dag run` 在前台跑,你**不算在监督**。
> 此时只有两条诚实路径:① 前台再跑 `omac dag run` 推进到终态;② 明确向用户说"尚未收敛、
> 当前未在监督,还剩哪些非终态节点、需要我继续吗"。**禁止**在仍有非终态节点时用"持续监督中/等待完成"收尾。

全 done 或判断可收尾时:
1. **汇总 digest**: 哪些 done、哪些 failed、PR 链接列表、已知问题与限制
2. **向用户汇报**: 交付物(PR 列表 / 集成分支)、验收状态、后续建议

## 派活与合并纪律

### 派活铁律(防跑偏)

派出的 worker 必须:
- 只 import 共享契约、禁重定义
- 守红线/非目标
- 从集成分支切
- PR base 指向集成分支
- 过 CI + 独立评审才允许关闭

**关闭前必须过闸**: worker 的回报只是线索,最终以 reviewer 判决与引擎状态为准,
不要凭 worker 的 prose 总结就放行。

### 合并纪律(避免并发踩踏)

试合并、解冲突、合入都在专用集成 worktree 做(`git worktree add ../integ <集成分支>`),
**绝不在共享主工作树留半成品 merge 态** —— 并发的 reviewer 会读到脏主树、甚至误
`git reset --hard` 冲掉你未提交的工作。

Reviewer 侧对应铁律:只读共享态、不动主树。两边一起守,并发才安全。

## 禁止事项

1. ❌ 不要手工改 issue metadata(编译单向写入,引擎只读)
2. ❌ 不要跳过 lint(manifest 错误会导致引擎行为异常)
3. ❌ 不要在引擎运行中手工改 issue status(会破坏状态机)
4. ❌ 不要混用本机制与 kanban 看板机制(两套并行,按基底择一)
5. ❌ 不要自己实现循环逻辑(引擎是固定的,你只负责拆解)
6. ❌ 不要拆成数百微任务(粒度 = 并行单元,半天~两天可收口)
7. ❌ 不要在没有活跃 `omac dag run` 前台进程时声称"在监督/持续观察/等待完成"(假监督)
8. ❌ 不要把监督寄望于后台进程或"未来某轮" —— turn 退出即任务结束


## 派发 body 模板 (dispatch prompt)

以下是派发给 worker / reviewer / architect 的权威文本(随任务送达,版本永远正确)。
`src/omac/pipeline/dispatch.py`(P2.3 落地)注入时,内容与此处保持同源。

### Worker 派发

```
🎯 你被派发为 worker 执行此任务

**任务信息**:
- Issue ID: <issue-id>
- 任务标题: <title>
- 集成分支: <integration-branch>

**关键约束(必读)**:
1. **只读共享态**:契约、入口、映射表位于 contract 的 required_contracts / source_of_truth,只 import,禁重定义
2. **守红线**:见 issue body 🚫 红线部分
3. **非目标边界**:见 issue body 🚧 范围边界部分
4. **唯一口径文档**:见 issue body 定位表(全文读完对应章节)
5. **PR base**:必须指向 contract.pr_base,不是 master

**执行协议**:
参照 `omac guide worker` 的完整执行清单(8 步)

**完成标准**:
- 测试全绿(全量测试套件,不只本模块)
- 改动分支覆盖 ≥ coverage_gate(缺省 90%):`diff-cover` 退出码 0
- 集成测试全绿:contract.integration_gates 每个 gate 都有 verification 证据,且锚定 source_of_truth / delivery_goal
- PR 已产出并指向正确 base
- 经 `omac work submit --pr-url --verification-file` 写入 artifacts + verification 并通过自校验
- work item 标 done(指派 reviewer + 转 in_review 由引擎回收完成)

**如遇阻塞**:
在 issue 评论坦诚说明原因 + 卡点,回流给编排器,不要硬撑
```

### Reviewer 派发

```
🔍 你被派发为 reviewer 评审此任务

**任务信息**:
- Issue ID: <issue-id>
- Worker: <worker-agent-name>
- PR: <pr-url>(从 artifacts.pr_url 读取)

**关键约束(必读)**:
1. **收活铁律**:先 `git diff <base>...<head>` 看真实改动,再独立复跑测试,绝不只凭 worker 自述
1a. **集成门铁律**:逐个复跑 verification.integration_gates 的 commands,核对 metrics/artifacts,按 source_of_truth / delivery_goal 判断是否覆盖真实交付目标
2. **只读共享态**:审查时确认 worker 只 import 共享契约、未重定义
3. **对照三份材料**:
   - Issue body 的唯一口径文档
   - Issue body 的约束与红线
   - Git diff 的真实改动
4. **独立复跑改动分支覆盖**:亲自跑 `diff-cover`,不信 worker 报的数字; < gate 阈值 = Blocker

**评审重点**:
- 需求对齐 / 设计对齐 / 边界处理 / 契约遵守 / 测试质量
- 改动分支覆盖 ≥ gate 阈值(缺省 90%),未覆盖分支逐条列出

**判决输出**:
- `pass`: 无 blocker → done
- `reject`: 有 blocker → 转回 worker,comment 详细问题
- `pass-with-nits`: 无 blocker 但有建议 → needs_decision,等待人工确认

**执行协议**:
参照 `omac guide reviewer` 的完整执行清单
```

### Architect 派发

```
🏗️ 你被派发为 architect 执行架构任务/评审

**任务信息**:
- Issue ID: <issue-id>
- 任务类型: <架构设计 | 架构评审>

**架构设计任务(作为 worker)**:
- 产出:共享契约代码 + 架构决策记录 + 模块依赖图
- 关注:模块边界、数据流向、依赖方向、跨模块契约
- 验收:契约可 import + 不变量测试已写 + 决策已文档化

**架构评审任务(作为 reviewer)**:
- 评审范围:模块边界清晰度、契约遵守、依赖方向、设计模式一致性、架构漂移
- 不关注:实现细节、变量命名、算法优化
- 判决:必修项(架构问题)vs 建议项(优化方向)

**执行协议**:
参照 `omac guide roles` 的 Architect 执行清单
```

完整设计:docs/omac-cli-design.md
