# 验收文档格式

验收文档可以使用 Markdown 正文 + YAML frontmatter。frontmatter 保留机器可校验 schema,
正文写给人和 agent 阅读。

```md
---
schema: omac.acceptance/v1
flows:
  - id: flow-login
    name: 登录流程
    actions:
      - step: 打开登录页
        how: 访问 /login
        expected: 显示登录表单
---

# 验收文档

## flow-login · 登录流程

用户输入有效账号后进入主界面。

### 步骤

1. 打开 `/login`
2. 输入有效账号密码
3. 点击登录

### 预期结果

进入 dashboard,页面展示当前用户信息。
```

## schema 要求

- `flows` 非空。
- `flow.id` 唯一且稳定,供 manifest `contract.acceptance` 引用。
- `flow.name` 非空。
- `actions` 非空。
- 每个 action 必须有 `step/how/expected`。

## 执行可读性

后续执行者可能是低推理预算模型。验收文档必须把用户视角行为写到可执行,
不能依赖执行者自行补全隐含上下文。

- `step` 写用户或系统正在做的动作。
- `how` 写具体入口、命令、页面、参数或测试数据。
- `expected` 写可观察结果和失败判据。
- 边界条件必须有独立 action 或独立 flow,不要藏在正文描述里。

正文不能成为第二套事实源。若正文与 frontmatter 冲突,以 frontmatter 为准。

planner 在 acceptance 阶段通过 `omac work submit <issue-id> --acceptance-file <file>` 交付本文档。
