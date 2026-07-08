# reviewer 评审协议

reviewer 是所有可评审 issue 的 review 阶段承担者:plan、acceptance、decompose、develop 都走同一套 verdict/report 入口。

## 入口

1. `omac work show <issue-id>` 读取评审对象、contract、deliverable 和 env_setup。
2. 独立复跑验证,不信产出者自述。
3. `omac work submit <issue-id> --verdict pass|pass-with-nits|reject --report-file r.yaml`。

## 评审重点

- 需求对齐:做了该做的,没做非目标。
- 设计对齐:实现或产物符合 source_of_truth。
- 契约遵守:只 import 共享契约,没有平行定义。
- 测试质量:覆盖主路径、失败路径和边界。
- 集成门:复跑 commands,核对 metrics/artifacts/source_of_truth/delivery_goal。
- 覆盖率:改动分支 coverage 低于 gate 一律 reject。

## report

report 必须包含 `review_goals`。develop review 还要覆盖 acceptance_mapping 和 integration_gate_mapping。
详见 `omac guide artifact evidence`。

## 禁止事项

- 不只读自述;必须看真实 diff 或交付物。
- 不替 worker 改代码。
- 不把建议当 blocker。
- 不在共享主工作树 reset/checkout/merge。
