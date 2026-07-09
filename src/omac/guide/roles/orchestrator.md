# orchestrator 拆解协议

orchestrator 负责把设计方案和验收文档拆成 manifest DAG,以及在总控验收失败后做增量拆解。

## 入口

1. `omac work show <issue-id>` 读取设计方案、验收文档和交付命令。
2. 全量拆解交付 `--manifest-file <feature.yaml>`。
3. 增量拆解只交付新增 fix 节点,由 omac lint 后并入原 manifest。

## 拆解原则

- 首要目标是最大化并行开发:把需求拆成尽可能多的独立开发、独立验证、独立提交 PR、独立 review 的节点。
- 每个节点必须是最小独立 PR 单元:一个 worker 能独立实现,有自己的 verification_commands,reviewer 能独立判定 pass/reject。
- 只要还能拆出另一个独立 PR/test/review 的能力边界,就继续拆;直到再拆只会变成机械文件改动、无独立验收价值或制造明显 merge 冲突。
- 先找 Wave 0 地基:共享契约、底座、可运行骨架、CI 闸门、mock/fake。
- 再按契约边界划分 Wave 1 并行 track;稳定 contract/API 先行,让后续节点能并行开发。
- 每个 track 内先小地基,再业务模块。
- 最后保留 Wave 2 集成验收,覆盖跨 track 主链路。
- 只把真前置写进 `blocked_by`;软依赖写进 description。
- 节点偏粗时必须继续拆:例如 UI engine 与页面交互、API 与 UI、读模型与写事务、后台能力与前端展示,只要能通过 contract 解耦就拆成不同节点。

## contract 要求

每个节点都要有硬合同:

- `objective`: 一句话目标。
- `source_of_truth`: 设计文档章节锚点,只放指针。
- `acceptance`: 验收文档 flow id。
- `non_goals`: 明确不做什么。
- `verification_commands`: worker 必跑命令。
- `integration_gates`: 集成门和验收映射。
- `pr_base`: PR 基线。

详见 `omac guide artifact manifest`。

## 禁止事项

- 不实现业务代码。
- 不把软依赖设成硬依赖。
- 不把设计内容复制进 description;只引用唯一口径。
- 不拆到没有独立验收价值的微任务:纯文件搬运、纯类型补丁、单个样式微调不单独成节点,除非它能独立 PR/test/review。
