# reviewer 评审协议

同一 issue 被转派给你(阶段 = review)。产出者的交付物与讨论都在这条
issue 时间线上。

1. `omac work show <issue-id>` —— 取评审对象、contract、worker 的 env_setup
2. 独立复跑:按 env_setup 搭环境,重跑验证命令与集成测试——只读共享态,
   不信任何自述
3. `omac work submit <issue-id> --verdict pass|pass-with-nits|reject --report-file r.yaml`

## report 结构

```yaml
review_goals:            # 必填:你评审所依据的目标(验收映射/覆盖率/集成门/设计引用)
  - "acceptance 全覆盖且逐条可验证"
diff_reviewed: true
tests_rerun: true
integration_tests_rerun: true   # contract 有集成门时必填
coverage_checked: true
acceptance_mapping:      # 逐条映射 contract.acceptance
  - { acceptance: "...", evidence: "...", status: pass }
integration_gate_mapping: [ ... ]
blockers: []             # pass 时必须为空
nits: []
```

reject 时 issue 转回产出者,你的评审目标与意见一并可见——
让开发者朝目标修,而不是只修列出的问题。
