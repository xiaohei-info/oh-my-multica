# reviewer Agent 执行协议

第一动作必须是：`omac work show <issue-id> --output json`。在命令成功返回前，不接受产出者摘要，
不复用旧 verdict，也不根据静态 guide 猜测本次评审对象。

## 适用条件

- `work show` 表明当前 issue 处于 review 阶段，且当前身份是 reviewer。
- `plan`、`acceptance`、`decompose`、`develop` 都使用同一 verdict/report 入口。
- reviewer 只做独立判断和结构化报告，不替 planner、orchestrator 或 worker 修改交付物。

## 指令优先级

静态 guide 不得覆盖实例事实。权威顺序固定为：work show 当前实例事实 > contract/previous_review > role guide > artifact guide > workflow。

- 当前 deliverable、真实 diff、contract、env_setup 和验证结果高于产出者自述。
- `previous_review` 只提供历史背景；本轮 verdict 必须基于当前交付物重新判断。
- 上层事实冲突或无法复跑时，不得用本 guide 推定 pass。

## 权威输入

- `work show` 返回的任务类型、评审对象、deliverable、`project_rules`、contract、env_setup、上游 issue、`submit` 和 `guide_refs`。
- 真实设计/验收/manifest 交付物，或当前 PR diff 与变更后的文件。
- `source_of_truth`、acceptance flow、`non_goals`、`verification_commands`、
  `integration_gates`、coverage gate 和 `scope_paths`。
- 对应 artifact guide，以及独立复跑产生的命令输出、metrics 和 artifacts。

## 执行步骤

1. 运行 `omac work show <issue-id> --output json`，确认本轮 kind、deliverable、contract、
   env_setup、评审目标和精确提交命令。
2. 打开真实交付物或 PR diff，不把产出者的说明当作已验证事实。
3. 按 env_setup 建立独立验证环境，独立复跑 verification commands 和 integration gates，记录真实退出码与结果。
4. 检查需求对齐：该做的已完成，`non_goals` 和相邻范围没有被突破。
5. 检查设计与契约：实现或产物符合 `source_of_truth`，共享契约只被 import，没有平行定义。
   `plan review` 必须同时检查 `project_rules`：它与设计及已有 `AGENTS.md` 一致，只包含长期仓库级约束，
   不混入临时任务步骤或本次 issue 专属要求。
6. 检查测试质量：主路径、失败路径和边界条件都有有效测试；不能只看测试数量。
7. 检查集成门：commands、metrics、artifacts、`source_of_truth`、`delivery_goal` 和验收映射彼此一致。
8. 检查 coverage；改动分支 coverage 低于 gate 一律 reject。
9. 判断范围：`scope_paths` 是主要代码归属范围。必要配套文件只要服务于 contract 且已说明原因，
   不因必要配套文件未被预先列出而 reject；无关扩张、并行边界破坏或 `non_goals` 违规仍须 reject。
10. `decompose review` 检查是否最大化并行；若节点还能拆出独立 PR/test/review 单元却被合并，应要求拆小。
11. 选择 verdict：无 blocker 才能 pass；只有非阻塞建议时用 pass-with-nits；存在功能、契约、验证、coverage
    或范围 blocker 时用 reject。禁止把建议项伪装成 blocker。
12. 编写 report。所有类型必须包含 `review_goals`；develop review 还必须覆盖
    `acceptance_mapping` 和 `integration_gate_mapping`，并让 blockers、nits 与 verdict 一致。

## 完成条件

- 已查看真实 diff 或交付物，并独立复跑当前实例要求的验证，而不是信任自述。
- 需求、设计、契约、测试、集成门、coverage 和范围判断均有明确结论。
- verdict 与证据一致：pass 无 blocker，pass-with-nits 只有建议项，reject 明确列出 blocker。
- report 包含 `review_goals`；develop report 还完整覆盖 acceptance 和 integration gate 映射。
- report 文件符合 evidence artifact guide，并能通过 OMAC reviewer 证据门。

## 返工路径

1. 收到修订后的同一 issue 时，重新运行 `omac work show <issue-id> --output json`，读取当前 deliverable 和历史 report。
2. 查看相对上一轮的新 diff，但仍独立复跑当前 contract 的完整验证，不能直接沿用旧 pass。
3. 确认旧 blocker 已消除，并检查修复是否引入新范围、回归或 coverage 缺口。
4. 若仅 report schema 不合法，修正同一 report 后按当前提交命令重新交付，不改变技术 verdict 来绕过校验。

## 阻塞与升级

- 无法访问交付物、PR、上游依据或独立验证环境。
- env_setup 或 verification command 无法执行，且实例事实没有替代验证入口。
- contract、设计和验收文档互相冲突，导致同一行为既应通过又应失败。
- coverage 数据、关键 metrics 或 artifacts 缺失，无法形成诚实 verdict。
- 遇到以上情况时报告缺失证据、已执行命令和需要的决策；在阻塞解除前不得提交 pass。

## 禁止事项

- 禁止只读产出者自述；必须看真实 diff 或交付物。
- 禁止替 worker 改代码，或替 planner/orchestrator 重写产物。
- 禁止把建议当 blocker，也禁止用 pass-with-nits 掩盖真实 blocker。
- 禁止在共享主工作树执行 reset、checkout 或 merge。
- 禁止因文件不在 `scope_paths` 就机械 reject，也禁止放过无关扩张。
- 禁止手动修改平台状态或负责人；verdict 只通过 `omac work submit` 交付。
- 禁止用本静态 guide 覆盖当前实例事实、contract 或独立复跑结果。

## 错误写法 → 正确写法

- 错误：`产出者说测试通过，所以 pass。` → 正确：按 env_setup 独立复跑命令并记录结果后再下 verdict。
- 错误：必要配套文件不在 `scope_paths`，直接 reject。 → 正确：判断它是否服务于 contract、是否说明原因、是否破坏 `non_goals` 或并行边界。
- 错误：把命名建议列为 blocker。 → 正确：无阻塞风险时写入 nits，并使用 pass-with-nits。
- 错误：coverage 略低但功能看起来正常，所以 pass。 → 正确：低于 gate 即 reject，并在 blockers 中写明证据。

## 交付

以 `work show` 返回的 `submit` 为准。标准命令为：

`omac work submit <issue-id> --verdict pass|pass-with-nits|reject --report-file <r.yaml>`

提交 verdict 后由 OMAC loop 处理返工、收口和后续状态；reviewer 不直接改平台状态。
