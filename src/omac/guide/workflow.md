# omac 整体工作流

omac 是确定性 CLI 驱动的多 Agent 并行开发编排。Loop 驱动 Agent,LLM 只做被派发的、有终点的专家任务。

## 标准路径

1. `omac init` 配置 engine、workspace、project 和角色映射。
2. `omac plan create --name <feature> [--goal 需求 | --doc 设计方案文档]`
   生成设计方案、验收文档和 manifest DAG。
3. `omac dag run .omac/<feature>.yaml` 前台运行确定性 loop,直到收敛或 exit 20。
4. exit 20 后用 `omac dag status`、`omac node show`、`omac node retry|accept|abandon`
   做显式决策,再重跑 `omac dag run`。

## plan 到 dag run 的衔接

`omac plan create/resume exit 0` 表示设计、验收和拆解已经收敛,manifest 已落盘。
此时不要猜文件名;读取命令输出里的 `manifest:` 和 `下一步: omac dag run ...`。
agent 继续主线时直接执行这条下一步命令,让 `dag run` 接管开发、CI、review、merge 和总控验收。

## 阶段导航

| 阶段 | 角色 guide | 产物 guide |
|---|---|---|
| 方案设计 | `omac guide role planner` | `omac guide artifact design` |
| 验收定义 | `omac guide role planner` | `omac guide artifact acceptance` |
| DAG 拆解 | `omac guide role orchestrator` | `omac guide artifact manifest` |
| 开发执行 | `omac guide role worker` | `omac guide artifact evidence` |
| 独立评审 | `omac guide role reviewer` | `omac guide artifact evidence` |
| 总控验收 | `omac guide role acceptor` | `omac guide artifact acceptance` |
| 异常恢复 | `omac guide recovery` | - |

## 关键机制

- issue 的范围是一个完整阶段:产出、评审、回退都在同一条 issue 上。
- 被派发 agent 永远先跑 `omac work show <issue-id>`,再按 show 里的 submit 命令交付。
- 下游 issue 的 body 与 `work show` 会列出上游 issue;人从 Markdown 链接回跳,
  agent 从 `omac work show <上游 issue id>` 读取 deliverable/ref/附件,再按
  `contract.source_of_truth` 的章节锚点定位内容。
- review 是各 issue 类型内的阶段,不是单独 issue。
- 验收文档锚定需求目标,manifest `contract.acceptance` 必须引用验收 flow。
- worker/reviewer/acceptor 的证据都走结构化 schema,缺项在 `omac work submit` 当场失败。
- 状态在 manifest + 平台 work item 中,重跑 `dag run` 即续跑。

## 监督边界

`omac dag run` 是前台阻塞进程。不要放后台,不要在没有活跃前台进程时声称“继续监督”。
要么把命令跑到返回再汇报,要么明确说明当前未在监督。

## 入口辨识

- 标题带 `[DAG:...]` 的 issue 是 omac 派发任务,必须走 `omac work show/submit`。
- 无此前缀的 issue 是普通 issue,除非正文明确要求运行 omac 命令。

## 下一步

运行 `omac guide` 查看完整索引。机制问题读 `workflow`,角色行为读 `role ...`,文件格式读 `artifact ...`。
