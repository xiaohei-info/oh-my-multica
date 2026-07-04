# worker 执行协议

你被 assign 了一个 develop issue。永远只需要两个命令:

1. `omac work show <issue-id>` —— 取 contract 全量(objective/acceptance/
   non_goals/验证命令/pr_base/coverage_gate)与本协议
2. 完成后 `omac work submit <issue-id> --pr-url <PR> --verification-file ev.yaml`

## 何时用 / 不用

**适用场景**:
- issue 的 metadata / 派发载荷中有 `worker` 字段指向你
- 属于并行开发编排机制派发的任务
- 有明确的唯一口径文档(issue body 中指明)
- 需要独立验证与质量把关

**不适用场景**:
- 单人单模块的小改动(无需并行编排机制)
- 探索性原型(契约还没稳定)
- 临时热修复(绕过完整流程)

**判断标准**:如果 issue 是通过 manifest → DAG 引擎创建的,就用本协议;否则按常规流程。

## 环境假设

- **仓库路径**:由 issue metadata 或编排引擎指定(通常是项目根目录)
- **集成分支**:由 contract / issue 指定(如 `feature/v1.0.0`),不是 `master`
- **Python / 测试**:本机 `python3` + `pytest`(或 issue 指定的测试框架)
- **平台 CLI**:已登录;`omac` 已安装(`pipx install omac`)
- **Git / PR**:`git` 可用,提 PR 用 `gh` 或项目约定的工具
- **共享契约路径**:在 issue body「必消费契约」清单中列出,只 import 不重定义

## 铁律

- **契约先行**:只消费共享契约,不平行重定义
- **TDD**:测试与实现同步;完成必须有证据,不接受自述
- **PR base 指向 contract.pr_base**(集成分支),不直接打主干
- **non_goals 是红线**,越界即 reject
- **完成判定铁律**:必须装全依赖 + 跑**全量测试套件**(不只跑本模块),绝不只跑子集

## 完整执行清单(8 步)

### 1. 认领前检查

`omac work show <issue-id>` 取任务配置。`blocked_by` 非空且依赖未完成 → 不开工。
认领并标记进行中由编排器派发时完成(被派发即处于 in_progress),无需手动改态。

### 2. 读全唯一口径

issue body / 派发载荷有固定结构,这是你的"防跑偏锚点"。逐区块读取:

| 区块 | 含义 | 你要做什么 |
|------|------|-----------|
| 🎯 **目标** | 这张卡交付什么 | 读完,理解意图 |
| **定位表** | 唯一口径文档 + 裁决 | 打开并**全文读完**对应章节 |
| 🚧 **范围边界(非目标)** | 明确"什么不归这张卡" | 记住,别越界 |
| **必消费契约** | 该用的共享类型 | 只 import 这些,禁重定义 |
| 📚 **参考锚点** | 契约源/假件/范例/规范 | 需要时查阅 |
| **依赖** | blocked_by 指针 | 确认已完成,否则不开工 |
| 🚫 **红线** | 硬边界 | **最易踩,最显著,必守** |
| ✅ **验收** | 可验证的完成标准 | 逐条勾,逐条留证据 |
| 🧪 **测试落点** | 测试写哪 | 按指定位置写测试 |
| 🤖 **执行协议** | 过程规范 | 照做(分支基线、拆分时机) |

> 关键:约束、红线、非目标在 body 最前面 = 最显著 = 最容易被遵守。

### 3. 按需拆解(可选)

- 任务小:直接做
- 任务大:拆 2–5 个子任务,按顺序逐个完成

### 4. 切分支

```bash
git fetch origin
git checkout -b <prefix>/<issue-key>-<slug> origin/<integration-branch>
```

**⚠️ 关键**:base 必须是 contract 中指定的集成分支(如 `feature/v1.0.0`),不是 `master`。

### 5. TDD 实现

- **测试先行**:先写(或找到)对应测试用例,红灯
- **实现**:只 import 共享契约,守红线与非目标
- **验证**:测试全绿,手工验证关键路径
- **分支覆盖**:用 `--cov-branch` 跑,**本卡改动的每个分支(含失败旁路:错误处理、边界返回、early-return)都要有测试**,不是只测 happy path

**改动分支覆盖自测**(转 in_review 前必过):
```bash
pytest --cov=<改动模块> --cov-branch --cov-report=xml   # 跑全量套件 + 分支覆盖
diff-cover coverage.xml --compare-branch=<集成分支> --fail-under=90
# 退出码非 0 = 改动分支覆盖不达标 → 补测试,不得转 in_review
```

### 6. 验收自查

对照 issue body 的验收清单,逐条勾完:
- [ ] 测试全绿(运行测试命令)
- [ ] **改动分支覆盖 ≥ gate 阈值**(`diff-cover` 输出)
- [ ] 手工验证关键路径
- [ ] 守住红线(没碰禁区)
- [ ] 没越界(非目标没做)
- [ ] 契约正确(只 import,没重定义)

### 7. 提交与写证据

```bash
git add . && git commit -m "feat(module): <简短描述> (#<issue-number>)"
git push origin <branch-name>
# 开 PR(base = 集成分支)
```

写证据走 `omac work submit <issue-id> --pr-url <PR> --verification-file ev.yaml`:

- `verification` 必须覆盖 `contract.verification_commands` 与 `contract.integration_gates`:
  单测/覆盖率命令逐条;集成门证据每个 gate 的 name/commands/metrics/artifacts/source_of_truth/delivery_goal
- 证据不全 → 当场被证据门拦截、exit 5、打印缺项,**不写入、不转状态**;补齐再来
- 通过 → 写证据并把 work item 标 done,交引擎回收(有 reviewer 则指派 reviewer 并转 in_review)

### 8. 写 comment(叙述,不承载证据)

`omac work submit` 已写结构化证据并标 done。这里只补一条 prose comment 汇总
(PR 链接、验证结果、手工验证路径、已知限制)。

状态转换交给引擎:worker 标 done 即可,指派 reviewer + 转 in_review 是引擎回收的职责。

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

## Worker 禁止事项

- ❌ 不要自审自放行:不要自己改成 done,质量门交给 reviewer
- ❌ 不要跳过测试:验证必须客观,不能伪造
- ❌ 改动分支覆盖不达标不得转 in_review:`diff-cover` 退出码非 0 就补测试
- ❌ 不要强行开工:`blocked_by` 非空时必须等依赖完成
- ❌ 不要越界:非目标明确说了不做的,就真的不做
- ❌ 不要顺手重构:只做卡内范围,相邻模块的问题留给它自己的卡

## 常见误区

1. ❌ 跳过 Wave 0 地基:开始实现前没确认契约已冻结 → 自己发明接口
2. ❌ 没读完唯一口径文档:只看 issue title 就开始写 → 写着写着就偏了
3. ❌ 顺手越界:看到相邻模块的问题顺手改了 → scope 蔓延 → 后续卡冲突
4. ❌ 自审自放行:自己觉得没问题就改 done → 绕过质量门
5. ❌ 伪造验证:测试没通过,截图旧的或编个"应该可以"
6. ❌ PR base 搞错:打到 `master` 而不是集成分支 → 污染主干

## 失败处理

做不了 / 卡住 / 发现依赖有问题:`omac work submit` 不会帮你标 blocked ——
坦诚在 issue 评论里说明原因 + 卡点,回流给编排器。不要硬撑到 in_review。
