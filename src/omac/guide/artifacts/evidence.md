# 证据格式

证据是 omac 左移门和权威门共用的结构化数据。缺项会在 `omac work submit` 当场失败。

## worker verification

```yaml
commands:
  - { cmd: "python3 -m pytest tests/auth", exit_code: 0, summary: "passed" }
integration_gates:
  - name: auth-e2e
    source_of_truth: [docs/design.md#auth-flow]
    delivery_goal: 登录主链路可用
    commands:
      - { cmd: "python3 -m pytest tests/e2e/test_login.py", exit_code: 0 }
    metrics: {}
    artifacts: []
pr_base: feature/login
coverage: 92
env_setup:
  - "docker compose up -d db"
```

## reviewer report

```yaml
review_goals:
  - acceptance 全覆盖且逐条可验证
diff_reviewed: true
tests_rerun: true
integration_tests_rerun: true
coverage_checked: true
acceptance_mapping:
  - { acceptance: "flow-login", evidence: "tests/e2e/test_login.py", status: pass }
integration_gate_mapping: []
blockers: []
nits: []
```

## final acceptance results

```yaml
- id: flow-login
  status: pass
- id: flow-payment
  status: fail
  notes: 支付成功页未展示订单号
```

结果必须逐项覆盖验收文档 flow id。fail 必须有 notes。
