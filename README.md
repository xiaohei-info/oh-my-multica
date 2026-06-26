# parallel-dev-skills

> 一套**通用、与平台无关**的 Agent Skills：把一份设计/plan 拆成声明式 manifest DAG，用固定引擎驱动**多 Agent 并行开发**到收敛闭环。核心是「契约先行」方法论 + 防跑偏闸门 + 多协作平台适配（Mock / GitHub / Multica）。

任何支持 **Agent Skills**（`SKILL.md` 约定）的运行时都能装上即用——Claude Code、Codex、OpenCode、ClosedCode 等。Skill 正文是引擎中立的纯文本与 Python 脚本，不绑定任何特定 Agent 平台。

---

## 包含两个 Skill

| Skill | 谁加载 | 作用 |
|---|---|---|
| **`parallel-dev-orchestration`** | 编排者（leader / 你） | 把设计拆成 manifest DAG，跑引擎派活、盯进度、失败决策、收尾。自带编排引擎脚本（`scripts/run_dag.py`）。 |
| **`parallel-dev-executor`** | 被派到任务的 worker / reviewer Agent | 执行协议：从 work item 读配置 → TDD 实现 → 产可评审 PR → 写证据；reviewer 独立复跑测试判决。纯方法论，无脚本。 |

二者配合：**orchestration 在协作平台建 issue 并派发，被派到的 Agent 加载 executor 干活。** 你只直接运行 orchestration 一侧的引擎脚本。

---

## 目录结构

```
parallel-dev-skills/
├── README.md                 # 本文件（安装/配置指南）
├── AGENTS.md                 # 配套通用工作规范（合并进你项目的 AGENTS.md/CLAUDE.md）
├── LICENSE                   # MIT
├── scripts/install.sh        # 安装脚本：把 skills 复制/软链进 Agent 的 skills 目录
└── skills/
    ├── parallel-dev-orchestration/
    │   ├── SKILL.md          # 编排方法论 + 引擎用法
    │   ├── .env.example      # 引擎配置示例
    │   └── scripts/          # 编排引擎（CLI + core + engines + tests）
    │       ├── run_dag.py    # ★ 编排入口
    │       ├── setup.py      # 交互式配置向导（生成 .env）
    │       ├── core/         # manifest / graph(frontier) / lint
    │       ├── engines/      # mock / github / multica 适配
    │       └── tests/        # 116 passed 的测试套件
    └── parallel-dev-executor/
        └── SKILL.md          # worker/reviewer 执行协议
```

---

## 安装

### 方式一：用安装脚本（推荐）

把两个 skill 装进目标 Agent 的 skills 目录：

```bash
git clone https://github.com/xiaohei-info/parallel-dev-skills.git
cd parallel-dev-skills

# 默认装到 ~/.claude/skills（复制模式）
./scripts/install.sh

# 或指定目标目录；--link 用软链以便跟随仓库更新
./scripts/install.sh ~/.codex/skills
./scripts/install.sh ./.claude/skills --link
```

### 方式二：手动放置

把 `skills/parallel-dev-orchestration` 和 `skills/parallel-dev-executor` 两个目录整体复制进你的 Agent 的 skills 目录即可。

### 各 Agent 的 skills 目录

不同 Agent 的 skill 发现路径不同，安装到对应位置：

| Agent | 用户级 skills 目录 | 项目级 |
|---|---|---|
| Claude Code | `~/.claude/skills/` | `<project>/.claude/skills/` |
| Codex / OpenCode / 其它 | 见各自文档的 skills/插件目录 | 通常为项目内 skills 目录 |

> 不确定你的 Agent 用哪个目录时，查其「skills」或「plugins」文档；`install.sh` 接受任意目标路径，放对地方即可被发现。

安装后，每个 skill 是一个含 `SKILL.md`（YAML frontmatter 带 `name` / `description`）的自包含目录，符合 Agent Skills 通用约定。

---

## 配置编排引擎

**配置只放非敏感的「配置项」（engine 类型、workspace、squad）；认证交给各 CLI 自己管理**
（github 用 `gh auth login`，multica 用其自身登录）——不要把 token 写进配置。

配置面统一为「环境变量」，有三种给法，优先级 **低 → 高**：

```
.env 文件（可选）  <  进程环境变量（export）  <  命令行参数（--engine / --workspace）
```

- `.env` **可选**：存在就加载，不存在不报错。适合持久化 workspace/squad 这类常用值。
- `export`：覆盖 `.env`，适合临时切换。
- `--engine/--workspace`：覆盖一切，最显式。
- **缺必填项**（engine 的 workspace）→ 报清晰错误，直接告诉你设哪个环境变量或传哪个参数。

```bash
cd <skills-dir>/parallel-dev-orchestration

# 任选其一即可：
python3 scripts/setup.py                                  # A. 向导生成 .env
cp .env.example .env                                      # B. 手填 .env
python3 scripts/run_dag.py m.yaml --engine multica --workspace <ws>   # C. 纯命令行，不用 .env
```

### 三种引擎与所需配置

| 引擎 | `ENGINE_TYPE` | 必需配置（env 或 `--workspace`） | 认证（不在配置面） |
|---|---|---|---|
| **Mock** | `mock` | `MOCK_WORKSPACE_ID`（任意串，有默认） | 无——本地空跑 |
| **GitHub** | `github` | `GITHUB_REPO=owner/repo` | `gh auth login`（gh 管 token） |
| **Multica** | `multica` | `MULTICA_WORKSPACE_ID`（`MULTICA_SQUAD_ID` 可选） | `multica` 自身登录（`~/.multica`） |

> work item 在 GitHub 下即 GitHub Issue，在 Multica 下即 Multica issue。manifest 里 `worker:` / `reviewer:` 填的 Agent 名必须真实存在于该 workspace/squad 的成员池。

---

## 各引擎前置准备（Agent 引导用户清单）

加载本 skill 的 Agent 自己装不全外部依赖（CLI 登录、平台 id、成员池都在用户侧）。
**选定引擎后，按下表逐项引导用户完成**，每项缺失都会让真实编排卡住：

### Mock —— 零前置，先验证机制
- [ ] 无需任何外部依赖；`ENGINE_TYPE=mock` + 任意 `MOCK_WORKSPACE_ID` 即可。
- [ ] 不连真实平台、不真派 Agent，仅验证 lint→frontier→派发→状态机链路。
- 适用：第一次上手、CI、演示。**真实开发请切 github / multica。**

### GitHub —— issue 即 GitHub Issue
引导用户：
- [ ] **登录 `gh` CLI 管认证**：`gh auth login`（token 由 gh 自己保管，**不写进配置/.env**）。
- [ ] **确认目标仓库**：`GITHUB_REPO=owner/repo`，且账号对该仓有 **issue 读写权限**。
- [ ] **worker/reviewer 名 = 仓库可 assign 的 GitHub 用户名**——manifest 里写的名字必须是该仓能被指派 issue 的协作者/成员，否则派发失败。
- [ ] **确认集成分支存在**（manifest 的 `integration_branch`），PR 以它为 base。
- 配置：`ENGINE_TYPE=github` + `GITHUB_REPO=...`（`.env` / `export` / `--workspace owner/repo` 任一）

### Multica —— issue 即 Multica issue
引导用户：
- [ ] **登录 `multica` CLI 管认证**，确认 `multica` 在 PATH（认证存 `~/.multica`，**不写进配置/.env**）。
- [ ] **拿 workspace id**：`multica workspace list` → 填 `MULTICA_WORKSPACE_ID`。
- [ ] **拿 squad id**：`multica squad list`（或 `multica squad member list <squad-id>` 核对成员）。把它经 `setup.py` 填进 **`MULTICA_SQUAD_ID`（.env）**，作为默认派发小队——orchestrator 据此枚举成员、生成 manifest，无需先手编一个尚不存在的 manifest。各 manifest 的 `meta.squad` 为**可选覆盖**：写了则以 manifest 为准，没写就回退这个 env 默认值。
- [ ] **在小队里给每个 Agent 配好角色**：`worker` / `reviewer` / `architect`（编排者据此挑选——把擅长后端的 worker、专职评审的 reviewer 放到对应卡）。
- [ ] **确认成员池**：manifest 里每个 `worker:` / `reviewer:` 填的是 **agent 名**，且必须是该 squad 的真实成员；reviewer ≠ worker。

> **关于「为什么 manifest 里是 agent 名而不是 role」**：引擎按**名字**在 squad 成员池里校验与派发（`multica squad member list` 取成员名）。role 是**编排者选人的依据**——你按角色挑出合适的 agent，再把它的**名字**写进 `worker`/`reviewer` 字段。所以「在平台上配好角色」与「manifest 里写名字」二者配合：角色帮你选对人，名字是实际派发句柄。

- 配置：`ENGINE_TYPE=multica` + `MULTICA_WORKSPACE_ID=...`（`.env` / `export` / `--workspace <id>` 任一；`MULTICA_SQUAD_ID` 可选）

### id 用环境变量驱动（不必手改文件）

manifest 的字段支持 **`${ENV_VAR:-默认值}`** 展开。这样 squad / 仓库标识等 id 不必硬写进文件——
用户设环境变量即可，未设则用默认值（mock 下开箱即跑）：

```yaml
meta:
  squad: "${ORCH_SQUAD:-mock-workspace}"   # 真实运行：export ORCH_SQUAD=<你的squad>
```

- 未设环境变量 → 取默认 `mock-workspace`（mock 引擎不校验成员，直接跑）。
- `export ORCH_SQUAD=<squad-id>` 后重跑 → 自动展开为真实 squad，**无需编辑 manifest**。
- 变量未设且无默认值（`${FOO}`）→ 保留原样，便于一眼看出"这里还没配"。

> 这条机制就是为了避免「committed 文件里留个占位符、别人不知道要替换」——
> 看到 `${VAR}` 即知"设这个环境变量"，自解释。

### git 回写开关（`ORCH_GIT_SYNC`，默认关）

引擎以 manifest 为唯一口径，跨机器协作时靠 **git commit + push** 流转状态。但这一步
**默认关闭**，避免你第一次装完跑测试 / demo 时往业务项目仓库塞 commit：

| `ORCH_GIT_SYNC` | 行为 | 适用 |
|---|---|---|
| 未设 / `0` / `false`（默认） | 只在本地写 manifest 文件，**不 `git add/commit/push`** | 首次试跑、单机、mock/demo、CI |
| `1` / `true` / `yes` / `on` | 状态变更回写并 `git commit`（有远程则 `push`） | **真实跨机器协作**：manifest 落在项目 `.orchestrator/`、受版本管理 |

两种配法都生效（显式 `export` 优先于 `.env`）：

```bash
# 法一：写进 .env（与引擎配置同处管理）
echo 'ORCH_GIT_SYNC=1' >> .env

# 法二：跑前 export（不依赖 .env，适合走 run_dag --engine/--workspace 命令行参数的场景）
export ORCH_GIT_SYNC=1
python3 scripts/run_dag.py .orchestrator/<name>.yaml
```

> 引擎启动时会打印「git 回写: 开/关」当前状态，便于确认。无论开关如何，manifest
> 文件本身都会被写状态——故跑 demo 仍建议用临时副本（见下方快速开始），别直接对
> committed 样例跑。
>
> 注：引擎有两条配置入口——`.env`（`create_engine_from_env`）或命令行
> `--engine/--workspace`（`create_engine_from_config`，不读 `.env`）。走命令行参数那条
> 路时 `.env` 不会被加载，`ORCH_GIT_SYNC` 需用 `export`。

---

## 快速开始（Mock 空跑，零依赖）

第一次先用 mock 引擎跑通机制，不连任何平台：

```bash
cd <skills-dir>/parallel-dev-orchestration

# 1) 配置 mock 引擎（workspace 用 mock-workspace，与 demo manifest 的默认 squad 对齐）
printf 'ENGINE_TYPE=mock\nMOCK_WORKSPACE_ID=mock-workspace\n' > .env

# 2) 看 CLI 用法
python3 scripts/run_dag.py --help

# 3) 跑一个最小 manifest（自带冒烟样例）
#    引擎会把状态回写进 manifest 文件，故先拷贝到临时路径，避免弄脏 committed 样例
cp scripts/tests/smoke_test_manifest.yaml /tmp/demo.yaml
python3 scripts/run_dag.py /tmp/demo.yaml
```

引擎会自动 lint → reconcile → 建 work item → 算 frontier → 派发 → 轮询到终态 → 写回状态。
mock 引擎预置成员 `alice/bob/charlie`，样例的 `worker/reviewer` 用的就是它们，开箱即跑（3 节点 100% 完成）。看懂流程后，再切到 github/multica 跑真任务。

> 注：引擎以 manifest 为唯一口径，会把状态**回写进 manifest 文件**。git 提交默认**关闭**
> （`ORCH_GIT_SYNC` 未开），所以不会污染仓库历史；但文件本身仍会被改写，故 demo 用 `/tmp`
> 副本，不动 committed 样例。真实跨机器协作再开 `ORCH_GIT_SYNC=1`（见上「git 回写开关」）。

---

## 真实使用的完整闭环

由编排者 Agent 加载 `parallel-dev-orchestration` skill 后驱动，分两个阶段（详见该 skill 正文）：

1. **Wave 0 打地基（串行）**：把跨模块契约写成**代码**、搭骨架 + CI、给对端写 mock，验证地基全绿。地基没冻结就扇出 = 最常见的失败。
2. **拆 manifest DAG**：按 track 切并行单元，写成 `.orchestrator/<name>.yaml`，每节点带 `worker` / `depends_on` / `description`。
3. **manifest 走 PR 评审门**：评审通过才进下一步。
4. **跑引擎**：`python3 scripts/run_dag.py .orchestrator/<name>.yaml`，全自动派发与监督。
5. **失败 / 断点续跑**：改 manifest（换 worker / 拆小 / 降范围）后对同一文件重跑——已 done 自动跳过，失败的重做（幂等）。
6. **收尾**：汇总 digest（PR 列表、验收状态、遗留问题）。

被派到任务的 worker / reviewer Agent 则加载 `parallel-dev-executor` skill，按执行协议 TDD 实现并产出可评审 PR。

---

## 配套通用规范（[AGENTS.md](./AGENTS.md)）

两个 skill 定义的是「**怎么编排**」与「**怎么执行**」的协议；要让一群 Agent 在并行开发中
**不跑偏**，还需要一组**常驻护栏**——全局工作纪律必须放进「每次都会被加载」的文件里
（`AGENTS.md` / `CLAUDE.md`），而不是埋在长文档中。这正是编排 skill 防跑偏模型的一环。

本仓自带这份通用底稿：[`AGENTS.md`](./AGENTS.md)（项目无关，只含工程纪律：契约先行 /
先规划后实现 / 测试同步 / 完成需证据 / 根因调试 / 代码品味准则）。

**怎么用**（三选一，详见 `AGENTS.md` 文末）：

1. **合并（推荐）**：把 `AGENTS.md` 的工作纪律整合进你项目**已有的 `AGENTS.md` / `CLAUDE.md`**，
   与你的业务专属约束（架构边界、领域规则）并列。这样 worker/reviewer 每次加载即受同一套约束。
2. **引用**：在你项目的 `AGENTS.md` 顶部加一行指针，指向本文件。
3. **直接放置**：项目还没有 `AGENTS.md` 时，拷到项目根作起点，再叠加业务约束。

> 边界：`AGENTS.md` 只放**通用工程纪律**；项目专属口径（架构分层、技术选型、领域模型、
> 权限角色、部署）仍写在你自己的 `AGENTS.md` 里，不要塞进这份通用底稿。

---

## 前置依赖

- **Python ≥ 3.9**（引擎脚本仅用标准库 + PyYAML；`python3` 可直接调用）
- 选 GitHub 引擎：`gh` CLI 已登录
- 选 Multica 引擎：`multica` CLI 已登录
- Mock 引擎：无外部依赖

安装 PyYAML（若环境缺）：

```bash
python3 -m pip install pyyaml
```

---

## 开发与测试

```bash
cd skills/parallel-dev-orchestration/scripts
python3 -m pytest tests/ -q                       # 全套
python3 -m pytest tests/ -q -m "not live_multica" # 排除需真 CLI 的 live 测试
```

live 测试默认 skip，仅当 `multica` CLI 在 PATH 且显式设置 `MULTICA_WORKSPACE_ID` + `MULTICA_TEST_SQUAD` 时才运行（仓库不携带任何私有 workspace/squad 默认值）。

---

## 设计要点（一句话）

- **接口是地基，不是产物**：契约先于实现冻结、以代码存在，下游只 import、禁重定义——接口漂移直接编译/测试不过。
- **对端可以是假的**：契约冻结后每个模块对着 mock 独立开发，这是并行度的来源。
- **manifest 是唯一口径**：节点状态直接写进 manifest 经 git 流转，无自造存储；幂等重跑、跨机器接力都靠它。
- **完成必须有证据**：worker 自报只是线索，以 reviewer 独立复跑判决 + 引擎状态为准。

---

## License

[MIT](./LICENSE)
