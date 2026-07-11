# omac 工作流（Controller Agent）

本 guide 面向启动、推进和恢复 omac 流程的 Controller Agent。它只解释稳定机制，
不替代具体 issue 的实例事实。处理已派发任务时，先运行
`omac work show <issue-id> --output json`。

## 适用条件

- 需要从需求启动设计、验收、拆解和开发闭环。
- 需要接续已经存在的 manifest DAG。
- 需要判断当前应运行 `plan`、`dag` 还是恢复命令。

## 指令优先级

发生冲突时按以下顺序执行：

1. `work show` 返回的当前实例事实。
2. 当前任务的 `contract` / `previous_review`。
3. 对应 role guide。
4. 对应 artifact guide。
5. 本 workflow 总览。

## 标准路径

1. `omac init` 配置 engine、workspace、project 和角色映射。
2. `omac plan create --name <feature> [--goal 需求 | --doc 设计方案文档]`
   生成设计方案、验收文档和 manifest DAG。
3. `omac dag run .omac/<feature>.yaml` 前台运行确定性 loop，直到收敛或 exit 20。
4. exit 20 后运行 `omac dag status`、`omac node show`、
   `omac node retry|accept|abandon` 做显式决策，再重跑 `omac dag run`。

## plan 到 dag run 的衔接

`omac plan create/resume exit 0` 表示设计、验收和拆解已经收敛，manifest 已落盘。
不要猜文件名；读取命令输出中的 `manifest:` 和 `下一步: omac dag run ...`。
Controller Agent 应直接执行这条下一步命令，让 `dag run` 接管开发、CI、review、merge
和总控验收。

## 阶段导航

| 阶段 | 角色 guide | 产物 guide |
|---|---|---|
| 方案设计 | `omac guide role planner` | `omac guide artifact design` |
| 验收定义 | `omac guide role planner` | `omac guide artifact acceptance` |
| DAG 拆解 | `omac guide role orchestrator` | `omac guide artifact manifest` |
| 开发执行 | `omac guide role worker` | `omac guide artifact evidence` |
| 独立评审 | `omac guide role reviewer` | 对应被评审产物 + `omac guide artifact evidence` |
| 总控验收 | `omac guide role acceptor` | `omac guide artifact acceptance` + `evidence` |
| 异常恢复 | `omac guide recovery` | - |

具体派发任务不需要预读全部 guide。读取 `work show` 的 `guide_refs`，只加载当前任务需要的
最小知识集合。

## 稳定机制

- 一条 issue 承载一个完整任务：产出、评审和返工都发生在同一时间线上。
- 标题带 `[DAG:...]` 的 issue 是 omac 派发任务；Agent 先跑
  `omac work show <issue-id> --output json`，再按返回的 `submit` 交付。
- 下游 issue 的 Human 内容提供上游链接；Agent 从 `work show.context.source_issues`
  读取引用，再查询上游实例上下文和 deliverable/ref。
- review 是各 issue 类型的阶段，不是另一条 issue。
- 验收文档锚定需求目标，manifest `contract.acceptance` 必须引用验收 flow。
- worker、reviewer、acceptor 都提交结构化证据；缺项由 `omac work submit` 当场拒绝。
- 状态同时落在 manifest 与平台 work item 中；重跑 `dag run` 会复用已完成节点并续跑。

## 监督边界

`omac dag run` 是前台阻塞进程。不要放到后台，也不要在没有活跃前台进程时声称
“继续监督”。要么把命令运行到返回再汇报，要么明确说明当前没有运行中的监督进程。

## 完成条件

- `plan create/resume` exit 0 后已经执行输出中的 `下一步`。
- `dag run` exit 0，且 manifest 中全部节点处于允许的终态。
- 若命令 exit 20，已转入 `omac guide recovery`，不能把它报告为完成。

## 入口辨识

- 标题带 `[DAG:...]`：按 omac 实例任务处理。
- 无此前缀：按普通 issue 处理，除非正文明确要求运行 omac 命令。

运行 `omac guide` 查看 topic 索引；不要凭记忆编造 topic 或 submit 参数。
