# Agent-first 内容架构设计

> 状态：已于 2026-07-11 落地并通过完整测试。

## 目标

将 OMAC 的内容通道收敛为清晰的混合架构：issue 面向 Human，`work` 与 `guide`
面向 Agent。动态任务事实由 `work show` 提供，长期行为规则由静态 guide 提供，避免
同一规则在 issue、help、guide 和运行时输出中平行维护。

## 受众边界

| 内容通道 | 主要受众 | 职责 |
|---|---|---|
| issue 标题与正文 | Human | 目标、阶段、负责人、完成标准、非目标、上游依据 |
| issue 顶部 bootstrap | Agent | 仅提供一条 `omac work show <id> --output json` 入口 |
| `omac work show` | Agent | 当前 issue 的完整实例事实、指令优先级、精确动作、guide refs、submit 命令 |
| `omac work submit` | Agent | 结构化提交结果或可自纠错错误 |
| `omac guide ...` | Agent | 角色协议、产物 schema、总控流程与恢复协议 |
| 其它 CLI help/status/web | Human/Operator | 安装、观察、决策和故障定位 |

## 核心原则

1. **实例事实优先**：`work show` 当前实例事实 > contract / previous review > role guide
   > artifact guide > workflow 总览。
2. **内容守恒**：现有 guide 的独有规则不得删除；只能保留、合并或扩展，并在迁移矩阵中
   登记最终位置。
3. **按任务裁剪**：`work show` 根据 `kind × phase` 返回精确 `guide_refs`，Agent 不自行猜
   topic，也不默认读取全量 guide。
4. **Human-first issue**：正文不再复制角色协议、submit 参数和平台状态铁律；只保留人能
   快速理解的任务内容与一条 Agent 入口。
5. **零破坏业务契约**：保持现有命令路径、退出码、engine 抽象和 submit 校验不变；新增
   JSON 字段采用向后兼容扩展。

## 动态层

### issue bootstrap

issue 第一段固定为一条 Agent 入口，带当前 engine/workspace/project 环境：

```bash
OMAC_ENGINE=... OMAC_WORKSPACE_ID=... OMAC_PROJECT_ID=... omac work show <id> --output json
```

正文其余部分采用中文 Human 摘要：任务类型、目标、验收标准、非目标、任务详情、目标仓库
和上游 issue。上游产物可保留在折叠区，避免正文首屏被长文档占满。

### `work show`

输出继续保留原有 `task/context/protocol/submit` 字段，并新增：

- `task.status/blocked_by/wave/bounces`：平台当前状态、依赖、波次与回退计数。
- `context.issue_description`：issue 当前正文，保证无 contract 的任务仍有完整输入。
- review 阶段的 `context.artifacts/verification/*_ref`：让 reviewer 直接取得真实 PR、
  worker 证据与附件引用。
- `authority`：固定的指令优先级列表。
- `guide_refs`：当前 `kind × phase` 必须读取的精确 guide 命令列表。

映射如下：

| kind × phase | guide refs |
|---|---|
| plan × authoring | `role planner`, `artifact design` |
| acceptance × authoring | `role planner`, `artifact acceptance` |
| decompose × authoring | `role orchestrator`, `artifact manifest` |
| develop × authoring | `role worker`, `artifact evidence` |
| final-acceptance × authoring | `role acceptor`, `artifact acceptance`, `artifact evidence` |
| plan × review | `role reviewer`, `artifact design` |
| acceptance × review | `role reviewer`, `artifact acceptance` |
| decompose × review | `role reviewer`, `artifact manifest` |
| develop × review | `role reviewer`, `artifact evidence` |

`work show` 与 `work submit` 默认使用 JSON；成功和错误均结构化，错误写入 stderr；
显式 `--output table` 保留人类调试视图。

`work submit` 成功结果使用 `submitted_phase` 表示本次调用所处阶段，使用 `next_phase`
表示提交后的阶段；不得把 authoring 推进到 review 误报成 reviewer 已提交 verdict。

## 静态 guide 层

现有 topic 和命令路径全部保留。

### role guide 固定骨架

1. 适用条件
2. 指令优先级
3. 权威输入
4. 顺序执行步骤
5. 完成条件
6. 返工路径
7. 阻塞与升级条件
8. 禁止事项
9. 错误写法 → 正确写法
10. 交付命令

### artifact guide 固定骨架

1. 使用场景
2. 最小合法示例
3. 字段语义
4. validator 硬门
5. 常见非法示例
6. 修正方式
7. submit 命令

### workflow / roles / recovery

- `workflow` 给总控 Agent：init → plan → dag run → exit 20 的确定性主线。
- `roles` 给 Agent 做角色路由，防止 role mixing。
- `recovery` 给总控 Agent：区分可自动恢复、必须请求 Human 决策和禁止擅自决策。

## 内容迁移矩阵

| 现有语义 | 最终位置 | 处理 |
|---|---|---|
| 前台阻塞监督、不得后台假监督 | workflow / recovery | 保留并扩展停止条件 |
| planner 的真实问题、数据、边界、兼容性 | role planner / artifact design | 按行为与产物拆分 |
| Wave 0/1/2、最大并行、blocked_by | role orchestrator / artifact manifest | 保留 |
| 低推理预算、不能补全隐含上下文 | planner/orchestrator 与 design/acceptance/manifest | 合并重复措辞，角色内保留提醒 |
| worker 上游链、TDD、PR base、返工复用 PR | role worker | 保留并改成顺序步骤 |
| scope_paths 不是穷举白名单 | orchestrator/worker/reviewer/manifest | 保留角色判定差异 |
| reviewer 独立复跑、只读共享态、覆盖率门 | role reviewer / artifact evidence | 保留 |
| acceptor 逐 flow pass/fail、fail notes | role acceptor / artifact evidence | 保留 |
| 三类 evidence schema | artifact evidence | 保留并让示例通过真实 validator |
| exit 20 决策表、retry/accept/abandon | recovery | 保留并增加升级 Human 的报告格式 |

## 测试策略

- 先写失败测试，锁定 `guide_refs`、完整 `work show` 上下文和 Human-first issue。
- guide 测试覆盖迁移矩阵中的每条独有语义。
- YAML/Markdown 示例由真实 acceptance、manifest、evidence validator 校验。
- 每种 `kind × phase` 均验证 guide refs 与 submit 参数。
- issue 测试禁止重新出现多步骤 guide-first bootstrap 和大段 Agent 铁律。
- 完成前运行 `python3 -m pytest tests/`。
