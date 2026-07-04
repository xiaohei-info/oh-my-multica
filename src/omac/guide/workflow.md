# omac 整体工作流

omac 是确定性 CLI 驱动的多 Agent 并行开发编排:Loop 驱动 Agent,LLM 只做
被派发的、有终点的专家任务(planner / orchestrator / reviewer / worker / acceptor)。

## 标准路径

1. `omac init` —— 一次性配置:选 workspace → 列全量 agent → 角色映射
   → 落盘 `.orchestrator/config.yaml`(体检:`omac init --check`)
2. `omac plan create --name <feature> [--doc 设计文档]` —— 计划 → 验收文档 → 拆解,
   全程内置 review 阶段,产出 `.orchestrator/<feature>.yaml`( + `.acceptance.yaml`)
3. `omac dag run .orchestrator/<feature>.yaml` —— 确定性 loop:
   回收结果 → 计算就绪节点 → 派发,直到收敛;收敛后进入总控验收外层循环
   - exit 0:验收全部 pass,真正可交付
   - exit 20:无法继续推进,stdout 有结构化报告(含可执行的下一步命令)
4. exit 20 后:`omac dag status` 看全景 → `omac node show <key>` 看证据链
   → `omac node retry|abandon` 决策 → 重跑 `omac dag run`(重跑即续跑)

## 关键原则

- issue 的范围 = 一个完整阶段:产出、评审、回退都在同一条 issue 上,交接 = 转派
- 重试是显式决策,不自动发生
- 全部状态在 manifest + 平台,任意中断可恢复,支持跨机器接力
- 前置:runtime 机器需安装 omac(`pipx install omac`)与已登录的平台 CLI

## 执行编排(阶段 C — 跑引擎)

`omac dag run` 是**前台阻塞**进程 —— 内置主循环(回收结果 → 派发 → 轮询)
一直跑到 DAG 终态(全 done 或失败收口)才返回。你必须在当前轮以前台同步方式
把它跑到返回、并收集其退出结果,才算"在监督"。

- ❌ 不要放后台、不要 `&`、不要寄望"未来某轮再看"。本轮 turn 退出 = 本次任务结束,
  没有被收集的后台进程不会替你推进 DAG。
- ❌ 没有活跃的 `omac dag run` 在跑,就**禁止**在回复里说"继续监督/持续观察/等待完成"。
- ✅ 要么前台把 `omac dag run` 跑到返回再汇报;要么明确告诉用户"当前未在监督,要我启动/继续吗"。
- DAG 大、单轮久:仍前台跑,靠 `max-parallel` 控并发、靠日志看进度,而不是退出后口头声称在盯。

### 引擎自动行为(固定逻辑,不可配)

- **就绪节点计算**: `status=todo` 且 `blocked_by` 全 done → 就绪(可派)
- **失败隔离**: 某节点 failed → 其下游标记为 blocked → 不再派发
- **幂等重跑**: manifest 是唯一口径,对同一 manifest 多次执行:
  - 已 done 且有 work_item_id 的节点:精准取,0 新建
  - blocked/failed 的节点:重置为 todo 重试,复用原 work_item_id
  - 无 work_item_id 的节点:首次建 work item 并回填
- 失败处理与中断续跑是同一条路径:改完 manifest 重跑即可

### manifest 唯一口径

manifest 文件是全局唯一口径 —— 不依赖 checkpoint、Run 存储、event log 等自造存储。
节点状态直接写进 manifest 的 `status` 字段,经 git 流转。

关键节点 commit + push:
- 每节点首次建 work item 回填 work_item_id 后 push 一次
- 节点进终态(done/blocked/failed)后 push 一次
- 中间 status 只写本地文件不提交
- push 失败醒目告警但不中断编排

> **git 回写开关(`OMAC_GIT_SYNC`,默认关)**:上述 commit + push 默认关闭 —— 只在本地写 manifest,
> 不碰 git。真实跨机器协作时 `export OMAC_GIT_SYNC=1` 打开。关闭状态下 manifest 仍以本地文件为口径,
> 单机断点续跑不受影响。

### reconcile(跨机器接力)

manifest 经 git 流转,可能是别的机器 commit 来的、带部分状态。Phase 2 启动时做一次
全局 reconcile:逐节点拿 `work_item_id` 去平台核对真实状态 vs manifest 记录,补齐 gap。
manifest 是口径,平台是实况,二者对齐后才往下跑。

## 收尾(Closeout)

> **收尾前置自检(防假监督)**:在汇报"完成/已收尾"前,先核对 manifest —— 若存在非终态节点
> (todo/in_progress/in_review)且当前没有活跃的 `omac dag run` 在前台跑,你**不算在监督**。
> 此时只有两条诚实路径:① 前台再跑 `omac dag run` 推进到终态;② 明确向用户说"尚未收敛、
> 当前未在监督,还剩哪些非终态节点、需要我继续吗"。**禁止**在仍有非终态节点时用"持续监督中/等待完成"收尾。

全 done 或判断可收尾时:
1. **汇总 digest**: 哪些 done、哪些 failed、PR 链接列表、已知问题与限制
2. **向用户汇报**: 交付物(PR 列表 / 集成分支)、验收状态、后续建议

## 派活与合并纪律

### 派活铁律(防跑偏)

派出的 worker 必须:
- 只 import 共享契约、禁重定义
- 守红线/非目标
- 从集成分支切
- PR base 指向集成分支
- 过 CI + 独立评审才允许关闭

**关闭前必须过闸**: worker 的回报只是线索,最终以 reviewer 判决与引擎状态为准,
不要凭 worker 的 prose 总结就放行。

### 合并纪律(避免并发踩踏)

试合并、解冲突、合入都在专用集成 worktree 做(`git worktree add ../integ <集成分支>`),
**绝不在共享主工作树留半成品 merge 态** —— 并发的 reviewer 会读到脏主树、甚至误
`git reset --hard` 冲掉你未提交的工作。

Reviewer 侧对应铁律:只读共享态、不动主树。两边一起守,并发才安全。

## 禁止事项

1. ❌ 不要手工改 issue metadata(编译单向写入,引擎只读)
2. ❌ 不要跳过 lint(manifest 错误会导致引擎行为异常)
3. ❌ 不要在引擎运行中手工改 issue status(会破坏状态机)
4. ❌ 不要混用本机制与 kanban 看板机制(两套并行,按基底择一)
5. ❌ 不要自己实现循环逻辑(引擎是固定的,你只负责拆解)
6. ❌ 不要拆成数百微任务(粒度 = 并行单元,半天~两天可收口)
7. ❌ 不要在没有活跃 `omac dag run` 前台进程时声称"在监督/持续观察/等待完成"(假监督)
8. ❌ 不要把监督寄望于后台进程或"未来某轮" —— turn 退出即任务结束

完整设计:docs/omac-cli-design.md
