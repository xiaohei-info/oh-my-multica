# oh-my-multica

[![CI](https://github.com/xiaohei-info/oh-my-multica/actions/workflows/ci.yml/badge.svg)](https://github.com/xiaohei-info/oh-my-multica/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-1.0.0-blue)](https://github.com/xiaohei-info/oh-my-multica)

[English](README.md) | [简体中文](README.zh-CN.md)

**oh-my-multica** 是确定性 CLI 驱动的多 Agent 并行开发编排，CLI 命令与 Python 包名仍为
`omac`。它把复杂软件开发从「一个
agent 靠长上下文硬扛」变成「契约先行 + manifest DAG + 多 Agent 并行执行 +
结构化证据 + reviewer 独立验收」的可收敛工程流程。

核心理念是**控制反转**:LLM 从「驱动者」降级为「被调用者」——确定性 CLI 程序
承载整个编排循环,planner / orchestrator / reviewer / worker / acceptor 全部是
CLI 派发的、有终点的单次任务。

## 受众矩阵

| 入口 | 首要受众 | 使用契约 |
|---|---|---|
| 平台 issue | Human-first | issue 顶部只保留一个 Agent bootstrap:`omac work show <id> --output json` |
| `omac work show` / `omac work submit` | Agent-first | 两者默认 JSON;先 show 取得实例事实和精确 submit 命令,Human 调试显式使用 `--output table` |
| 全部 `omac guide ...` | Agent-first | Guide 是稳定静态知识;只按 `work show` 返回的 `guide_refs` 最小加载,冲突时实例事实优先 |

JSON-first 用法:

```bash
# Agent bootstrap(JSON 默认值也可省略)
omac work show "$ISSUE_ID" --output json

# Human 调试
omac work show "$ISSUE_ID" --output table
```

`work submit` 同样默认 JSON，校验失败也会在 stderr 返回结构化错误；使用 `work show`
返回的精确 `submit` 命令，不要从静态 Guide 猜参数。

## 机制优势

| 维度 | 机制 | 效果 |
|---|---|---|
| 成本 | 编排循环是纯确定性程序 | 监督 token 从「全周期」降为 **0**;LLM 只花在计划、拆解、开发、评审、验收等真实智力工作上 |
| 可靠性 | loop 是代码不是提示词 | 不存在「监控几轮后自行退出」;终态只有收敛(exit 0)或带结构化报告移交(exit 20) |
| 不跑偏 | 验收文档锚定 + contract 硬合同 + 双门禁证据闭环 | 需求 → 拆解 → 开发 → 验收全程有机器可校验的锚点 |
| 可恢复 | 状态全在 manifest + 平台,循环幂等 | 任意中断重跑即续跑,支持跨机器接力 |
| 可交付 | CI / merge / 总控验收内置,done = 已合入集成分支 | 「DAG 跑完」=「按验收文档全 pass、真正可交付」,而非「代码写完了」 |
| 分发 | 单 pipx 包,零外部知识依赖 | 人 / agent / Web 同一入口;内部角色的协议随派发载荷现场注入 |
| 演进 | Store / Runtime 双接口 | 接 Linear / Jira 只增适配器,不动 pipeline |

## 前置条件

每台参与编排的机器(runtime)需安装:

- **omac CLI**:从私有仓库源码安装(见下「安装」),runtime 机器统一用 `pipx` 隔离
- **平台 CLI**:Multica 引擎需 `multica` CLI 已登录(`multica` 在 PATH,认证存 `~/.multica`)
- **Python** >= 3.10,依赖 `PyYAML`(pipx 自动隔离)

Mock 引擎零外部依赖,仅用于本地演示、CI 与首次试跑。

## 安装

omac 未发布到公共 PyPI(项目当前私有,仅内部分发)。控制机与每台 agent 机统一按下面装。

**1) 一次性装 pipx**(runtime 多为 externally-managed,用 pipx 隔离绕开 PEP 668):

```bash
# Linux
python3 -m pip install --user pipx --break-system-packages && pipx ensurepath
# macOS
brew install pipx && pipx ensurepath
```

装完重开 shell,让 `~/.local/bin` 进 PATH。

**2) clone 仓库并安装:**

```bash
git clone git@github.com:xiaohei-info/oh-my-multica.git
cd oh-my-multica
pipx install .
```

**3) 验证(每台都要过):**

```bash
omac --version          # omac 1.0.0
omac init --check       # 引擎 / config 体检
```

**更新到最新:**

```bash
cd oh-my-multica && git pull && pipx reinstall omac
```

> - 某台不方便配 git 认证:改用 wheel 离线分发 —— 在有仓库的机器 `python3 -m build` 产出 `dist/omac-1.0.0-py3-none-any.whl`,拷到目标机 `pipx install omac-1.0.0-py3-none-any.whl`。
> - 开发调试(在本仓内改代码):可编辑安装 `pip install -e .`(需在 venv 内,或加 `--break-system-packages`)。

## 快速开始

以下命令均可在本仓根目录实测运行(Mock 引擎)。Mock 成员池预设
`alice`、`bob`、`charlie`,下文以这三者为例配置角色。

### 1. 一次性配置(`omac init` / `omac config set`)

首次运行 `omac init` 时，第一项选择输出语言：默认 `en`，也可选 `cn`。选择会保存到
`.omac/config.yaml` 的 `language` 字段；后续 CLI、Guide 和 Web 文案都使用该设置。

```bash
# 人类首次配置:运行交互式向导
omac init

# agent/CI 首次配置:不要运行裸 omac init,直接声明式写 config
omac config set language cn
omac config set engine mock
omac config set workspace mock-workspace
omac config set roles.planner alice
omac config set roles.orchestrator bob
omac config set roles.workers '["alice"]'
omac config set roles.reviewers '["charlie"]'
omac config set workflow.human_in_loop false
omac config set workflow.acceptance_doc true
omac config set workflow.goal_required true

# 体检:检查配置文件与角色映射是否就绪
omac init --check
```

交互式 `omac init` 会先列出工作空间现有 Agent，并允许从仓库内置模板创建新 Agent。
模板位于 [`agents/`](./agents)，包含完整 Instructions 和当前 Multica 配置所使用的
Skill 文件。创建时由用户选择 Runtime 和 Agent 名称；创建完成后，新旧 Agent 进入同一
候选池，再由用户自由映射到 planner、orchestrator、workers、reviewers、acceptor。

内置模板包括：

```text
planner  orchestrator  worker  reviewer  acceptor
architect  backend  frontend  pm
```

使用已有 Agent 时，OMAC 不修改其 Instructions 或 Skills。通过模板创建时，OMAC 会复用
workspace 中同名 Skill、上传缺失 Skill 的完整目录，然后创建 Agent、注入 Instructions
并绑定模板对应的 Skill。模板创建是可选增强，OMAC 的运行正确性仍由 `work show/submit`、
内置 guide、contract 和证据校验保证。

> exit 5 提示"角色不在工作空间 agent 池内"?一定用的是 `alice`/`bob`/`charlie`
> 三者之一,mock 池不接受其他名字。

### 2. 计划与 DAG 拆解(`omac plan`)

`omac plan create` 已实现完整流水线:设计方案 + 项目级开发规范 → 验收文档 → 拆解为
manifest DAG。planner 必须同时提交设计文档与项目规范；流水线收敛后,OMAC 更新项目根目录
`AGENTS.md` 的管理区。默认行为读 `.omac/config.yaml` 的 `workflow` 块；`--doc` 跳过
planner、直接使用现成设计文档,同时也跳过 `AGENTS.md` 更新。
`--no-review` / `--no-acceptance` / `--no-confirm` 仍可按单次命令临时关阶段。
`omac dag check` 对现成 manifest 走 lint + review 门；`omac dag show` 看摘要。
字段与流程见 `omac plan --help`、`omac dag --help` 与
`omac guide artifact manifest`。

想跳过 planner 直接体验 Loop?仓内自带 `tests/fixtures/smoke_p1.yaml` 作为现成
manifest 示例,可直接进下面第 3 步。

```bash
cat tests/fixtures/smoke_p1.yaml
```

### 3. 确定性 Loop 执行(`omac dag run`)

把 smoke fixture 复制到 `.omac/` 下(该目录会落库 git,被 DAG 改写状态):
(演示用 `/tmp/` 以免污染本仓)

```bash
cp tests/fixtures/smoke_p1.yaml /tmp/smoke.yaml

# 前台循环,	mock 引擎自动完成所有节点,收敛后 exit 0
omac dag run /tmp/smoke.yaml

# 随时查看快照(不推进)
omac dag status /tmp/smoke.yaml

# 单轮推进后退出(exit 0 收敛 / 10 推进中 / 20 需决策)
omac dag tick /tmp/smoke.yaml
```

### 4. Agent 按需知识(`omac guide`)

全部 guide topic 面向 Agent。先运行 `omac work show <id> --output json` 读取当前
实例事实与 `guide_refs`,再只加载列出的 topic;Guide 不能覆盖当前实例事实。

```bash
omac guide                   # 列出全部 topic
omac guide workflow          # 整体工作流:init → plan → dag run → 异常处理闭环
omac guide roles             # 生命周期角色索引与职责边界
omac guide role planner      # 设计方案 + 验收文档协议
omac guide role worker       # develop 执行协议(TDD·证据·env_setup)
omac guide role reviewer     # review 阶段协议(独立复跑·评审目标)
omac guide artifact manifest # manifest DAG 与 contract 字段
omac guide artifact evidence # verification / review / acceptance-results 证据格式
omac guide recovery          # exit 20 之后的恢复手册
```

## 命令面一览

```
omac
  CORE(调用者/驱动侧)
    plan     create | confirm | resume      设计、验收与 DAG 拆解流水线
    dag      check | show | run | status | tick
    node     show | retry | accept | abandon
  WORK(Agent-first)
    work     show | submit                 当前实例事实 + 结构化交付(默认 JSON)
  SETUP
    init     交互式配置 / --check 体检
    config   get | set
  GUIDE(Agent-first)
    guide    workflow | roles | role <name> | artifact <name> | recovery(按 guide_refs 最小加载)
  WEB
    web      本地只读可视化面板(选 manifest、看进度与证据链)
```

### 退出码契约

| 码 | 含义 |
|---|---|
| `0` | 成功 / DAG 收敛全部 done |
| `1` | 通用错误 |
| `2` | 平台/网络错误 |
| `3` | 认证错误(平台 CLI 未登录等) |
| `5` | 校验失败(lint / 证据 schema) |
| `10` | 推进中(仅单轮 tick 模式) |
| `20` | 需要调用者决策(附结构化报告) |

## 变更日志

详见英文版 [CHANGELOG.md](./CHANGELOG.md)。

## 设计文档与 Guide

- 完整设计:`docs/zh-CN/omac-cli-design.md`(英文主文档为 `docs/omac-cli-design.md`)
- 工作流知识(随包分发):`omac guide workflow`, `omac guide role <name>`,
  `omac guide artifact <name>`, `omac guide recovery`
- 命令契约与协议细节:`omac <command> --help`

## 测试

macOS / Linux 开发期建议用 editable 安装,让 `omac` 进入 PATH 才能跑 e2e:

```bash
pip install -e .
pip install pytest
python3 -m pytest tests/ -q -m "not live"   # 全量 e2e(含 CLI 子进程级测试)
python3 -m pytest tests/ -q -m live        # live 测试需已登录 multica
```

## License

[MIT](./LICENSE)
