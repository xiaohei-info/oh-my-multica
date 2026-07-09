# worker 执行协议

worker 负责 `develop` issue:按 contract TDD 开发,交 PR 与结构化 verification。

## 入口

1. `omac work show <issue-id>` 读取 contract。
2. 完成后 `omac work submit <issue-id> --pr-url <PR> --verification-file ev.yaml`。

## 上游 issue 链

`work show` 可能包含「上游 issue」段。先按段内命令逐个运行
`omac work show <上游 issue id>`，再读取对应 issue 的 deliverable/ref/附件内容。
`contract.source_of_truth` 里的 `plan#...`、`acceptance#...` 是章节锚点；先找到
上游 plan / acceptance issue，再在其交付文档中定位这些章节。

不要通过猜附件文件名或全 workspace 搜索来找设计方案。找不到上游内容时，先回到
`work show` 的上游 issue 链和当前 issue body 的 Markdown 链接。

## 执行清单

1. 沿「上游 issue」链读全 `contract.source_of_truth` 指向的设计/验收章节。
2. 确认 `blocked_by` 已完成。
3. 从 `contract.pr_base` 切分支,不要从主干乱切。
4. 先写或定位测试,再实现。
5. 只做 contract 范围内的事,守住 `non_goals`。
6. 跑全量测试、覆盖率和 integration gates。
7. 开 PR,base 指向 `contract.pr_base`;GitHub PR 必须 ready for review,不能是 draft。
8. 提交 verification 文件。

## 返工规则

reviewer reject / pass-with-nits 回到 worker 时,先读取 `work show` 的 `previous_review`。
默认在原 PR 分支上继续提交并复用原 PR URL;不要为同一个 DAG 节点另开平行 PR。
只有原 PR 已关闭、base 不可修复或权限无法 push 时才新建替代 PR,并在新 PR 正文说明替代关系。

## verification

详见 `omac guide artifact evidence`。证据必须覆盖 verification commands、integration gates、
coverage、pr_base 和必要的 env_setup。
GitHub PR 在 `work submit` 时会检查 draft 状态;draft PR 会被拒绝,不会进入 CI/review/merge。

## 禁止事项

- 不自审自放行。
- 不手动改平台状态或分配:禁止执行 `multica issue status`、`multica issue assign`、`multica issue rerun`、`multica issue cancel-task`。状态流转只由 OMAC loop 推进。
- 不跳过测试或伪造验证。
- 不重定义共享契约;只能 import。
- 不顺手重构相邻模块。
