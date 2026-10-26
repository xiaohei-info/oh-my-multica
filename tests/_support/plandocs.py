"""Fixtures for the plan pipeline tests: a design, an acceptance document and manifests that pass lint."""

PLAN_TEXT = """\
# 演示设计方案

目标:实现 demo 特性,包含 login 与 dashboard 两个节点。
步骤:
1. 搭 login
2. 搭 dashboard
"""

PROJECT_RULES_TEXT = """\
## OMAC 项目级开发规范

- 保持现有登录与权限错误语义向后兼容。
- 新行为必须先写测试，并运行完整测试集。
"""

ACCEPTANCE_YAML = """\
schema: omac.acceptance/v2
flows:
  - id: flow-login
    name: 登录流程
    actions:
      - id: ACT-LOGIN-01
        kind: business-action
        step: 打开登录页
        how: GET /login
        expected: 返回 200 与登录表单
      - id: ACT-LOGIN-02
        kind: business-action
        step: 提交合法凭证
        how: POST /login {user, pwd}
        expected: 跳转到首页
  - id: flow-dashboard
    name: 仪表盘流程
    actions:
      - id: ACT-DASHBOARD-01
        kind: business-action
        step: 访问仪表盘
        how: GET /dash
        expected: 显示数据卡片
"""

GOOD_MANIFEST = """\
meta:
  name: demo-create
nodes:
  - id: login
    worker: alice
    description: Implement the login flow and its observable session behavior.
    contract:
      objective: 实现登录
      acceptance_claims:
        - flow-login
      acceptance_contributions:
        - flow_id: flow-login
          action_ids: [ACT-LOGIN-01, ACT-LOGIN-02]
      acceptance_refs: [flow-login]
      source_of_truth: ["docs/login.md"]
      non_goals: ["不改 dashboard"]
      verification_commands: ["pytest tests/login"]
      integration_gates:
        - name: login-gate
          layer: L1
          delivery_goal: 登录交付
          source_of_truth: ["docs/login.md"]
          covers: ["route-login"]
          acceptance_refs: ["flow-login"]
          commands: ["pytest tests/int/login"]
      pr_base: feature/demo
      scope_paths: [src/login/**]
      evidence_mode: fixture
      produces: [{artifact_id: login-component}]
      consumes: []
  - id: dashboard
    worker: bob
    description: Implement the dashboard flow and its observable data behavior.
    blocked_by: [login]
    contract:
      objective: 实现仪表盘
      acceptance_claims:
        - flow-dashboard
      acceptance_contributions:
        - flow_id: flow-dashboard
          action_ids: [ACT-DASHBOARD-01]
      acceptance_refs: [flow-dashboard]
      source_of_truth: ["docs/dash.md"]
      non_goals: ["不改 login"]
      verification_commands: ["pytest tests/dash"]
      integration_gates:
        - name: dash-gate
          layer: L1
          delivery_goal: 仪表盘交付
          source_of_truth: ["docs/dash.md"]
          covers: ["route-dash"]
          acceptance_refs: ["flow-dashboard"]
          commands: ["pytest tests/int/dash"]
      pr_base: feature/demo
      scope_paths: [src/dashboard/**]
      evidence_mode: fixture
      produces: [{artifact_id: dashboard-component}]
      consumes: []
"""

BAD_MANIFEST_LINT = """\
meta:
  name: bad-create
nodes:
  - id: login
    worker: ghost
    blocked_by: [missing]
    contract:
      objective: ""
      acceptance: []
      non_goals: []
      verification_commands: []
      integration_gates: []
      pr_base: ""
"""

CLEAN_MANIFEST = """\
meta:
  name: demo
nodes:
  - id: a
    worker: alice
    contract:
      objective: 实现 a
      acceptance: ["a 可运行"]
      source_of_truth: ["docs/a.md"]
      non_goals: ["不碰 b"]
      verification_commands: ["pytest tests/a"]
      integration_gates:
        - name: a-gate
          layer: L1
          delivery_goal: a 交付
          source_of_truth: ["docs/a.md"]
          covers: ["route-a"]
          acceptance_refs: ["a 可运行"]
          commands: ["pytest tests/int/a"]
      pr_base: feature/v1
  - id: b
    worker: bob
    reviewer: alice
    blocked_by: [a]
    contract:
      objective: 实现 b
      acceptance: ["b 可运行"]
      source_of_truth: ["docs/b.md"]
      non_goals: ["不碰 a"]
      verification_commands: ["pytest tests/b"]
      integration_gates:
        - name: b-gate
          layer: L1
          delivery_goal: b 交付
          source_of_truth: ["docs/b.md"]
          covers: ["route-b"]
          acceptance_refs: ["b 可运行"]
          commands: ["pytest tests/int/b"]
      pr_base: feature/v1
"""

BAD_MANIFEST = """\
meta:
  name: bad
nodes:
  - id: a
    worker: ghost
    blocked_by: [missing]
    contract:
      objective: ""
      acceptance: []
      non_goals: []
      verification_commands: []
      integration_gates: []
      pr_base: ""
"""
