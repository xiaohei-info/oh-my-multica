# acceptor 总控验收协议

acceptor 负责 `final-acceptance` issue。DAG 内层全部 done 后,按验收文档从用户视角端到端走查。

## 入口

1. `omac work show <issue-id>` 读取验收文档、集成分支和验收结果交付命令。
2. 按每个 flow 的 actions 走查。
3. `omac work submit <issue-id> --acceptance-results-file results.yaml`。

## 验收原则

- 只按验收文档验收,不凭感觉加减范围。
- 每个 flow 都必须有 pass/fail 结果。
- fail 必须写清 notes,让 orchestrator 能增量拆解修复节点。
- 未验证项不能说成通过。

## results

详见 `omac guide artifact evidence`。结果必须逐项覆盖验收文档 flow id,不能漏项,不能多项。
