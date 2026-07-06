# omac

[![CI](https://github.com/xiaohei-info/oh-my-agent-cluster/actions/workflows/ci.yml/badge.svg)](https://github.com/xiaohei-info/oh-my-agent-cluster/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-1.0.0-blue)](https://github.com/xiaohei-info/oh-my-agent-cluster)

**omac** 是确定性 CLI 驱动的多 Agent 并行开发编排。它把复杂软件开发从「一个
agent 靠长上下文硬扛」变成「契约先行 + manifest DAG + 多 Agent 并行执行 +
结构化证据 + reviewer 独立验收」的可收敛工程流程。

核心理念是**控制反转**:LLM 从「驱动者」降级为「被调用者」——确定性 CLI 程序
承载整个编排循环,planner / orchestrator / reviewer / worker / acceptor 全部是
CLI 派发的、有终点的单次任务。

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
git clone git@github.com:xiaohei-info/oh-my-agent-cluster.git
cd oh-my-agent-cluster
pipx install .
```

**3) 验证(每台都要过):**

```bash
omac --version          # omac 1.0.0
omac init --check       # 引擎 / config 体检
```

**更新到最新:**

```bash
cd oh-my-agent-cluster && git pull && pipx reinstall omac
```

> - 某台不方便配 git 认证:改用 wheel 离线分发 —— 在有仓库的机器 `python3 -m build` 产出 `dist/omac-1.0.0-py3-none-any.whl`,拷到目标机 `pipx install omac-1.0.0-py3-none-any.whl`。
> - 开发调试(在本仓内改代码):可编辑安装 `pip install -e .`(需在 venv 内,或加 `--break-system-packages`)。

## 快速开始

以下命令均可在本仓根目录实测运行(Mock 引擎)。Mock 成员池预设
`alice`、`bob`、`charlie`,下文以这三者为例配置角色。

### 1. 一次性配置(`omac init`)

```bash
# 体检:检查配置文件与角色映射是否就绪
omac init --check

# 写入最小配置(Mock 引擎,使用 mock 成员池内的 agent 名)
omac config set engine mock
omac config set workspace mock-workspace
omac config set roles.planner alice
omac config set roles.orchestrator bob
omac config set roles.workers '["alice"]'
omac config set roles.reviewers '["charlie"]'

# 再次体检,应输出「体检通过」
omac init --check
```

> exit 5 提示"角色不在工作空间 agent 池内"?一定用的是 `alice`/`bob`/`charlie`
> 三者之一,mock 池不接受其他名字。

### 2. 计划与 DAG 拆解(`omac plan`)

`omac plan create` 已实现完整流水线:计划 → 验收文档 → 拆解为 manifest DAG(全程
内置 review 阶段;`--doc` 跳过 planner 直接用现成设计文档,`--no-review` /
`--no-acceptance` 按需关阶段)。`omac plan check` 对你自拆的 manifest 走 lint +
review 门;`omac plan show` 看摘要。字段与流程见 `omac plan --help` 与
`omac guide manifest`。

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

### 4. 知识分发(`omac guide`)

协议细节不在 README 里堆,按需读取 guide 即可:

```bash
omac guide                   # 列出全部 topic
omac guide workflow          # 整体工作流:init → plan → dag run → 异常处理闭环
omac guide manifest          # manifest DAG 拆解方法论与 contract 字段
omac guide roles             # 角色模型与配置
omac guide worker            # worker 执行协议(TDD·证据·env_setup)
omac guide reviewer          # reviewer 评审协议(独立复跑·评审目标)
omac guide recovery          # exit 20 之后的恢复手册
```

## 命令面一览

```
omac
  CORE(调用者/驱动侧)
    plan     create | check | show         计划制定 + DAG 拆解流水线(全程内置 review 阶段)
    dag      run | status | tick           确定性 loop 执行
    node     show | retry | abandon        exit 20 后的决策工具
  WORK(被派发 agent 侧)
    work     show | submit                 统一执行接口(5 类 issue × 产出/评审阶段)
  SETUP
    init     交互式配置 / --check 体检
    config   get | set
  GUIDE
    guide    workflow | manifest | roles | worker | reviewer | recovery
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

详见 [CHANGELOG.md](./CHANGELOG.md)。

## 设计文档与 Guide

- 完整设计:`docs/omac-cli-design.md`(背景、取舍、架构、角色、流程、引擎接口、平台可移植性)
- 工作流知识(随包分发):`omac guide <topic>`(workflow / manifest / roles / worker / reviewer / recovery)
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
