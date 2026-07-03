# 角色模型与配置

全部角色都是工作空间里的 agent,`omac init` 从全量 agent 列表挑选映射,
不使用小队/分组等平台特有概念。

| 角色 | 职责 | 产出 |
|---|---|---|
| planner | 制定计划;计划定稿后产出验收文档(用户视角端到端验收点) | 计划 + 验收文档 |
| orchestrator | 拆解 manifest DAG;总控验收 fail 后增量扩展 | manifest(全量/增量) |
| reviewer | 评审计划/验收文档/manifest/代码 PR(同一 issue 转派) | verdict + report(含评审目标) |
| worker | 按 contract TDD 开发;修 CI 失败与 merge 冲突 | PR + 证据(含 env_setup) |
| acceptor | DAG 收敛后按验收文档端到端走查 | 逐项 pass/fail 结果 |

约束:planner 与 orchestrator 是独立角色(可配同一 agent);
reviewer 强制 ≠ 产出者;acceptor 缺省复用 reviewers 池。

配置(.orchestrator/config.yaml):

```yaml
engine: multica
workspace: ws_xxx
roles:
  planner: planning-agent
  orchestrator: arch-agent
  workers: [backend-agent, fe-agent]
  reviewers: [review-agent-a, review-agent-b]
defaults: { max_parallel: 4, poll_interval: 30, coverage_gate: 90 }
ci:    { check_command: "gh pr checks {pr_url}" }   # 可选
merge: { command: "gh pr merge {pr_url} --squash" } # 可选
acceptance: { max_rounds: 3 }
```
