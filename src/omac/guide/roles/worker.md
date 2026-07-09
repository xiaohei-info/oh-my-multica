# worker 执行协议

worker 负责 `develop` issue:按 contract TDD 开发,交 PR 与结构化 verification。

## 入口

1. `omac work show <issue-id>` 读取 contract。
2. 完成后 `omac work submit <issue-id> --pr-url <PR> --verification-file ev.yaml`。

## 执行清单

1. 读全 `contract.source_of_truth` 指向的设计章节。
2. 确认 `blocked_by` 已完成。
3. 从 `contract.pr_base` 切分支,不要从主干乱切。
4. 先写或定位测试,再实现。
5. 只做 contract 范围内的事,守住 `non_goals`。
6. 跑全量测试、覆盖率和 integration gates。
7. 开 PR,base 指向 `contract.pr_base`;GitHub PR 必须 ready for review,不能是 draft。
8. 提交 verification 文件。

## verification

详见 `omac guide artifact evidence`。证据必须覆盖 verification commands、integration gates、
coverage、pr_base 和必要的 env_setup。
GitHub PR 在 `work submit` 时会检查 draft 状态;draft PR 会被拒绝,不会进入 CI/review/merge。

## 禁止事项

- 不自审自放行。
- 不跳过测试或伪造验证。
- 不重定义共享契约;只能 import。
- 不顺手重构相邻模块。
