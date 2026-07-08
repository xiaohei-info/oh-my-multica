# manifest DAG 与 contract

manifest(`.omac/<name>.yaml`)是状态机载体:节点、依赖、contract、
work_item_id、status 全部在此,进 git。

## 节点结构

```yaml
nodes:
  - id: user-api
    title: Implement user API
    worker: backend-agent          # 必填,须在 agent 池内
    reviewer: review-agent         # 可选,必须 ≠ worker
    blocked_by: [shared-contracts] # 依赖
    contract:                      # 硬合同,lint 强制
      objective: 一句话目标
      source_of_truth: [docs/design.md#user-api]  # 必填:worker 照着实现的设计指针(只放指针,不放正文)
      acceptance: [ ... ]          # 须锚定验收文档条目
      non_goals: [ ... ]
      scope_paths: [src/auth/**]   # 可选:本节点限定改动的路径,圈定工程边界防并行冲突;新项目结构未定可留空
      verification_commands: [ ... ]
      integration_gates: [ ... ]   # 每个 gate 须有 source_of_truth/delivery_goal/covers/acceptance_refs/commands
      pr_base: feature/v1          # PR 基线,防打错分支
      coverage_gate: 90
```

## lint 口径

objective/source_of_truth/acceptance/non_goals/verification_commands/integration_gates/pr_base
必填且非空;coverage_gate 0-100;required_contracts 路径必须存在;
reviewer ≠ worker;DAG 无环;worker/reviewer 在 agent 池内。

> **source_of_truth 必填**:每个节点必须带实现层设计指针(设计文档 + 节号),否则 worker 只能脑补设计。
> **scope_paths 可选**:有项目结构就填(圈定 worker 只能碰的路径,把并行冲突从散文约束变成可扫描的工程边界);
> 新项目结构还没定,可留空放行——有则最好,无也可过。

字段支持 `${ENV_VAR:-默认值}` 展开,id 类值不必硬写进文件。

omac 命令链:`omac plan create` 产 manifest → `omac dag run` 消费 manifest 推进 DAG。

## contract 如何进入 agent 真实工作

manifest 不是静态文档。`omac dag run` 派发节点时,节点 contract 会进入 issue body
和 `omac work show <issue-id>` 输出;worker/reviewer 都以同一份 contract 为准。

| contract 字段 | worker / reviewer 怎么用 |
|---|---|
| `contract.source_of_truth` | worker 必须全文读取对应设计锚点;reviewer 对照它查语义漂移 |
| `contract.acceptance` | worker 逐条实现并留证据;reviewer 产出 acceptance_mapping |
| `contract.non_goals` | worker 不越界;reviewer 发现越界即 blocker |
| `contract.verification_commands` | worker 在 `verification.commands` 逐条记录;reviewer 独立复跑 |
| `contract.integration_gates` | worker 逐 gate 写 metrics/artifacts/source_of_truth/delivery_goal;reviewer 独立复核 |
| `contract.pr_base` | worker PR base 必须指向它;reviewer 与 submit 校验同口径 |
| `contract.coverage_gate` | worker 跑 diff-cover 自测;reviewer 复跑,低于阈值判 reject |

真实执行链:
1. worker 先跑 `omac work show <issue-id>`,从 contract 取目标、边界、验证命令与 PR base。
2. worker 只按 contract 做卡内范围,完成后跑
   `omac work submit <issue-id> --pr-url <PR> --verification-file ev.yaml`。
3. reviewer 再跑 `omac work show <issue-id>`,读取同一份 contract 和 worker 写入的 verification/report 输入。
4. reviewer 用 `omac work submit <issue-id> --verdict pass|pass-with-nits|reject --report-file r.yaml`
   写回判决。

## 拆解方法论(道)

> 本节回答「为什么这样拆、怎样防跑偏」—— 遇到拿不准的拆图判据,回这里查。
> 「术」(5 步操作 + 粒度/依赖速查)见下方「阶段 B」。

### 核心信念

1. **跑偏不能靠"提醒"治,要靠"结构"治** —— 让错误的写法编译不过/测试不过/合并不了,才可靠。
2. **接口是地基,不是产物** —— 模块间的契约必须**先于**业务实现冻结,且以**代码**形式存在。
3. **对端可以是假的** —— 契约冻结后每个模块对着 mock/fake 独立开发,无需等对端做完。
4. **单一事实源** —— 每条口径只有一个权威出处,其它地方引用它,禁止平行拷贝。
5. **"完成"必须有客观证据** —— 测试/构建/接口调用通过,不是"我觉得没问题"。

### 防跑偏三层模型

| 层 | 防什么偏 | 手段 |
|---|---|---|
| **接口层** | 模块间对接不一致 | **契约即代码**:共享类型包,下游只 import、禁重定义 |
| **边界层** | 越过职责红线、违反硬约束 | **CI 闸门**:边界扫描 + 契约不变量测试 + 质量门禁 |
| **语义层** | 接口对但实现意图跑偏 | **独立评审**:非实现者对照"设计文档 × 约束"逐条核对 |

经验法则:**CI 抓接口/边界漂移,评审抓语义漂移**。两者互补,缺一不可。

### 七道防跑偏闸(按越早越硬排序)

1. **契约即代码** —— 共享类型包,下游只 import、禁平行定义。
2. **单文档单负责人工单** —— 一卡一口径,约束前置。
3. **常驻护栏** —— 把全局核心约束放进每次都被加载的地方(如派发 issue body),不埋在长文档里。
4. **小任务粒度** —— 切到"一两个文件、可短时收口”,不给它跑远的机会。
5. **客观 CI 闸门** —— 契约不变量测试、边界扫描、质量门禁、关键不变量 e2e。
6. **独立评审闸** —— 非实现者拿"模块 diff × 唯一口径文档 × 全局约束"逐条核对。
7. **完成前独立验证** —— "完成"必须附证据,不接受自述。

### 两级拆解原理

**第一级(你拆,扇出前)= 卡级 issue** —— 粒度 = 并行单元(半天~两天收口)。
**第二级(领卡的执行者来拆,领取后)= sub-issue** —— 卡偏大时由领卡人拆成 2–5 个子任务。

宏观骨架你定、微观切分交给有上下文的人。判据:卡太大就再切一张;小到"一个函数"就别单独立卡;**宁可卡少而清晰,不要卡多而碎**。

### 依赖三原则

1. **只把"真前置"设为硬依赖(blocked_by)** —— 上游没关,下游不可领。
2. **软依赖只做提示,不设硬边** —— 写进 description 作提示,留给执行者需要时自己收紧。硬边宁缺毋滥:每多一条假硬边,就少一分并行度。
3. **节点内细分用描述,节点间协作用依赖** —— 内部分解由 worker 自己决定,节点间依赖在 manifest 显式声明。

### 常见误区

1. 跳过 Wave 0 直接并行(最常见失败)—— 没冻结契约就扇出 = 各自发明接口。
2. 契约写成文字而非代码 —— 文字契约挡不住漂移。
3. 地基追求"实现完整" —— 地基要"形状对、可对接、可测试",真实重实现可留占位。
4. 边界扫描过度,误报淹没真报 —— 要排除文档/注释里的"反向说明"。
5. 一张工单背多份文档 —— 一卡一口径。
6. 扇出前就把卡拆到微任务 —— 微拆依赖实现上下文,扇出前不具备。
7. 把软依赖也设成硬 blocked_by —— 把本可并行的活锁成串行。
8. 在 description 里复述设计内容 —— 只放指针(唯一文档+节号),不放正文。
9. 不写非目标 —— 最隐蔽的越界源。
10. 不钉 PR 基线分支 —— 自助 Agent 会误把 PR 打上主干。
11. Orchestrator 抢 worker 的活 —— 你只负责拆、派、盯、收,不实现业务代码。

## 三阶段流程(术)

### 阶段 A — 打地基(Wave 0,串行)

按核心信念产出"地基四件套":
- **共享契约**(代码,下游只 import)
- **共享底座**(DB schema / 配置 / 工具库)
- **可运行骨架**(项目结构 / 入口 / CI pipeline)
- **CI 闸门 + 对端假件**(mock/fake)

判据:能写出每张工单卡、且卡里"必消费契约"已是可 import 的代码、"验收"已有测试位——做不到就别扇出。

从零准备地基的清单:
1. 锁定设计文档为单一事实源
2. 列出模块间需要对接的全部口径(DTO/事件/枚举/错误/状态/跨服务调用)
3. 把口径写成代码契约(如 `shared/contracts/`),作为地基第一件
4. 为契约写"不变量测试"(漂移守卫)
5. 写边界扫描 + 质量门禁,接进 CI
   - **改动分支覆盖闸门(硬门槛)**:集成分支 CI 加 `diff-cover` check —— 改动分支覆盖 < 90% → CI 红灯、合并不了。与 reviewer 独立复跑同口径(双层:CI 物理拦 + reviewer 判决兜底)。
6. 搭可运行骨架
7. 为尚未实现的对端写 fake/mock
8. 用测试验证地基本身可跑、可测、全绿 → 地基冻结,可以扇出

### 阶段 B — 拆 plan 成 manifest DAG(5 步)

**步骤 1 — 找"地基"(Wave 0)**:从设计文档识别共享契约边界、底座组件、骨架、闸门与假件、测试地基。这些是串行前提。

**步骤 2 — 找"集成缝"(划分并行 track)**:沿契约边界切分独立 track,track 间无运行时依赖(只通过契约对接)。同步产出集成覆盖矩阵:每条集成缝标明由哪个 integration gate 覆盖,gate 要锚定需求/设计文档中的 delivery goal。

**步骤 3 — 每个 track 内找"小地基"**:每 track 内排序:小地基 → 业务模块。

**步骤 4 — 配对端 mock**:确保每个业务模块的"对端"已在 Wave 0 产出 mock/fake。

**步骤 5 — 留集成验收波(Wave 2)**:最后一波替换 mock → 真对端,跑端到端测试。业务节点 contract 可声明局部 L1/L2 integration gate,Wave 2 节点负责跨 track 的 L2/L3 主链验收。

### 阶段 B 收尾:manifest 落盘 + review 门

拆完 DAG 后,manifest 写到 `.omac/<name>.yaml`(固定路径),必须含完整 issue body(每节点的 `description` 是 worker 的上下文来源)。

### Manifest 编写规范

**依赖关系**:只把"真前置"设为硬边 `blocked_by`;软耦合只写进 description 提示,不设硬边。

**关键字段**:
- `nodes.<key>.worker`: worker agent 名(必填,须 ∈ agent 池)
- `nodes.<key>.reviewer`: reviewer agent 名(可选,非空时须 ≠ worker)
- `nodes.<key>.blocked_by`: 依赖节点 key 列表(空 = Wave 0 可立即开始)
- `nodes.<key>.contract`: 硬合同(推荐)。声明目标、验收、非目标、验证命令、PR base、覆盖率门槛。

**环境变量展开**:manifest 任意字段值支持 `${VAR}` 与 `${VAR:-默认值}`。

**contract 示例**:见本文件顶部「节点结构」。

**lint 规则**:objective/acceptance/non_goals/verification_commands/integration_gates/pr_base 必填;coverage_gate 缺省 90,填写时须 0-100;required_contracts 中的仓库路径必须存在。

### 粒度与依赖(拆图规则速查)

- **粒度**:一个节点 = 一个并行单元(半天~两天可收口)。你只拆第一级(卡级),第二级交给领卡人。
- **依赖**:只把「真前置」设硬边;软耦合只写进 description 提示,**不设硬边**。

### Agent 选择 / Reviewer 可选 / Architect

- 从 workspace agents 中按角色选择(不要写死 agent 名)。
- **有 reviewer**: worker done → in_review → reviewer 复跑判 verdict → pass 才 done。
- **无 reviewer**: worker done → 直接 done(风险高,适合低风险卡)。
- 推荐: Wave 0 地基 + Wave 2 集成验收**必须有 reviewer**;Wave 1 业务模块按风险决定。
- Architect 负责共享契约设计(Wave 0)、架构模式评审、最终整体架构评审(Wave 2 后)。

## 拆解检查清单

**结构检查**:
- [ ] Wave 0(地基)已识别且串行(contracts/scaffold/mock)
- [ ] Wave 1 按 track 划分,track 间并行
- [ ] 每个 track 内小地基先于业务模块
- [ ] Wave 2 集成验收依赖全部 Wave 1
- [ ] 所有 blocked_by 引用存在(无悬空依赖)

**依赖检查**:
- [ ] 只设"真前置"为硬依赖(假硬边会减少并行度)
- [ ] 软依赖只在 description 提示
- [ ] 无循环依赖(lint 会检查)

**成员检查**:
- [ ] 所有 worker ∈ agent 池
- [ ] reviewer(如有) ∈ agent 池且 ≠ worker
- [ ] 按 agent 特长分配

**验收检查**:
- [ ] Wave 0 + Wave 2 有 reviewer
- [ ] 高风险节点有 reviewer
- [ ] description 明确交付物
