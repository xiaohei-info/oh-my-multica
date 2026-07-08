# 角色模型与配置

全部角色都是工作空间里的 agent,`omac init` 从全量 agent 列表挑选映射,
不使用小队/分组等平台特有概念。

| 角色 | 职责 | 产出 |
|---|---|---|
| planner | 编写设计方案;方案定稿后产出验收文档(用户视角端到端验收点) | 设计方案 + 验收文档 |
| orchestrator | 拆解 manifest DAG;总控验收 fail 后增量扩展 | manifest(全量/增量) |
| reviewer | 评审设计方案/验收文档/manifest/代码 PR(同一 issue 转派) | verdict + report(含评审目标) |
| worker | 按 contract TDD 开发;修 CI 失败与 merge 冲突 | PR + 证据(含 env_setup) |
| acceptor | DAG 收敛后按验收文档端到端走查 | 逐项 pass/fail 结果 |

约束:planner 与 orchestrator 是独立角色(可配同一 agent);
reviewer 强制 ≠ 产出者;acceptor 缺省复用 reviewers 池。

## Architect 特殊角色

当 agent 池中有 `role: architect` 的 agent 时:

### 作为 Worker(架构设计任务)

适用:Wave 0 共享契约设计、跨模块接口定义、架构模式选型、技术栈决策。

执行流程:
1. **读全设计文档**:理解系统整体架构意图
2. **识别关键决策点**:模块边界在哪? 数据流向如何? 依赖方向是否合理? 有哪些跨模块契约?
3. **产出架构制品**:
   - 共享契约代码(DTO/事件/枚举/错误)
   - 架构决策记录(ADR)
   - 模块依赖图
   - 接口规范文档
4. **验收标准**:
   - 契约代码可被 import
   - 契约不变量测试已写
   - 架构决策已文档化
   - 模块边界清晰可验证

### 作为 Reviewer(整体架构评审)

适用:Wave 2 集成后的整体架构评审、跨模块重构的架构一致性检查、关键架构约束的遵守情况审查。

评审重点(架构层面,不是实现细节):
1. **模块边界清晰度**: 是否有越界 import?
2. **契约遵守情况**: 业务模块是否自己定义了应该 import 的契约?
3. **依赖方向合理性**: 是否有循环依赖? 底层是否依赖上层?
4. **设计模式一致性**: 错误处理/数据访问/API 设计风格是否统一?
5. **架构漂移检测**: 对照设计文档检查模块职责是否偏移、是否引入了设计之外的依赖

### Architect 禁止事项

- ❌ 不要陷入实现细节:你关注架构层面,不是变量命名或算法优化
- ❌ 不要自己重写代码:发现问题标出来,回流给对应 worker
- ❌ 不要过度设计:架构服务于需求
- ❌ 不要脱离设计文档:架构评审要对照设计文档

## 配置(`.omac/config.yaml`)

```yaml
engine: multica
workspace: ws_xxx
project: proj_xxx          # multica 必填:issue 归入该 project(repo 在 workspace registry);mock 不需要
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
