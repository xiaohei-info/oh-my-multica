# worker 执行协议

你被 assign 了一个 develop issue。永远只需要两个命令:

1. `omac work show <issue-id>` —— 取 contract 全量(objective/acceptance/
   non_goals/验证命令/pr_base/coverage_gate)与本协议
2. 完成后 `omac work submit <issue-id> --pr-url <PR> --verification-file ev.yaml`

## 铁律

- 契约先行:只消费共享契约,不平行重定义
- TDD:测试与实现同步;完成必须有证据,不接受自述
- PR base 指向 contract.pr_base(集成分支),不直接打主干
- non_goals 是红线,越界即 reject

## 证据(verification-file)

```yaml
commands:            # 必须覆盖 contract.verification_commands,exit_code 全 0
  - { cmd: "...", exit_code: 0, summary: "..." }
integration_gates:   # 逐项覆盖 contract.integration_gates(commands/metrics/artifacts)
pr_base: feature/v1  # 必须等于 contract.pr_base
coverage: 92         # 必须 ≥ coverage_gate
env_setup:           # contract 声明集成门/env 依赖时必填:环境构建步骤,
  - "docker compose up -d db"       # reviewer 照做即可复跑
```

submit 时左移校验:缺什么当场打回(exit 5)并精确告知。
CI 失败 / merge 冲突会把同一 issue 转回给你,错误上下文在评论里。
