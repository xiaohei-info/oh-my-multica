# 变更日志

本文档记录 **omac** 每个发布版本的显著变更。格式遵循 [Keep a Changelog]，
版本号遵循 [语义化版本]。

[Keep a Changelog]: https://keepachangelog.com/zh-CN/1.0.0/
[语义化版本]: https://semver.org/lang/zh-CN/

## [1.0.0] — 2026-07-05

第一个正式发布版本。omac 从「一个 agent 靠长上下文硬扛」进化为
「契约先行 + manifest DAG + 多 Agent 并行 + 结构化证据 + 独立验收」的
可收敛工程流程；确定性 CLI 程序承载编排循环，LLM 只花在计划 / 拆解 / 开发 /
评审 / 验收等真实智力工作上。

### 新增

- **交付级 e2e 闭环（§7.6 / §10.3）**：plan create 产出（manifest + 验收文档）
  → dag run（含假 CI / merge 脚本）→ 总控验收（fail → 增量 → pass）→ exit 0；
  覆盖 CI 绿链、merge 链、验收外层循环退出 / 退出码链等场景，标记 `e2e`，
  CI 稳定绿。
- **mock 引擎稳定性**：`_auto_complete_check` 只为已注册行为走真实 work submit
  路径，回落通用 DONE-with-deliverable，避免 `plan create` hung；review / verify
  证据路径对 dict-shaped contract（acceptance / decompose）安全兜底。
- **发布物料**：版本升至 `1.0.0`，增加本文档，README 逐条命令核对可跑。

### 修复

- **CI 节点永久 in_progress 死锁**：CI pass 后 `advance_delivery` 把工单倒回
  `IN_PROGRESS`，但循环的「无 reviewer 直进 done」分支未把平台工单同步置 DONE，
  下一轮 reconcile 把 manifest 从 done 拉回 in_progress，collect_results 又不动，
  永久循环。
- **reviewer mock 评审判定不落地**：`pending_review` 里先 `assign_work_item(reviewer)`
  再 `update_status(IN_REVIEW)`，assign 内 `get_work_item` 触发 auto_complete 时工单
  仍是 post-CI 的 `IN_PROGRESS`，走了 deliverable 路径把 assigned 槽位清空，后续
  wake 找不到已派发项；改为先把工单标 `IN_REVIEW` 再派发。
- **conftest mock 延迟归零**：引入 `MOCK_AUTO_COMPLETE_DELAY` 默认 0，在库内
  `main()` 级测试跑得更快，与真实 e2e 子进程路径行为一致。
- **dag.py 验收后 emit 延迟**：验收外层循环并入 fix 节点后，额外一次幂等 tick 再
  emit，让输出 JSON 反映增量后的 done 列表。
- **emit JSON schema**：`--output json` 始终包含 `report` 字段（收敛时为 null），
  消费方无需 try/catch 读字段。

### 变更

- `src/omac/__init__.py` / `pyproject.toml` 版本从 `0.1.0` → `1.0.0`。

## [0.1.0] — 2026-06

初始内部版本，落地 P1–P4 全部分层：

- **P1 – 骨架与可观测性**：命令树、退出码契约、多引擎骨架、mock 引擎、
  `run_task` 循环。
- **P2 — 流水线与并行**：manifest、graph、dispatch、collect_results、CI mock、
  develop authoring / 3-step PR closure、web 服务端 + SPA 面板。
- **P3 — 计划与拆解**：plan create / check / show、reviewer handoff、retry 配置、
  README 文档化。
- **P4 — 验收与闭合**：CI ci_check 监控与有界回退、auto merge、冲突回退、
  acceptance 外层 fix-增量循环、lint / merge increment、交付闭环。
