# exit 20 恢复协议（Controller Agent）

`omac dag run` 以 exit 20 退出，表示确定性引擎需要调用者决策。它不是成功，也不是可以
静默重试的普通错误。stdout 中的结构化报告是本次恢复的实例事实。

## 指令优先级

1. exit 20 报告与 `omac dag status <manifest> --output json`。
2. `omac node show <manifest> <key>` 的节点证据链。
3. 若节点已有 issue，`omac work show <issue-id> --output json` 的实例上下文。
4. manifest contract 与 previous review。
5. 本恢复 guide。

## 决策流程

1. 运行 `omac dag status <manifest> --output json` 获取全景快照。
2. 对每个未决节点运行 `omac node show <manifest> <key>`，读取验证输出、reviewer report、
   PR、平台 issue 链接和回退计数。
3. 选择一个显式动作：
   - `omac node retry <manifest> <key> [--worker 换人]`：重置为 todo。
   - `omac node accept <manifest> <key>`：人工接受已知风险并标记 done。
   - `omac node abandon <manifest> <key>`：放弃该节点，解锁非硬依赖下游。
   - 若 DAG 尚未开始执行，可修改 manifest 后运行 `omac dag check`。
   - 若 DAG 已开始执行且问题属于 contract、验收映射或拓扑定义，使用受控
     `omac dag amend propose`，禁止直接覆盖运行中 manifest。
4. 重新运行 `omac dag run <manifest>`。已 done 节点会复用，其余节点续跑。

### 阶段级恢复与 merge 观察

- 恢复以 issue 的真实 `phase` 为准：authoring 只恢复 worker；reviewer run 失败或结束但未提交 verdict 时，保留同一 issue、worker 的 PR/verification 与 review subject，在 `review` 阶段重新派发 reviewer；不得错误退回 worker。
- `merging` 只观察已经持久化的 merge intent/request。GitHub/平台返回 `UNKNOWN` 或临时读取失败时，节点留在 `merging`，不消耗 `retry.merge`、不回退 worker、也不再发送第二次 merge 请求。
- 认证或授权失败不是暂态观察结果：OMAC 会本地持久化 `blocked` 并保留 work item、PR 和 merge marker，不会重新发起合入。修复凭证/权限、远端核实 PR 后，读取节点证据并使用提示的显式恢复命令再运行 DAG。
- 只有明确的 `CLOSED_UNMERGED` 或已知 merge 命令失败才进入 merge 失败/回退语义。恢复期间仍必须以远端 `MERGED + mergedAt` 作为 done 的唯一事实。

## plan / decompose review 耗尽后的继续决策

当 `omac plan create/resume` 因 plan、acceptance 或 decompose 的 review 轮次耗尽而
exit 20 时，先读取报告中的 `item_id`、`rounds`、`last_opinion` 和 `next_action`，再由
Human 或获授权的 operator 明确决定是否增加一轮：

```bash
omac plan continue-review --dag-key decompose-p-xxxx \
  --reason "Human approved one additional review round"
omac plan resume --plan-id p-xxxx
```

- `continue-review` 在同一 work item 上写入小型 `review_continuation` decision，绝对上限
  只增加一轮；它不清零 `review_bounce`，未消费上一轮授权时也拒绝继续叠加。
- review 或 machine guard 的预算耗尽时，OMAC 会把同一 issue 投影为
  `status=blocked`、`phase=review`，并写入有界的 `omac.decision-required/v1`
  metadata（含 gate、轮次、resume issue ID 和已有证据引用）。完整问题仍由
  review 或 machine-feedback 附件承载，不复制进 metadata。
- exhausted reject 会通过 OMAC `reset_review` 和 status 恢复到 authoring/todo，让 producer
  先更新交付；final pass-with-nits 已有新交付时只开放下一 Reviewer round。
- 该命令不修改 `.omac/config.yaml` 或 `retry.review`，因此不会为了单次人工决策制造项目
  Git revision 漂移。`retry.review` 仍是新任务和旧自动化入口的默认预算。
- 命令只读检查 active Agent；发现活跃 run 时拒绝，不会自动 cancel。等待当前 run 结束后
  再执行，除非调用者另行选择现有的显式取消/重启流程。

## 动作选择

| 报告信号 | 先检查 | 推荐动作 |
|---|---|---|
| `reviewer reject` | report.blockers、真实 diff、失败命令 | 修复同一节点后 `omac node retry` |
| `contract-boundary-conflict` | `decision_required.conflict_codes`、review report 引用、当前 contract 的 `responsibility` | 若 Reviewer 要求越界，保留 contract 并给出纠正事实后对同一节点 `omac node retry`；若 contract 本身缺失真实上游输入，先 amendment，再按批准的恢复阶段续接同一 issue/PR。 |
| CI 失败 | CI 日志、verification.commands | 修 CI 后 retry；若 contract 不合理则改 contract 或拆新节点 |
| merge 回退耗尽 | PR base、冲突文件、集成分支 | 换 worker retry，或手工解决冲突后重跑 |
| `acceptance.max_rounds` 耗尽 | fail 清单、增量 manifest | 降范围、补节点，或显式 accept/abandon |

`accept` 只用于接受已知风险，不是跳过失败验证。`retry` 必须有新的事实或方案，
不能原样重复已失败的尝试。

## 运行中 DAG 的受控 amendment

当已批准 DAG 开始执行后才发现 contract、验收责任或依赖边错误，不要重新运行整份
`plan`，也不要手工编辑 manifest。先准备 Reviewer/blocker 报告，再提供权威设计文档路径：

```bash
omac dag amend propose .omac/project.yaml \
  --blocked-node bootstrap-console \
  --report-file /tmp/dag-review.md \
  --docs docs
```

- Orchestrator 只提交 `omac.dag-amendment/v1` 的结构化 operations；不能写运行态字段。
- 全局 acceptance responsibility 迁移必须使用 `update-responsibility`：只携带三个责任字段、
  `clear_legacy_acceptance: true` 和按名称定位的 gate `acceptance_refs` patch，不能复制完整 contract。
  done/merged 默认仍不可变；只有带 `historical_contract_correction: true` 与 operation reason 的
  acceptance-only 校正才允许，且只写 manifest 和 `historical_contract_correction/synced` ledger 条目，
  不恢复 Store 阶段、不派发 Agent、不重放 merge。
- 对无 work item 的未开始节点，省略 `resume_stage` 保持 definition-only；任何显式
  `resume_stage: review|authoring|merging` 都要求已有 work item。对已有 work item，未提供 `resume_stage` 时职责
  迁移仍按旧规则最小恢复到 review；需要覆盖时，在同一个 `update-responsibility` operation 中设置它，绝不能再为同一节点
  增加第二个 `resume` operation。`merging` 要求 Reviewer pass 与 PR：accept 先静默同步新 contract/contract_ref，
  再保持 review verdict、PR/verification、Store status/phase 和人员分配不变；它不派发 Agent，也不观察或请求
  merge，后续 `dag run` 统一负责。historical contract correction 禁止设置 `resume_stage`。
- OMAC 先做 DAG、循环、依赖、agent pool、done/merged 不可变性和 ownership migration
  机器门，再交给独立 Reviewer。
- Reviewer pass 后命令以 exit 20 停在 `confirmation`，不会自动应用。人工审阅生成的
  amendment 文件后再运行返回的 `omac dag amend accept ...` 命令。
- amendment 的 review 或 machine guard 预算耗尽不是 confirmation。issue 会停在
  `blocked/review` 并携带 `decision_required`；人工决定继续后，使用原
  `omac dag amend propose ... --resume-issue-id <issue-id>` 命令续接同一 issue、交付与
  Reviewer 历史，不创建新的 amendment issue。
- 普通 `--resume-issue-id` 不会作废合法的 Reviewer-pass confirmation，也不会创建新的
  Agent Run。若旧 confirmation 的 amendment 内容本身必须替换，必须显式追加
  `--restart-authoring`，并完整提供本轮新的 report/docs 输入：

  ```bash
  omac dag amend propose .omac/project.yaml \
    --blocked-node bootstrap-console \
    --report-file /tmp/new-dag-review.md \
    --docs docs \
    --resume-issue-id <issue-id> \
    --restart-authoring
  ```

  该动作只适用于处于 confirmation 的 amendment。OMAC 先只读检查是否有
  queued/pending/running/dispatching Run；存在时以 exit 20 失败关闭，绝不 cancel。
  安全时由 Store 对旧 review subject 做 CAS，把当前 deliverable、Reviewer verdict/report/
  ledger、decision 和 machine feedback 引用失效，保留历史评论与附件，然后刷新 issue
  正文、contract/source refs 并在同一 issue 重开 authoring。新 Worker 必须重新执行
  `omac work submit`，随后完整重走 Reviewer 和人工确认。若 Agent Run completed 但没有
  fresh structured submit，命令有界返回 exit 20，不会自动 rerun 或永久轮询。
- accept 在 manifest 写锁内执行 CAS，并原子写入 manifest 定义与逐节点 apply ledger。
  Store 不与文件系统共享事务，因此这不是跨系统原子事务；ledger 以
  `pending/syncing/synced/observed_progress` 记录每个节点的补偿进度。进程崩溃后，重复 accept
  只补偿尚未完成且仍安全的 side effect；已同步或已经继续推进的节点绝不回退。
- ledger 存在 `pending`、`syncing` 或其他非完成状态时，`dag run/tick/reconcile` 在任何
  Store 读取、派发或 merge 前失败关闭，并输出 amendment identity、未完成节点和同一
  `omac dag amend accept <manifest> <amendment-file>` 续接命令。只有所有条目达到
  `synced/observed_progress` 后 Runner 才恢复。
- 完整 manifest digest 变化但 definition digest 未变时，只在最小恢复集合没有变化的前提下
  rebase 自然发生的 status/work_item 进展；任何 contract、边、节点定义或受影响集合漂移都会
  拒绝应用，要求重新生成并评审。
- contract update 是完整替换，并统一经过 canonical manifest serializer；只保留旧 contract
  实际存在的 typed boundary 字段。旧 contract 省略 `consumes` 时必须继续省略，除非 amendment
  明确改变 input policy。若要清除整个 typed boundary，在 update operation 顶层设置
  `clear_contract_boundary: true`，并从 replacement 中省略所有 boundary 字段。
  `acceptance_claims`、`acceptance_contributions`、`acceptance_refs` 会被保留并依据
  `meta.acceptance_file` 指向的权威文档校验。评审后 acceptance 内容漂移会拒绝首次 apply；
  已写入 pending ledger 的崩溃恢复仍优先完成同一 amendment identity，避免半应用死锁。
- 未变节点的 `work_item_id`、status、bounce、PR、verification/review 引用和 merged 事实不动。
  contract-only 且交付证据未变的节点恢复到 review；有效 pass+PR 的 merge-only 节点恢复到
  merging；改变实现 scope 的节点才回 authoring。新增节点从 authoring 开始。merge-only
  accept 不观察或请求 PR merge，后续统一由 `run_merge_delivery` 处理；临时 `UNKNOWN` 不消耗
  merge retry，也不会重复发起 merge。
- done/merged 节点不可修改或删除。已执行节点改变 worker 或 `scope_paths` 必须携带显式
  ownership migration 及理由。
- 对 blocked_by、worker、scope 或其他实现语义变更，OMAC 会计算受影响后继闭包。未启动后继
  继续由依赖自然阻塞；已启动后继进入显式 authoring 恢复集；若影响到 done/merged 后继则失败
  关闭，要求 Orchestrator 增加补偿节点，不能把它们称为 unaffected。

apply 后运行原命令即可衔接：

```bash
omac dag run .omac/project.yaml
```

若 accept 报 manifest definition 已变化或节点证据摘要变化，不要强行应用；基于最新事实重新
propose/review。若已有 amendment issue，可用 `--resume-issue-id` 续接同一条交接时间线。

## Agent 可决策与 Human 决策边界

Controller Agent 可以在不改变目标、contract 和风险接受程度时执行 retry，例如换用更合适的
worker、按现有 blocker 修复，或把过粗节点拆成语义等价的小节点。

以下情况必须先请求 Human：

- 使用 `node accept` 接受未通过验证或已知风险。
- 使用 `node abandon` 放弃用户可见能力，或会使下游验收范围不完整。
- 删除验收 flow、放宽 `non_goals`、降低 coverage/集成门或改变产品范围。
- 两种修复方案会产生不同的兼容性、成本、数据迁移或安全后果。
- 缺少凭证、外部授权或业务决策，Agent 无法从实例事实中确定答案。

请求决策时报告：未决节点、失败事实、已执行命令、受阻下游、可选动作、每个动作的风险，
以及推荐项。不要只说“需要确认”。

## abandoned 语义

`abandon` 是显式决策：该节点不再推进，但上游 abandoned 视为依赖已满足，
不硬依赖其交付物的下游可在下一轮进入就绪节点集合。

- 下游会继续派发，但不会等待 abandoned 节点的交付物。
- 报告会标注经过 abandoned 上游的节点，提醒验收范围可能不完整。
- 若反悔，可用 `omac node retry` 把该节点恢复为 todo。

适用于价值有限、反复失败的可选能力，或上游放弃后仍能独立交付的实验性集成。

## 常见退出原因

- 证据不达标、reviewer reject、CI 或 merge 回退耗尽：节点 blocked 或 needs_decision。
- pass-with-nits 默认回到 worker 处理建议项，不消耗 review bounce，不进入 needs_decision。
- 总控验收超过 `acceptance.max_rounds` 仍有 fail：报告保留未通过项清单。

## 失败隔离

- 失败节点的硬依赖下游自动 blocked，不再派发。
- 独立分支继续推进，不被单点失败拖停。
- Controller Agent 可换 worker、拆成 2–3 个更小节点、降低范围或接受部分失败。

调整示例：

```yaml
# 原节点反复失败
nodes:
  jwt-service:
    worker: frontend-agent
    blocked_by: [oauth-setup]

# 换擅长的 Agent，并按可独立验证边界拆小
nodes:
  jwt-core:
    worker: backend-agent
    blocked_by: [oauth-setup]
  jwt-middleware:
    worker: frontend-agent
    blocked_by: [jwt-core]
```

## 完成条件

- 每个 exit 20 未决节点都有明确决策和理由。
- manifest 通过 `omac dag check`（若有修改）。
- 已重新运行 `omac dag run`，或明确向 Human 报告为什么暂不续跑。
- 汇报完成前核对 manifest；存在非终态节点且没有活跃 `dag run`，就不是完成。

## 禁止事项

- 禁止自动重试。重试必须是显式决策。
- 禁止绕过失败隔离直接推进 blocked 节点。
- 禁止在未读取实例事实和证据链时凭猜测 accept 或 abandon。
- 禁止把 exit 20 报告成成功。
- 禁止为了单次 plan review 继续而修改 `retry.review` 并污染被评审 revision；使用
  `omac plan continue-review`。
