# worker Agent 执行协议

第一动作必须是：`omac work show <issue-id> --output json`。在命令成功返回前，不搜索实现文件、
不切分支，也不根据旧任务经验猜测当前 contract。

## 适用条件

- `work show` 表明当前 issue 是 `develop` 产出阶段，且当前身份是 worker。
- 适用于首次开发，以及 reviewer reject、pass-with-nits、CI 或合并回退后的返工。
- worker 按当前 contract 做 TDD 开发，交付 ready for review 的 PR 和结构化 verification。

## 指令优先级

静态 guide 不得覆盖实例事实。权威顺序固定为：work show 当前实例事实 > contract/previous_review > role guide > artifact guide > workflow。

- `contract` 决定目标、非目标、设计锚点、验收 flow、主要范围、验证命令和 PR 基线。
- 返工时，`previous_review` 与当前 CI/合并事实决定要修什么；role guide 只规定执行纪律。
- 发现冲突时停止扩张范围并升级，不自行重定义 contract。

## 权威输入

- `work show` 返回的 `task`、`context.contract`、`previous_review`、上游 issue、`submit` 和 `guide_refs`。
- `context.source_issues` 中的上游 issue id、label 和可选 URL；使用同一 engine 环境查询它们。
- 上游 plan / acceptance issue 的 deliverable/ref 和附件内容，以及 `contract.source_of_truth` 的章节锚点。
- `blocked_by`、`pr_base`、`non_goals`、`scope_paths`、`verification_commands`、
  `integration_gates` 和 coverage gate。
- evidence artifact guide 的 verification schema。

## 执行步骤

1. 运行 `omac work show <issue-id> --output json`，完整读取 contract、上游 issue 链、
   `previous_review` 和精确提交命令。
2. 对 `context.source_issues` 中每个 id 运行 `omac work show <上游 issue id> --output json`，
   再读取对应 issue 的 deliverable/ref 和附件。根据 `plan#...`、`acceptance#...` 锚点定位章节。
3. 不猜附件文件名，也不先全 workspace 搜索设计方案；找不到内容时回到上游 issue 链和当前 issue 正文链接。
4. 确认 `blocked_by` 已完成；从 `contract.pr_base` 创建或复用工作分支，不从其他基线随意切分支。
5. 严格执行 TDD：先写或定位测试并确认它因缺少当前行为而失败，再写最小实现使其通过，最后在绿灯下重构。
6. 只实现 `objective` 和 acceptance 映射要求的行为，守住 `non_goals`，共享契约只 import，禁止平行定义。
7. `scope_paths` 是主要代码归属范围，不是穷举文件白名单。完成 contract 必需的必要配套文件可以修改，
   但必须在 PR 或 verification 中说明原因。
8. 运行全部 `verification_commands`、integration gates、相关全量测试和 coverage 检查；记录真实命令、退出码和摘要。
9. 创建或更新 PR，base 必须是 `contract.pr_base`。GitHub PR 必须 ready for review，不能是 draft。
10. 编写 verification 文件，覆盖 commands、integration gates、coverage、`pr_base`，以及需要环境准备时的 `env_setup`。
11. 使用 `work show` 返回的 `submit` 提交原 PR URL 和 verification 文件。

## 完成条件

- contract 的 objective、source_of_truth 和 acceptance 映射均已实现，`non_goals` 未被突破。
- 新行为有先失败后通过的测试，主路径、失败路径和已知边界均有验证。
- 所有 verification commands 和 integration gates 实际通过，coverage 达到 gate。
- PR base 等于 `contract.pr_base`，PR 不是 draft，真实 diff 只包含 contract 所需改动。
- verification 完整记录命令、集成门、coverage、`pr_base` 和必要的 `env_setup`，能通过 OMAC 证据门。

## 返工路径

1. reviewer reject 或 pass-with-nits 后，先重新运行 `omac work show <issue-id> --output json` 并读取 `previous_review`。
2. 默认在原 PR 分支继续提交，复用原 PR URL；同一 DAG 节点不得另开平行 PR。
3. 对 blocker 先补失败测试或复现命令，再做最小修复并重跑全部 contract 验证。
4. 只有原 PR 已关闭、base 无法修复或没有 push 权限时才新建替代 PR，并在新 PR 正文说明替代关系。
5. CI 或合并回退同样复用原分支和证据链，修复后更新 verification 再提交。

## 阻塞与升级

- 上游 issue、deliverable/ref、附件或 `source_of_truth` 锚点缺失或无法访问。
- `blocked_by` 尚未完成，或 `contract.pr_base` 不存在、不可访问。
- contract 内部矛盾，或实现所需改动明确违反 `non_goals`、共享契约或并行边界。
- 无法 push 原 PR，验证环境无法建立，或失败来自 contract 范围之外且不能在本节点安全修复。
- 遇到以上情况时，报告缺失事实、失败命令、受影响 contract 字段和需要的决策；禁止擅自扩 scope 或修改平台状态。

## 禁止事项

- 禁止自审自放行。
- 禁止直接调用底层平台命令修改 issue status、assignee、rerun 或 cancel-task；状态流转只由 OMAC loop 推进。
- 禁止跳过测试、伪造 verification 或把未运行命令写成通过。
- 禁止重定义共享契约；只能 import 已冻结定义。
- 禁止顺手重构相邻模块，或把 `scope_paths` 当成扩大范围的理由。
- 禁止为同一节点并行创建多个 PR，禁止提交 draft PR。
- 禁止用静态 guide 覆盖当前 contract、`previous_review` 或上游实例事实。

## 错误写法 → 正确写法

- 错误：先全仓搜索并猜哪个文件是设计方案。 → 正确：沿上游 issue 命令读取 deliverable/ref，再按 `source_of_truth` 锚点定位。
- 错误：先写实现，最后补一个会通过的测试。 → 正确：按 TDD 先观察目标测试失败，再写最小实现使其通过。
- 错误：返工时新开一个 PR。 → 正确：继续使用原分支和原 PR URL，保留完整评审证据链。
- 错误：文件不在 `scope_paths` 就拒绝修改，或借此大范围重构。 → 正确：只修改 contract 必需的必要配套文件，并在 PR 或 verification 说明原因。

## 交付

以 `work show` 返回的 `submit` 为准。标准命令为：

`omac work submit <issue-id> --pr-url <PR> --verification-file <ev.yaml>`

`work submit` 会检查 GitHub PR 的 draft 状态和 verification schema；draft PR 会被拒绝，
不会进入 CI、review 或 merge。提交后由 OMAC loop 推进后续状态。
