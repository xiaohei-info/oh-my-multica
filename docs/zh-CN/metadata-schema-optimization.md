# OMAC Metadata Schema 优化

[English](../metadata-schema-optimization.md) | [简体中文](metadata-schema-optimization.md)

## 背景

OMAC 使用平台 issue metadata 作为 Human、Agent 与确定性流水线之间的持久协调层。实测发现，
如果把长篇自然语言、完整评审报告或命令输出直接写入 metadata，很容易制造噪声并触发平台大小限制。

核心目标不是消灭所有大对象，而是把 metadata 收敛为稳定索引：固定 schema、可控大小、可用于状态判断。
任意 Markdown、自由文本、长报告和不可控输出都应存为附件或 payload comment，再由 metadata 保存引用。

## 核心决策

Metadata 是索引和状态表，不是内容仓库。

允许存储：

- 稳定 ID；
- phase、status 和固定枚举；
- 有界计数器；
- 短小、机器可读的引用；
- schema 与大小都有上界的程序生成摘要。

禁止存储：

- Markdown、设计文档、验收文档和完整交付物；
- 完整 verification 或 review report；
- Agent 自述与 reviewer 自由文本；
- 从用户输入、命令输出或 comment 复制的无界字符串。

长内容必须存为附件，并通过稳定的 `*_ref` 对象引用。

## 字段命名

Multica metadata 是扁平键值结构。新字段必须带 owner 或 phase 语义。

### 兼容字段

继续读取以下已有字段：

| 字段 | 策略 |
|---|---|
| `dag_key`、`kind`、`phase` | 保留，作为稳定任务定位与阶段事实。 |
| `worker`、`reviewer`、`blocked_by` | 保留。 |
| `ci_bounce`、`review_bounce`、`merge_bounce` | 保留有界计数。 |
| `review_verdict` | 保留固定枚举。 |
| `deliverable_ref`、`verification_ref`、`review_report_ref` | 保留稳定 payload 引用。 |
| `decision_required` | 保留，但只能包含有界机器事实和引用。 |
| `contract` | 暂时保留；它是固定 schema，出现大小问题后再迁移。 |

旧的 inline `deliverable`、`verification` 和 `review_report` 只用于读取兼容，不再新增写入。

### 新字段前缀

| 前缀 | Owner | 示例 |
|---|---|---|
| `task_*` | 任务身份 | `task_kind`、`task_phase` |
| `authoring_*` | 产出阶段 | `authoring_deliverable_ref` |
| `verification_*` | worker 证据 | `verification_status`、`verification_command_count` |
| `review_*` | reviewer 阶段 | `review_verdict`、`review_nit_count` |
| `decision_*` | Human 决策门 | `decision_reason`、`decision_report_ref` |
| `ci_*` / `merge_*` | CI 与合并循环 | bounce 与 status |

不要新增没有 phase 前缀和大小约束的 `summary`、`notes`、`report`、`evidence` 或 `result`。

## 引用对象

所有长 payload 使用同一引用结构：

```json
{
  "comment_id": "uuid",
  "attachment_id": "uuid",
  "sha256": "hex",
  "bytes": 1234,
  "filename": "omac-review-report-abcdef.yaml"
}
```

- 存在附件时，`sha256` 与 `bytes` 必填。
- `filename` 用于排障，但消费者不能依赖 hash 前缀长度。
- `comment_id` 与 `attachment_id` 只是平台定位符。
- 下载后应校验 `sha256`。

## 各阶段目标结构

### 创建任务

允许 `dag_key`、`kind`、`phase`、角色、依赖、wave 和可选固定 schema contract。
Issue description 不是 metadata；它可以保存 Human-first 摘要、一个 `work show` 入口和上游链接。

### 普通 authoring submit

计划、验收、manifest 与最终验收结果只写 `phase` 和 `deliverable_ref`。禁止把文件正文写入 metadata。
新写入继续使用附件 comment；旧 issue 的 inline `deliverable` 仍可读取。

### develop authoring submit

允许：

- `artifacts.pr_url`；
- `verification_ref`；
- 命令数、失败数、状态等有界摘要。

禁止完整 verification、命令日志、环境说明和 Agent 自述。读取时优先从 `verification_ref` 下载并解析，
没有引用时才回退旧 inline metadata。

### review submit

允许 verdict、`review_report_ref`、blocker/nit 数量和固定布尔值。禁止完整 report、review goals、mapping
中的自由证据段落、nit issue/fix、blocker 文本和 reviewer prose。读取规则与 verification 相同。

### pass-with-nits

`pass-with-nits` 不是失败门。默认回到 worker 处理非阻塞建议，不增加 review bounce，也不写自然语言
`decision_required`。允许保留 `review_report_ref`，让下一轮 `work show` 读取上轮评审。

未来若需要 Human 决策，`decision_required` 也只能保存 kind、phase、verdict、round、计数和引用。

### retry、CI 与 merge

Reset review 只清 verdict/comment/decision，恢复 `phase=authoring` 并增加有界 bounce。CI 和 merge 只保存
bounce 与固定状态；完整日志、冲突 patch 和命令输出必须放 comment 或附件。

## Store 规则

在 Multica store 建立小型 metadata policy，不把检查散落到 pipeline：

- scalar key 只允许字符串、枚举和计数；
- ref key 必须符合统一引用 schema；
- structured key 必须有固定 schema 与大小上界；
- legacy inline key 只读不写。

读取顺序固定为：

1. 优先 `*_ref` 并加载附件 YAML/JSON；
2. 回退旧 inline metadata；
3. 仅旧数据解析失败时返回 `{"raw": "..."}`，新写入绝不制造 raw metadata。

## 兼容策略

- 继续读取历史 `deliverable`、`verification`、`review_report` 和旧 decision nits。
- inline 与 ref 同时存在时以 ref 为准。
- 不自动清理历史 issue；新写入只使用引用与有界字段。
- 可选历史清理必须是独立维护命令，不混入正常流水线。

## 测试要求

Store 测试验证新写入只有 ref、能够从 YAML/JSON 附件恢复完整对象，并保留旧数据读取能力。
Decision 测试禁止 `nits`、`blockers`、`issue`、`fix`、`summary` 与完整 report。
CLI 端到端测试需要证明长篇评审和 verification 可以提交，但 metadata 只保存引用与短摘要，后续
`work show` 仍能恢复完整上下文。

## 验收标准

- 不再新增 inline `review_report` 或 `verification` metadata；
- pass-with-nits 不写自然语言 decision metadata；
- deliverable 保持 ref-based；
- 历史 inline issue 仍可恢复与续跑；
- `work show`、`dag status`、`plan resume` 和 `node accept` 同时支持新旧数据；
- 全量测试通过，并由一次 live Multica 任务确认 metadata 只含状态字段、引用、计数与固定枚举。

## 非目标

- 不重做整个 issue 模型；
- 不自动迁移历史 issue；
- 不删除 issue description 或 comment；
- 首轮不强制迁移固定 schema contract；
- 不立即重命名所有已有字段。

## 实施顺序

1. 增加 metadata policy 与测试。
2. 停止写 inline review report，并从 ref 恢复。
3. 停止写 inline verification，并从 ref 恢复。
4. 收敛 pass-with-nits decision metadata。
5. 增加禁止字段回归测试。
6. 运行全量测试和一条 live Multica 验证。
