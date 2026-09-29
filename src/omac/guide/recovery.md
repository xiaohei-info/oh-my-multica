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

### `pass-with-nits` 接受决策

develop 节点收到 `pass-with-nits` 后会停在 blocked，等待调用者决策；OMAC 不会为未变化的
verification 自动再次派发 Worker。检查 sealed delivery 和 review report 后选择：

```bash
omac node accept-nits <manifest> <node_key>
# 或拒绝建议项并回到 authoring：
omac node retry <manifest> <node_key>
```

`accept-nits` 要求当前 delivery 已 sealed、review subject/report ref 仍匹配，且没有 active
direct Run。它保留 Reviewer verdict/report，只写入有界的
`omac.review-nits-acceptance/v1` marker，清除 caller decision 并恢复 `review/in_review`；下一次
`dag run` 仍会观察远端 merge 事实，不会直接把节点标记 done。命令可安全重复执行。
普通 `node retry` 会清除 marker、使旧 review projection 失效，再回到 authoring 产生新交付。若节点已有 review 回退历史，即使当前 verdict 已被清除，retry 仍记录上一份 delivery 的 PR head 作为 baseline；Worker 必须提交新 head，不能用同一 head 绕过 Reviewer 防线。若旧 head 或交付因果资料缺失，OMAC 不猜测而 fail-closed，不会把仅有新附件的同 head 交付送进 Reviewer。若可读取旧 review report/ledger，retry 会把 report/ledger 引用及有限 blocker 摘要放入 `previous_review`，Worker 必须针对这些 blocker 完成返工；若已知为 reject 但无法恢复任何 report/ledger/blocker 上下文，retry 会以 exit 20 停止，不会再消耗 Worker 轮次。

### amendment 的 `pass-with-nits` 接受

运行中 DAG 的 amendment 若 Reviewer 返回 `pass-with-nits`，OMAC 保留原始
verdict，不把建议项伪装成 `pass`，并停在 confirmation 等待 operator。若进程中断导致
reviewed amendment YAML 尚未生成，使用同一 issue 的 `--resume-issue-id` 物化文件；该路径
只消费 Store 中已封存的交付和 verdict，不重新派发 Worker/Reviewer：

```bash
omac dag amend propose <manifest> \
  --report-file <original-review-report> \
  --docs <persisted-contract-source> \
  --blocked-node <node> \
  --resume-issue-id <amendment-issue-id> \
  --output-file <amendment-file>
```

`--docs` 必须与 issue 持久化 contract 的 `source_of_truth` 一致；不要把
`--report-file` 再作为 `--docs` 传入。OMAC 发现 docs/contract 漂移会 fail-closed，保留原始
verdict，不静默替换评审权威。文件生成后，先显式接受再 apply：

```bash
# 只记录明确的 operator acceptance，不应用 amendment
omac dag amend accept-nits <manifest> <amendment-file>
# 继续官方 apply 路径
omac dag amend accept <manifest> <amendment-file>
```

第一步只在当前 issue、review subject、report ref、ledger ref 和完整 deliverable
仍一致，且没有 active/unknown direct Run 时写入有界的
`omac.amendment-review-nits-acceptance/v1` marker；它不修改 Reviewer verdict、
不写 manifest，也不 apply。任何 subject/report/ledger/deliverable 漂移或运行中的
Run 都 fail-closed。marker 已存在且绑定完全相同的 review 时重复命令安全；不匹配时
拒绝覆盖。只有第二步 `omac dag amend accept` 才会执行正常 amendment CAS/apply 和
逐节点 ledger 补偿；它会再次验证 marker 与 Store 当前事实，不能跳过第一步。普通
develop 节点仍使用独立的 `omac node accept-nits`，两条路径不会互相消费 marker。

### 已应用 amendment 的 contract command 修复

若旧版 manifest load 已把 `${VAR:-}` 运行时占位符写成 `""`，不要重新执行
`amend accept`，也不要猜环境值或手改线上 metadata。使用同一份已 Reviewer pass 且已 applied 的
v2 amendment 原文执行定义级修复：

```bash
omac dag amend repair-contract-commands .omac/project.yaml /tmp/applied.amendment.yaml
```

命令只接受 identity、base digest、apply ledger 均匹配的 amendment，并且只恢复
`verification_commands` 与 `integration_gates[].commands` 中可证明的空默认占位符差异；支持
`add.value.contract` 与带已应用 `description`/`blocked_by` 的 `update.set.contract`，但不重放其余
operations。没有 WorkItem 的新节点只做 definition-only manifest 修复，不会创建 issue；当前 contract 已等于 approved 且没有命令损坏时，repair 也不会为缺失 contract_ref 重新发布 Store 附件。它先读取并锁定 WorkItem 当前运行事实，拒绝 active/unknown Run、平台 assignment、
非目标 contract drift 和无法证明的 Store 状态；Store 附件发布成功但响应丢失时只认一个带 OMAC contract producer marker、绑定同一 issue、文件名 digest
及 bytes 均完整匹配的既有 publication，不会盲目重复发布。每个 Store/manifest 步骤都写入可恢复 receipt；中断后重复
同一命令会继续未完成步骤而不回退 status、phase、bounce、PR、verification 或 review 事实。修复后
命令提交 manifest；push 失败保留本地 commit，按普通 git 同步告警处理。

### 阶段级恢复与 merge 观察

- 恢复以 issue 的真实 `phase` 为准：authoring 只恢复 worker；reviewer run 失败或结束但未提交 verdict 时，保留同一 issue、worker 的 PR/verification 与 review subject，在 `review` 阶段重新派发 reviewer；不得错误退回 worker。
- `merging` 只观察已经持久化的 merge intent/request。GitHub/平台返回 `UNKNOWN` 或临时读取失败时，节点留在 `merging`，不消耗 `retry.merge`、不回退 worker、也不再发送第二次 merge 请求。
- 认证或授权失败不是暂态观察结果：OMAC 会本地持久化 `blocked` 并保留 work item、PR 和 merge marker，不会重新发起合入。修复凭证/权限、远端核实 PR 后，读取节点证据并使用提示的显式恢复命令再运行 DAG。
- 只有明确的 `CLOSED_UNMERGED` 或已知 merge 命令失败才进入 merge 失败/回退语义。恢复期间仍必须以远端 `MERGED + mergedAt` 作为 done 的唯一事实。
- Worker 在 no-submit 回退上限边界成功执行 `omac work submit` 时，Controller 会在耗尽上限前重新读取并封存这次交付（包括 `delivery_identity`）；只有最终权威读取仍证明无新交付时，才保留 no-submit 失败并阻断。真实 no-submit 上限保护不会被绕过。
- 若上一轮竞态已把 WorkItem 和 manifest 留在 `blocked/authoring`，但同一 causal `worker_handoff` 仍在且权威证据显示新交付，下一轮 `dag tick/run` 会先恢复到 collect 路径、封存 delivery，再转入 Reviewer；存在 `decision_required` 或无法证明新交付时仍保持 blocked，不能手改 metadata。
- review convergence 的完整 report/ledger 始终通过 attachment reference 保存；`decision_required` 控制面只写入有界路由字段、审计标量以及各清单的 count/digest 摘要，保留 `review_report_ref`、`review_ledger_ref` 和 `contract_ref`。若有界投影仍无法落入平台 metadata 上限，则 fail-closed，不删事实或强行推进。
- amendment Reviewer Run 若明确终止但没有提交 verdict，`dag amend propose --resume-issue-id` 只在确认没有 active Run 后清除对应的 `reviewer-completed-without-verdict` decision，并重新派发同一 issue 的 Reviewer；其它 decision 不会被清除。
- 节点即使 manifest 投影为 `done + merged + merged_at`，只要残留 `recovery_marker` 就仍属于 active control barrier。OMAC 只有在权威 control read 证明不存在 handoff、review baseline 或 decision 后才清除 marker；读取失败或不可用时保持 fail-closed，不能把真实 recovery 吞成已完成。

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

`commit_manifest` 的 push 失败时，OMAC 先保留本地 manifest commit，并对同一仓库/路径
使用 10 秒起、最多 5 分钟的指数退避；退避窗口内不重复发起 push 或刷屏。远端暂时落后
是显式同步告警，不会伪造已同步状态；恢复后成功 push 会清除退避状态。

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
  Store 仅用于 apply 前 evidence CAS 读取，不写 contract、contract_ref 或任何其他事实；不恢复 Store
  阶段、不派发 Agent、不重放 merge。
- 对无 work item 的未开始节点，省略 `resume_stage` 保持 definition-only；任何显式
  `resume_stage: review|authoring|merging` 都要求已有 work item。对已有 work item，未提供 `resume_stage` 时职责
  迁移仍按旧规则最小恢复到 review；需要覆盖时，在同一个 `update-responsibility` operation 中设置它，绝不能再为同一节点
  增加第二个 `resume` operation。`merging` 要求 Reviewer pass 与 PR：accept 先静默同步新 contract/contract_ref，
  再保持 review verdict、PR/verification、Store status/phase 和人员分配不变；它不派发 Agent，也不观察或请求
  merge，后续 `dag run` 统一负责。historical contract correction 禁止设置 `resume_stage`。
- OMAC 先做 DAG、循环、依赖、agent pool、done/merged 不可变性和 ownership migration
  机器门，再交给独立 Reviewer。若 blocked node 已持有明确的 decision_required，只有
  `review-convergence-*` 或 `contract-boundary-conflict` 才能作为 amendment 准入；
  no-submit、network、metadata 和 Runner 错误必须沿各自恢复路径处理，不能借 amendment 重试。
- Reviewer pass 后命令以 exit 20 停在 `confirmation`，不会自动应用。人工审阅生成的
  amendment 文件后再运行返回的 `omac dag amend accept ...` 命令。新文件使用
  `identity_schema: omac.dag-amendment-identity/v2`，把 base manifest/acceptance digest
  与 review issue 绑定进 amendment identity；旧无该标记的文件按旧 identity 兼容读取，
  下一次重新 propose 会生成 v2。
- amendment 的 review 或 machine guard 预算耗尽不是 confirmation。issue 会停在
  `blocked/review` 并携带 `decision_required`；人工决定继续后，使用原
  `omac dag amend propose ... --resume-issue-id <issue-id>` 命令续接同一 issue、交付与
  Reviewer 历史，不创建新的 amendment issue。
- 普通 `--resume-issue-id` 不会作废合法的 Reviewer-pass confirmation，也不会创建新的
  Agent Run。resume 会先重新读取 Store 当前事实；任何读取失败都会原样失败关闭，并且在失败前不会
  写 contract/metadata、观察 Runtime、assign 或 wake。调用方 snapshot 不参与 refresh 或阶段推进授权。
  只有成功读取的当前事实同时满足 `TODO + authoring + 无 deliverable/deliverable_ref + 无 stopped signal`
  时，才允许幂等刷新 issue body、contract 与 source refs。
  confirmation 只有在 verdict 为 pass/pass-with-nits、subject 仍绑定当前交付且 report/evidence
  重新校验通过时才可消费；否则 exit 20，既不清 confirmation，也不派发 Worker/Reviewer。若 amendment
  authoring Run 已停止但 Store 已有交付物，同样会在任何 Runtime 观察、assign 或 wake 前 exit 20，并要求
  保留旧 issue、通过 `--new-attempt --supersedes-issue-id <old-issue-id>` 创建新 attempt。
  当前没有引擎提供真实原子 conditional restart/dispatch；`--restart-authoring`
  仅保留为兼容入口，并会在任何 issue 读取、写入或 Agent 派发前以 exit 20 失败关闭，
  给出 `--new-attempt` 命令。OMAC 不保留 speculative generation/journal 状态机。
  新 attempt 保留旧 confirmation 作为审计历史：

  ```bash
  omac dag amend propose .omac/project.yaml \
    --blocked-node bootstrap-console \
    --report-file /tmp/new-dag-review.md \
    --docs docs \
    --new-attempt \
    --supersedes-issue-id <old-issue-id>
  ```

  attempt identity 绑定 manifest、report digest、递归 docs 内容 digest、blocked nodes 与 superseded issue。docs
  的逻辑路径固定相对 manifest 所属项目根（manifest 位于 `.omac/` 时取其父目录），不受当前 cwd 影响；项目外
  docs 失败关闭。相同命令在 issue 尚为 `TODO + authoring`、无交付/评审证据且无 active Run 的初始化崩溃场景中，
  会幂等补齐并复用同一 issue。只要 attempt 已派发、进入 review/confirmation/终态，或已有交付与评审证据，
  再次 `--new-attempt` 都会 exit 20，并要求先执行 `omac work show <issue-id> --output json`；需要继续时使用当前阶段
  的普通命令与 `--resume-issue-id`，不能把 `--new-attempt` 当作 resume。不同 report/docs 内容 digest 会产生不同
  attempt。新 issue metadata/source refs
  记录 supersedes、attempt id、report digest 和 docs digest；旧 issue 不会被清理、重开或自动关闭。新 attempt
  正常走 authoring → Reviewer → human confirmation，最终 amendment 仍应用到原 manifest/node。
- `omac dag run`、`dag tick`、`dag amend propose` 与 `dag amend accept` 对同一个真实 manifest
  共用同一把 host-local 写锁。锁在 engine 组装及 Store/Runtime 调用前获取，因此同一台机器上第二个 OMAC
  进程会在任何 issue/Run 副作用前失败关闭。该保证只覆盖通过 OMAC 管理、位于同一主机且指向同一 manifest
  realpath 的操作；它不是分布式锁，也不把 Multica 的 LWW metadata 变成 conditional CAS。其他机器上的 OMAC、
  直接 Multica/API 写入或其他外部参与者若并发修改 issue，属于 unknown/unsupported 边界，OMAC 只能在首次
  dispatch 前通过 active Run 观察与最后一次 pristine Store 重读拒绝已经可见的冲突，不能宣称线性化，也不会
  cancel、清理或补偿无法证明归属的外部事实。
- accept 在 manifest 写锁内执行 CAS，并原子写入 manifest 定义与逐节点 apply ledger。
  Store 不与文件系统共享事务，因此这不是跨系统原子事务；ledger 以
  `pending/syncing/synced/observed_progress` 记录每个节点的补偿进度。进程崩溃后，重复 accept
  只补偿尚未完成且仍安全的 side effect；historical correction 条目从创建起就是
  `synced/store_side_effect:none`，已同步或已经继续推进的节点绝不回退。
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
- 节点恢复到 authoring 时，Store 原子切换新的 `review_generation`，清除上一代当前
  decision/verdict/report/continuation/handoff，但保留历史 review ledger/ref 与绝对 bounce
  审计计数。只有 `review_ledger_generation` 与当前 generation 相同的 ledger 才能生成
  `work show` 的 `review_state` 和 `required_closures`；普通 review reject 不切代。
- 升级前已标记 `synced`、但尚无 generation 投影的 authoring apply 条目，会在重复执行原
  `omac dag amend accept` 命令时进入幂等 repair；不需要手工修改 metadata。repair 仍先检查
  active formal Run，并验证 contract digest、generation、decision/report/subject/handoff 与
  当前 ledger visibility 后才重新标记 `synced`。如果 WorkItem 已封存新的 delivery identity、
  已进入 review，或已切换到其他 generation，重复 accept 只记录 `observed_progress`，不会回滚
  已推进的 Store 事实。
- bounce 字段始终是单调累计的审计值，不清零。Review reject 回到 Worker 时，handoff 还会
  绑定上一轮已评审的 PR head；同一 reject head 的新 Run 不会直接派 Reviewer，而会沿 Worker
  retry/决策路径处理。`pass-with-nits` 与历史未封存的 evidence-only 路径仍允许用新证据附件
  复用同一 head。`work show.task.bounce_budget` 与 Worker retry
  日志同时给出 absolute audit、amendment baseline 和 current-generation consumed；实际预算
  判断仍由 manifest `amendment_apply.bounce_baseline` 的 relative 计算负责。
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
- pass-with-nits 会停在 caller acceptance，不自动派发 Worker；只有调用者显式执行
  `omac node retry` 才会回到 authoring。
- 总控验收超过 `acceptance.max_rounds` 仍有 fail：报告保留未通过项清单。

Review ledger 只记录已提交的语义评审，不记录 provider 过载、网络超时或附件读取等
基础设施重试。`review_convergence_decision` 是唯一收敛权威：同一 blocker 经两次返工仍
保持 `unchanged` 时在第 3 个 cycle 停止；`scope-expanding` 还必须同时满足最近至少两次
连续 blocker 数不再减少、所有 open blocker 都带显式 `owner` 且至少有两个不同 owner。
单纯出现三个责任维度、仍在减少的 blocker 集合或缺失 owner 证据不会准入 amendment；第 5
个 cycle 之后首次出现新的 root cause 也遵守同一 admission 条件；达到第 10 个 cycle 时
无条件停止。OMAC 会在再次派发 Worker 前持久化 `review-convergence-*` 决策。
该决策只要求重新考虑任务边界；develop 节点由 Orchestrator 提出 DAG amendment，OMAC
不会隐藏地重写 DAG。

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

## 历史读取、代际隔离与确定性阻塞

已完成任务的 `work show` 将旧 review ledger 放在 `context.review_history`，标记为
`unverified-history`；原始 rounds 与附件引用保留，不视作当前 convergence 通过证明。
新 writer 使用 `omac.review-ledger/v2` 的 ledger-local 连续轮次。旧 v1 只有通过同样的
严格校验才能继续参与活动评审；不兼容的旧账本必须经显式 authoring 恢复退役，不能重编号。

`node retry --stage review` 要求完整、匹配当前 PR/verification 引用且时间带时区的
controller-sealed delivery identity。缺少身份时使用
`omac node retry <manifest> <node> --stage authoring`，由新 Worker Run 正式提交再封存；
不得补造 identity 或 reviewer Run baseline。amendment 的 review 恢复也在应用前检查。

恢复旧报告须匹配当前 ledger generation、subject 和 report digest；旧代际的报告只保留
审计用途。handoff 携带的反馈绑定目标 generation 和 contract，合同改变后不得继续强制执行。
对于已污染的 Harness，确认 manifest 中的 SDK-only contract 后用 authoring retry 退役旧
handoff，再检查 `work show` 没有旧 Host-positive 的 `previous_review`。

Audit 遇到确定性的上游读取 exit 5，应按 Worker guide 使用 `work block`。修复读取后，
operator 运行 `omac work show <upstream-id>` 核对可读交付，再运行
`omac node retry <manifest> <audit-node> --stage authoring`。绝对历史计数不清零；预算已经
耗尽时，使用现有 reviewed amendment 的显式恢复与 fresh budget 流程，不提高预算上限。

Run 列表读取使用现有有界指数退避，含 TLS handshake timeout/握手超时。证书、认证、
权限和未知错误不重试；重试耗尽仍为 exit 2。修好读取后先核对已有 Run，再以单一受监督
进程运行 `omac dag run <manifest>`；不要因 Runner 退出而直接取消或重派已有 Agent。

### authoring 恢复后的同 HEAD 正式提交

普通 authoring 恢复产生的 `explicit-dispatch` handoff 若没有来源 review verdict/feedback，
旧 verification 附件仅用于区分新提交，不要求 Worker 为相同代码制造新 commit。新的正式
verification 仍必须绑定当前 Worker Run，且提交的 HEAD 必须与远端 PR HEAD 一致；
Controller 校验后才封存并进入 review，不消耗一次“未提交”重试。

来源为 reject 的返工仍必须改变已拒绝的 HEAD；缺少拒绝基线或来源未知的
`operator-retry` 不通过本规则放宽。旧 verification、缺失 HEAD 或不属于当前 Run 的
附件也不能复用。若已存在 operator prerequisite decision，部署此修复不会自动清除它、
归零计数或启动 Runner；保留现有提交和 handoff，由 operator 按已确认的恢复事实处理。

### reject 反馈在 authoring 交接中保留

普通 reject handoff 在清理当前 review 字段前保存有界 blocker 摘要和完整 report/ledger
引用，并绑定当前 contract 与 review generation。`work show.context.previous_review`
从该 handoff 展示反馈；no-submit 重试和中断恢复不会清除这些义务。

如果旧版本已清空报告和 handoff，显式 authoring retry 可从严格校验且属于当前 generation
的 ledger 最后一轮恢复 subject/report digest，再匹配不可变附件。缺失 live subject 不等于
没有评审。不同 generation、无效 ledger 或报告 digest 不匹配仍拒绝恢复，绝不只按评论
时间选最新附件。恢复后的 handoff 保留原 reject verdict 和 subject；真正 reject 的换 HEAD
校验不变。执行恢复前先读取新的 `previous_review`，不要把“之前提交成功”视为已通过评审。

### 修订与任务创建前的身份查找

Multica 的 `find_work_item_by_dag_key` 复用项目范围内的分页 issue envelope，先按精确
`dag_key` 或 `[DAG:key]` title 前缀查找（含创建后尚未写 metadata 的 shell），只对首个
匹配项完整读取和校验附件。无关 issue 的附件不会因身份查找而下载；未找到返回空。
匹配项的坏附件和分页读取错误仍失败关闭，不会把读取失败当成“任务不存在”并重复创建。

Multica issue envelope 的 `duplicate_of: null` 是未标记重复的空默认值，不阻止未派发
amendment shell 的幂等恢复。只有这个字段的 JSON null 被忽略；非空 duplicate 标记、
其他值类型以及任意其他未知字段仍视为持久化活动事实并失败关闭。恢复中断的新 attempt
必须保留原 manifest/report/docs/supersedes 输入，重用同一 deterministic dag_key，不另建重复 issue。

### 连续 amendment 的预算授权

新 amendment 的 `amendment_apply.nodes` 只包含本轮恢复节点。未受影响节点已经完成的
预算授权保留在 `amendment_apply.retained_bounce_baselines`，记录来源 amendment、
work item ID、contract digest 和原始 worker/review/merge baseline；不复制旧恢复步骤，
不会因接受无关 amendment 再次 reset/assign 旧节点。对同一节点接受新的恢复时，以新基线
为准。节点被移除、issue/contract 变化或非法基线不继承旧授权。

预算判断读取 manifest 中的已接受授权，不以 Store 的 bounce_baseline 投影代替授权，
绝对累计计数和配置 limit 均不改动。较早版本已经丢失的基线不会被自动猜回：必须从原已
应用 ledger/Git 历史核验 amendment、完成状态、issue 和 contract，再在单写者控制下只恢复
相应预算记录。不要重放整个旧 apply ledger、提高 limit、清零审计计数或制造合同变更。

### Worker 主动等待决定（结构化阻塞）

当前 develop/authoring Worker 遇到合同规定的失败停止条件或需要 owner
决定时，先重新运行 `omac work show <issue-id> --output json`，复制
`control.blocker_report_template`，通过返回的 `report_blocker` 命令提交
`omac.worker-blocker/v2` 报告。普通 Reviewer reject 仍须正常返工；不能仅因
被 reject 就报告需要决定，也不能将已结束的旧 Run 追溯补报。

- `quality-gate-failed`：evidence 至少一项包含真实执行的 command、非零整数
  exit_code、ref 和 observation。不要为了提交报告继续执行合同已禁止的后续验证。
- `owner-decision-required`：保留 evidence 的 ref/observation，并明确
  decision_needed。contract_ref 必须是当前 context.contract 中实际存在的顶层字段；
  summary 说明该字段与停止条件的关系，不创造新的合同授权。
- 保留模板中的 issue_id、review_context_binding、handoff_generation、worker
  和当前直接 Worker run_id。它们是因果关联，不是身份认证凭证。若 Run 尚未绑定，
  等待 OMAC 绑定后重读 work show；不要拿 session ID 或旧 Run ID 替代。
- ref/observation 是待核验的事实报告。保留原始日志、失败证据及工作树；本地未提交文件
  不代表其他机器可读取或已独立复现。阻塞不要求成功 verification，不产生 pass。

命令须实际返回 exit 20、`terminal: true`、`next_action: stop` 才表示阻塞已记录。
相同报告可幂等重试；不要把未知结果当成功。OMAC 先持久化 decision，再置 blocked，
停止该节点自动重派，保留 bounce、预算、review ledger 和工作文件。已消耗的预算不会
退回；其他独立节点仍可推进。v1 upstream-unreadable 报告保持兼容。

协调者解决决定后使用现有显式 retry 或真实 amendment 流程恢复；部署修复不等于
恢复许可。Worker block/submit/重派共享本机锁并在写入前重读控制事实；这不是跨机器
CAS。保持现有单 Runner 管理约束，不允许多个控制器同时替换同一节点的 generation。

报告最多 2048 UTF-8 JSON 字节，含 1–4 条证据；完整日志留在引用位置，不内嵌。

新建 Worker handoff 时，OMAC 必须先读取标记为 deferred 的 Store contract，
再计算绑定；未下载的 `None` 不能作为真实空合同。若附件读取失败，停止派发，
不保存空合同哈希。已存在的错误绑定不会自动被重写，也不能通过放宽 `work block`
校验恢复；应保留失败证据，由协调者在明确恢复授权后创建新的 handoff。

plan/amendment 等阶段任务在观察到 Agent Run 终态后，会重新读取当前交付或
Reviewer verdict，再判断是否缺少提交；不能用 Run 查询前的旧快照覆盖刚完成的提交。
若终态后的读取失败，则保留现有事实并报错，不写“未提交”决定。plan 的正式提交
由 plan/project-rules 双交付及 review 阶段转换识别，不使用 develop 的 delivery_identity。

`node retry` 不会因为存在当前 explicit-dispatch handoff，就把旧 generation 的
评审 ledger 重新视为有效。历史 review_bounce 保留作审计；仅当前有效评审或
实际携带来源 reject/返工 head 的 handoff 才要求恢复其评审上下文。

### 一次性同 HEAD 新证据送独立评审

`omac node review-evidence` 仅处理已有 `evidence-only-rework-head-policy` 阻塞：
原 reject、同一合同和 HEAD 下，原 completed Worker Run 已提交新的 verification
及不可变发布物。它不是 pass/accept，也不永久放开普通 reject 的换 HEAD 规则。
必须明确批准原始 Agent session 作为历史 handoff/旧 baseline 关联见证；该来源
只补历史关联，不能取代平台 Run、附件上传归属、合同、HEAD 和发布字节的独立读取。

先保存未经编辑的原始 JSONL，并独立固定文件 SHA256；选择其中完整 Issue JSON
`toolResult` 的一基行号。不要重写、裁剪或合成历史行。只读准备请求：

```bash
omac node review-evidence <manifest> <node> \
  --witness-file <original-session.jsonl> --witness-line <line> \
  --witness-sha256 <approved-file-sha256> \
  --reason '<explicit operator authorization>' > evidence-review-request.json
```

准备阶段不会写 Store/manifest 或派发 Agent。它重新下载原/新 verification、
原 reject report/ledger及全部 `retrievable_artifacts`，核对身份和字节。当前版本只支持
PR 同仓库、40位 commit 固定的 GitHub blob URL；不接受分支 URL。附件归属来自附件
task_id 或父 comment.source_task_id，两者冲突即拒绝。新附件必须来自原 completed
Run及同Agent，且在其时间窗内；不能归到前滚的另一个Run或复用历史baseline。

审核完整请求后，在同一控制器串行窗口中消费：

```bash
omac node review-evidence <manifest> <node> \
  --witness-file <same-original-session.jsonl> \
  --apply-request evidence-review-request.json
```

消费时再次独立核验。Controller复用正常封存函数生成identity，caller不得传入identity。
只有这个确切tuple获得同HEAD例外；原reject ledger原字节保留并绑定到已核验的当前合同
世代，使原blocker继续作为独立Reviewer义务。计数、预算baseline与配置limit不变。

授权消费进度记录在 `manifest.meta.evidence_review_authorizations`。中断时使用完全
相同请求与见证续接，不生成另一个请求；每一步只接受期望前态或该步已落地的后态。
已消费请求明确拒绝再次消费。HEAD、合同/世代、附件、发布集、Run集合、控制面或
manifest amendment authority变化均须停止重核。见证SHA只固定字节，不是平台签名。

成功只到 `ready-for-independent-review`，**不派发Reviewer、不标done、不声称pass**。
协调者核对事实后才用既有 `omac dag run <manifest>` 监督正常独立review。该命令会推进
整个DAG；不要与现有Runner/amendment写者并发。manifest锁和Store控制锁是本机
串行化，不是跨机器CAS；仍须保持单控制器。若原始平台历史已截断且没有批准见证，
必须保留fail-closed，不制造源码commit或手写identity来绕过。

当前见证格式还要求该toolResult与session内唯一的原始bash toolCall配对，命令必须是
精确的 `multica issue get <issue-id> --output json`；不接受echo、变换或拼造的JSON。
升级读取comment Run归属时，旧封存identity仅在新增归属恰好等于其原run_id时兼容，
不会重写旧identity，也不会接受冲突Run。
