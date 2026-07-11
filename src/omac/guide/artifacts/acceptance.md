# acceptance 产物合同

## 使用场景

本合同用于 `acceptance` 产出或评审阶段，把用户视角行为写成可由 worker、reviewer 和最终验收者
逐项执行的权威 flow。

第一步必须运行：

```bash
omac work show <issue-id> --output json
```

以返回的 task、context、authority、guide_refs 和 submit 为当前实例事实。本文是静态 guide，
不得覆盖实例事实、contract、上轮评审或精确提交命令。

## 最小合法示例

提交文件保持为一个可直接解析的单一 YAML mapping；其中的结构化字段是权威事实：

```yaml
---
schema: omac.acceptance/v1
flows:
  - id: flow-login
    name: 用户使用有效凭证登录
    actions:
      - step: 打开登录入口
        how: 访问 /login
        expected: 显示账号和密码输入框
      - step: 提交有效凭证
        how: 输入测试账号并点击登录
        expected: 进入 dashboard，展示当前用户信息
```

## 字段语义

| 字段 | 语义 |
|---|---|
| `schema` | 固定为 `omac.acceptance/v1`。 |
| `flows` | 非空 flow 列表；每个 flow 是一个可独立验收的端到端路径。 |
| `flow.id` | 唯一、稳定的标识，供 manifest `contract.acceptance` 和最终验收结果引用。 |
| `flow.name` | 非空的人类可读名称，说明被验收的用户结果。 |
| `actions` | 非空动作列表，按执行顺序描述 flow。 |
| `step` | 用户或系统正在执行的动作。 |
| `how` | 可复制的入口、命令、页面、参数或测试数据。 |
| `expected` | 可观察结果以及据此判断失败的标准。 |

结构化 YAML 是唯一权威；说明性正文不能成为第二套事实源。当前 submit validator 直接把
提交文件解析为 YAML mapping，因此不要在 YAML 文档后追加无法解析的 Markdown 正文。

后续执行者可能是低推理预算模型。每个 action 必须自包含，不能依赖隐含上下文。
边界条件应写成独立 action 或独立 flow，例如无效输入、重复提交、权限不足、超时和回滚结果。

## 校验硬门

1. 顶层必须是 mapping，`flows` 必须是非空列表。
2. 每个 flow 必须是 object；`id` 和 `name` 必须是非空字符串，且 `id` 不得重复。
3. 每个 flow 的 `actions` 必须是非空列表。
4. 每个 action 必须是 object，且 `step`、`how`、`expected` 都是非空字符串。
5. `flow.id` 必须稳定，并与 design 的 flows、manifest 的 `contract.acceptance` 保持一致。
6. 只写在说明性正文中的成功条件或边界条件不计入可校验验收事实。

## 常见错误 → 修正

| 常见错误 | 修正 |
|---|---|
| `flows` 为空或写成 object | 改成至少包含一个 flow 的列表。 |
| 多个 flow 复用同一个 `id` | 使用稳定且唯一的 id，并同步所有引用。 |
| `how` 写“正常操作” | 写明页面、命令、参数和测试数据，使步骤可复现。 |
| `expected` 写“成功” | 写可观察结果和失败判据。 |
| 把权限不足等情况藏在正文 | 为每个边界条件增加独立 action 或 flow。 |
| 另附正文并描述不同结果 | 删除重复事实或修正结构化 YAML；权威值只保留一份。 |

## 提交

提交前重新读取 `work show`，使用其返回的精确 submit 命令。`acceptance` 产出的常见形状是：

```bash
omac work submit <issue-id> --acceptance-file <file>
```

提交文件必须能被 YAML parser 直接读取；不要在产出阶段提交 verdict。
