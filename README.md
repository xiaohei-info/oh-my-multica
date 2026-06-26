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
    │       └── tests/        # 107 passed 的测试套件
    └── parallel-dev-executor/
        └── SKILL.md          # worker/reviewer 执行协议
```

---

## 安装

### 方式一：用安装脚本（推荐）

把两个 skill 装进目标 Agent 的 skills 目录：

```bash
git clone <this-repo> parallel-dev-skills
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

orchestration skill 通过 skill 目录下的 `.env` 选择协作平台。两种生成方式：

### A. 交互式向导

```bash
cd <skills-dir>/parallel-dev-orchestration
python3 scripts/setup.py        # 选引擎、按提示填变量，自动写出 .env
```

### B. 手动复制示例

```bash
cd <skills-dir>/parallel-dev-orchestration
cp .env.example .env            # 编辑 .env，只保留所选引擎的配置
```

### 三种引擎与所需配置

| 引擎 | `ENGINE_TYPE` | 必需变量 | 前置依赖 |
|---|---|---|---|
| **Mock** | `mock` | `MOCK_WORKSPACE_ID`（任意串） | 无——本地空跑，先验证机制 |
| **GitHub** | `github` | `GITHUB_REPO=owner/repo`，`GITHUB_TOKEN`（建议） | 已安装并登录 `gh` CLI |
| **Multica** | `multica` | `MULTICA_WORKSPACE_ID`（`MULTICA_SQUAD_ID` 可选） | 已安装并登录 `multica` CLI |

> work item 在 GitHub 下即 GitHub Issue，在 Multica 下即 Multica issue。manifest 里 `worker:` / `reviewer:` 填的 Agent 名必须真实存在于该 workspace/squad 的成员池。

---

## 快速开始（Mock 空跑，零依赖）

第一次先用 mock 引擎跑通机制，不连任何平台：

```bash
cd <skills-dir>/parallel-dev-orchestration

# 1) 配置 mock 引擎
printf 'ENGINE_TYPE=mock\nMOCK_WORKSPACE_ID=demo\n' > .env

# 2) 看 CLI 用法
python3 scripts/run_dag.py --help

# 3) 跑一个最小 manifest（自带冒烟样例）
python3 scripts/run_dag.py scripts/tests/smoke_test_manifest.yaml
```

引擎会自动 lint → reconcile → 建 work item → 算 frontier → 派发 → 轮询到终态 → 写回状态。看懂流程后，再切到 github/multica 跑真任务。

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
