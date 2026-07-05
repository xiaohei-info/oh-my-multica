# omac

[![CI](https://github.com/xiaohei-info/oh-my-agent-cluster/actions/workflows/ci.yml/badge.svg)](https://github.com/xiaohei-info/oh-my-agent-cluster/actions/workflows/ci.yml)

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

- **omac CLI**:`pipx install omac`(或开发期 `pip install -e .`)
- **平台 CLI**:Multica 引擎需 `multica` CLI 已登录(`multica` 在 PATH,认证存 `~/.multica`)
- **Python** >= 3.10,依赖 `PyYAML`(pipx 自动隔离)

Mock 引擎零外部依赖,仅用于本地演示、CI 与首次试跑。

## 安装

```bash
pipx install omac
```

开发期(本仓内):

```bash
pip install -e .
```

安装后确认:

```bash
omac --version
```

## 快速开始

以下命令均可在本仓根目录实测运行(Mock 引擎)。

### 1. 一次性配置(`omac init`)

```bash
# 体检:检查 multica CLI / 配置文件 / 角色映射是否就绪
omac init --check

# 写入最小配置(Mock 引擎)
omac config set engine mock
omac config set workspace mock-workspace
omac config set roles.planner planning-agent
omac config set roles.orchestrator arch-agent
omac config set roles.workers '["backend-agent", "fe-agent"]'
omac config set roles.reviewers '["review-agent"]'

# 再次体检,应输出「体检通过」
omac init --check
```

### 2. 计划与 DAG 拆解(`omac plan`)

```bash
# 从零制定计划 + 验收文档 + 拆解为 manifest DAG
omac plan create --name feature-x

# 跳过 planner 制定计划,直接给设计文档进验收文档 + 拆解
omac plan create --name feature-x --doc docs/design.md

# 调用者自己拆好的 manifest:只走 lint 门 + manifest review
omac plan check .orchestrator/feature-x.yaml
```

### 3. 确定性 Loop 执行(`omac dag run`)

```bash
# 前台循环直到收敛或需决策
omac dag run .orchestrator/feature-x.yaml

# 查看快照(不推进)
omac dag status .orchestrator/feature-x.yaml

# 单轮推进后退出(exit 0 收敛 / 10 推进中 / 20 需决策)
omac dag tick .orchestrator/feature-x.yaml
```

### 4. 异常决策(`omac node`)

`dag run` 以 exit 20 退出时,由调用者决策:

```bash
# 看单节点完整证据链
omac node show feature-x.yaml user-api

# 显式重试(可换人)
omac node retry feature-x.yaml user-api --worker other-agent

# 放弃节点,解锁非硬依赖下游
omac node abandon feature-x.yaml user-api

# 决策后重跑(重跑即续跑,done 节点复用)
omac dag run .orchestrator/feature-x.yaml
```

### 5. 被派发 Agent 的接口(`omac work`)

被派发的 agent 永远只需要两个命令:

```bash
# 取任务上下文与执行协议
omac work show <issue-id>

# 提交交付物(左移校验:缺什么当场打回,exit 5)
omac work submit <issue-id> --pr-url <PR> --verification-file ev.yaml
```

### 6. 知识分发(`omac guide`)

```bash
# 列出全部 topic
omac guide

# 按需阅读
omac guide workflow     # 整体工作流
omac guide manifest    # manifest DAG 拆解方法论
omac guide roles       # 角色模型与配置
omac guide worker      # worker 执行协议
omac guide reviewer    # reviewer 评审协议
omac guide recovery    # exit 20 之后的恢复手册
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

## 设计文档与 Guide

- 完整设计:`docs/omac-cli-design.md`(背景、取舍、架构、角色、流程、引擎接口、平台可移植性)
- 工作流知识(随包分发):`omac guide <topic>`(workflow / manifest / roles / worker / reviewer / recovery)
- 命令契约与协议细节:`omac <command> --help`

## 测试

```bash
# 全量测试(live 测试默认 skip)
python3 -m pytest tests/ -q -m "not live"

# 含 live Multica 测试(需 multica CLI 登录 + 环境变量)
python3 -m pytest tests/ -m "live"
```

## License

[MIT](./LICENSE)
