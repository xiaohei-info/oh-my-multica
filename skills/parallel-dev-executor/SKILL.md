---
name: parallel-dev-executor
description: Worker/Reviewer 在并行开发机制中的执行协议——从 metadata 读配置、TDD 实现、独立复跑测试、产 gateable PR、写 metadata 证据，支持多种协作引擎
---

# 并行开发执行协议 (Worker/Reviewer)

当你被编排机制派发任务（work item metadata 含 `worker`/`reviewer` 字段）时加载本 skill。

## 你要解决什么问题

并行开发中，worker 和 reviewer 各自面对不同的风险：

**Worker 面对的陷阱**：
1. **边写边偏**：任务一长，写着写着就忘了约束、偏离了设计文档
2. **自审盲区**：自己写的代码，自己看不出问题
3. **交付模糊**：没有客观证据，只凭"我觉得可以"

**Reviewer 面对的陷阱**：
1. **轻信自述**：只看 worker 说"测试通过"，不亲自验证
2. **放任漂移**：没对照设计文档与约束，语义偏差没拦住
3. **变成实现者**：发现问题后自己重写，越界了

本协议用**结构化执行清单 + 客观证据链 + 收活铁律**同时解决这些问题。

## 何时用 / 不用

**适用场景**：
- Issue metadata 中有 `worker` 或 `reviewer` 字段指向你
- 属于并行开发编排机制派发的任务
- 有明确的唯一口径文档（issue body 中指明）
- 需要独立验证与质量把关

**不适用场景**：
- 单人单模块的小改动（无需并行编排机制）
- 探索性原型（契约还没稳定）
- 临时热修复（绕过完整流程）

**判断标准**：如果 issue 是通过 manifest → DAG 引擎创建的，就用本协议；否则按常规流程。

## 核心信念与设计哲学

1. **跑偏不能靠"提醒"治，要靠"结构"治**  
   光说"记得看文档"不可靠；让你在每步都被锚定（issue body 结构 + 执行清单），才可靠。

2. **"完成"必须有客观证据**  
   不是"我觉得没问题"，而是：测试全绿 + PR 链接 + 验证命令 + 独立复跑。

3. **收活必须看真实改动，不能只凭自述**  
   Reviewer 第一件事：`git diff`看真实改动 + 独立复跑测试，绝不只凭 worker 的 prose 总结判断。

4. **质量门不是实现者自审**  
   Worker 不能自己判"可以合并"；必须经过独立 reviewer 验证 + 判决。

5. **失败时坦诚标 blocked，不要硬撑**  
   做不了 / 发现问题就标 `blocked` + 说明原因，回流给编排器或上游，不要伪装成"完成"。

## 角色识别

从 issue metadata 读 `worker` / `reviewer` 字段判断你的角色：
- `metadata.worker == 你的 agent 名` → 你是 **Worker**（实现者）
- `metadata.reviewer == 你的 agent 名` → 你是 **Reviewer**（独立验证 + 质量审查）
- 你的 agent `role == "architect"` → 你可能是 **Architect**（架构设计与评审）

一个 issue 可以同时有 worker 和 reviewer；也可以只有 worker（无 reviewer = 自动通过质量门）。

**Architect 特殊职责**：
- 当 issue 涉及架构设计（如共享契约、模块边界、设计模式）时，architect 作为 worker
- 当需要整体架构评审时，architect 作为特殊 reviewer，关注架构层面而非实现细节

## Worker 完整执行清单（8 步）

当你是 worker 时，严格按以下顺序执行：

### 1. 认领前检查
```bash
# 读任务配置（worker/reviewer/blocked_by/contract），实时查询，不信列表快照
python3 scripts/agent_cli.py read-task <issue-id>
# blocked_by 非空且依赖未完成 → 不开工
```
- 认领并标记进行中由编排器/runner 派发时完成（worker 被派发即处于 in_progress），无需手动改态。

### 2. 读全唯一口径
- 打开 issue body 中的 **🎯 目标** 与 **定位表** 找到唯一口径文档
- **全文读完**该文档对应章节，不要只看标题
- 读 **🚧 范围边界（非目标）**，明确什么不归这张卡
- 读 **🚫 红线**，记住该模块最易踩的硬边界
- 读 **必消费契约**，明确该用哪些共享类型（禁止重定义）

### 3. 按需拆解（可选）
- 任务小：直接做
- 任务大：拆 2–5 个 sub-issue，使用引擎的子任务功能
- 拆解后，按 sub-issue 顺序逐个完成

### 4. 切分支
```bash
# 从集成分支切（不是默认主分支！）
git fetch origin
git checkout -b <prefix>/<issue-key>-<slug> origin/<integration-branch>
# integration-branch 见 issue metadata 或项目约定
```
**⚠️ 关键**：base 必须是 issue 中指定的集成分支（如 `feature/v1.0.0`），不是 `master`。

### 5. TDD 实现
- **测试先行**：先写（或找到）对应测试用例，红灯
- **实现**：只 import 共享契约，守红线与非目标
- **验证**：测试全绿，手工验证关键路径
- **分支覆盖**：用 `--cov-branch` 跑，**本卡改动的每个分支（含失败旁路：错误处理、边界返回、early-return）都要有测试**，不是只测 happy path。改动分支覆盖必须达到 gate 阈值（缺省 90%）。

**完成判定铁律**：必须装全依赖 + 跑**全量测试套件**（不只跑本模块），绝不只跑子集。
真实教训：worker 在新 worktree 只装本模块依赖、只跑本模块用例，缺依赖致跨模块测试被静默跳过，误判"全绿完成"。

**改动分支覆盖自测**（转 in_review 前必过）：
```bash
pytest --cov=<改动模块> --cov-branch --cov-report=xml   # 跑全量套件 + 分支覆盖
diff-cover coverage.xml --compare-branch=<集成分支> --fail-under=90
# 退出码非 0 = 改动分支覆盖不达标 → 补测试，不得转 in_review
```

### 6. 验收自查
对照 issue body 的 **✅ 验收** 清单，逐条勾完：
- [ ] 测试全绿（运行测试命令，截图或复制输出）
- [ ] **改动分支覆盖 ≥ gate 阈值**（`diff-cover` 输出，复制数字与未覆盖行清单）
- [ ] 手工验证关键路径（按 issue 指定的验证点）
- [ ] 守住红线（没碰禁区）
- [ ] 没越界（非目标没做）
- [ ] 契约正确（只 import，没重定义）

### 7. 提交与写证据
```bash
# 提交 + 推送
git add . && git commit -m "feat(module): <简短描述> (#<issue-number>)"
git push origin <branch-name>

# 开 PR（base = 集成分支）
# 使用 gh/hub 或其他工具，确保 base 指向集成分支
```
**写证据 + 自校验 + 标 done（唯一入口，禁止手敲 `metadata set`）**：

```bash
python3 scripts/agent_cli.py submit-worker <issue-id> \
    --pr-url <PR链接> --branch <分支> --commit <sha> \
    --command 'pytest tests/x --cov=... --cov-branch::0::N passed' \
    --command 'diff-cover coverage.xml --compare-branch=<集成分支> --fail-under=90::0' \
    --coverage <diff-cover 数字> --pr-base <集成分支> \
    --integration-gates-file gates.json    # contract 有 integration_gates 时必填
```

- `submit-worker` 把 `--pr-url/--command/--coverage/...` 组装成**唯一 JSON 口径**的 `artifacts` + `verification`（不产 dotted key、不产 prose），并用 **runner 同一套 validator** 在提交前自校验。
- `verification` 必须覆盖 `contract.verification_commands` 与 `contract.integration_gates`：单测/覆盖率命令逐条 `--command 'cmd::exit_code::summary'`；集成门证据写进 `--integration-gates-file`（每个 gate 的 name/commands/metrics/artifacts/source_of_truth/delivery_goal）。
- 证据不全 → 当场被证据门拦截、非零退出、打印缺项，**不写入、不转状态**；补齐再来，别把缺口甩给 reviewer/harvest。
- 通过 → 写证据并把 work item 标 `done`，交 runner harvest 收割（有 reviewer 则由 runner 指派 reviewer 并转 in_review）。

### 8. 写 comment（叙述，不承载证据）
- `submit-worker` 已写结构化证据并标 `done`。这里只补一条 prose comment 汇总（PR 链接、验证结果、手工验证路径、已知限制）。
- 评论是给人读的叙述，**结构化证据一律走 `submit-worker`**，不要在评论里塞 metadata。
- 状态转换交给 runner：worker 标 `done` 即可，指派 reviewer + 转 `in_review` 是 runner harvest 的职责，worker 不自行指派。

### Worker 禁止事项

❌ **不要自审自放行**：不要自己改成 `done`，质量门交给 reviewer  
❌ **不要跳过测试**：验证必须客观，不能伪造  
❌ **改动分支覆盖不达标不得转 in_review**：`diff-cover` 退出码非 0（低于 gate 阈值）就补测试，别把缺口甩给 reviewer  
❌ **不要强行开工**：blocked_by 非空时必须等依赖完成  
❌ **不要越界**：非目标明确说了不做的，就真的不做  
❌ **不要顺手重构**：只做卡内范围，相邻模块的问题留给它自己的卡

## Reviewer 完整执行清单

当你是 reviewer 时，按以下顺序执行：

### 1. 接手前检查
- 确认 work item 状态已进入 `in_review`
- 确认 worker 已写入证据（`artifacts` 和 `verification` metadata）
- 读任务配置走 `python3 scripts/agent_cli.py read-task <issue-id>`（归一化吐出 worker/reviewer/contract）

### 2. 读取上游证据
- 读上游证据走 `python3 scripts/agent_cli.py read-evidence <issue-id>`（归一化吐出 artifacts/verification/review_*）
- 从中提取 PR 链接、测试命令、集成门证据、验证路径
- 找到 PR URL
- 找到 `verification.commands` 中的单测/覆盖率命令
- 找到 `verification.integration_gates` 中每个 gate 的 `name`、`source_of_truth`、`delivery_goal`、`commands`、`metrics`、`artifacts`
- 找到手工验证路径

### 3. 独立复跑验证（收活铁律）

**铁律：先看 `git diff` 真实改动，再跑测试，绝不只凭 worker 自述判断。**

```bash
# checkout PR 分支
gh pr checkout <pr-number>

# 先看真实改动
git diff origin/feature/v1.0.0..HEAD

# 独立复跑测试（不信 worker 说的"通过"）
pytest <test-path>  # 或 issue 指定的测试命令

# 独立复跑改动分支覆盖（不信 worker 报的数字）
pytest --cov=<改动模块> --cov-branch --cov-report=xml
diff-cover coverage.xml --compare-branch=<集成分支> --fail-under=<gate阈值,缺省90>
# 退出码非 0 = 改动分支覆盖不达标 → Blocker，判 blocked

# 独立复跑集成门（不信 worker 报的数字）
# 对 contract.integration_gates / verification.integration_gates 中每个 gate:
# 1. 先读 source_of_truth / delivery_goal，确认 gate 锚定的是需求/技术设计中的交付目标
# 2. 逐条复跑 gate.commands
# 3. 核对 gate.metrics 是否满足 required_metrics
# 4. 核对 gate.artifacts 是否存在且足以诊断失败
# 5. 在 review_report.integration_gate_mapping 中写回 gate、source_of_truth、delivery_goal、commands、metrics、artifacts、status
# 缺 gate、命令失败、metrics 不达标、artifact 缺失、或未锚定文档目标 = Blocker，判 blocked

# 手工验证关键路径
# 按 issue 验收清单 + worker 的 verification 路径 + integration gate 的 delivery_goal，亲自走一遍
```

**为什么必须看 diff**：worker 的 prose 总结可能漏掉关键改动、或美化实际情况；只有 diff 是真实的。

**只读共享态铁律**：用 `git diff <集成分支>...<工作分支>` / `git show <ref>:<path>` 审阅。
⚠️ **绝不在共享主工作树 reset/checkout/merge**（编排者可能正在那里集成，你一动就冲掉它）；要跑测试就进被审分支自己的 worktree 里跑。

#### Superpowers 增强（如果可用）

当你有 Superpowers skill 可用时，可以使用专门的 code-reviewer agent 进行深度审查：

**使用场景**：
- 复杂的跨模块改动
- 关键的共享契约变更
- 高风险的架构调整

**调用方式**：
```bash
# 使用 requesting-code-review skill 派发给 code-reviewer agent
# 传递以下上下文：
# 1. 唯一口径文档路径与章节
# 2. 必消费契约列表（只读共享态）
# 3. 红线与非目标
# 4. PR 链接与 diff 范围
# 5. 验收清单

# code-reviewer agent 会：
# - 对照设计文档逐条核对
# - 检查契约遵守情况
# - 验证边界处理
# - 产出详细审查报告
```

**Superpowers Skill 映射表**：
| 任务类型 | 推荐 Skill | 用途 |
|---------|-----------|------|
| 代码深度审查 | `requesting-code-review` + `code-reviewer` | 对照设计文档的详细审查 |
| 测试覆盖分析 | `test-coverage-analyzer` | 验收↔测试映射检查 |
| 契约一致性检查 | `contract-validator` | 检测重定义与越界 import |
| 架构漂移检测 | `architecture-drift-detector` | 对比设计与实现的偏差 |

**集成要求**：
- Superpowers 审查结果作为 reviewer 判决的**输入**，不是替代
- 最终判决仍由你（reviewer）综合决定
- Superpowers 发现的问题要合并进你的 comment

### 4. 质量审查（三层对照）

对照三份材料逐条核对：
1. **Issue body 的唯一口径文档** — 需求、设计是否对齐
2. **Issue body 的约束与红线** — 是否守住硬边界
3. **Git diff 的真实改动** — 是否有语义漂移

审查重点：
- 需求对齐：做了该做的，没做不该做的
- 设计对齐：架构、模式、命名符合设计文档
- 边界处理：错误、边界值、失败路径
- 契约遵守：只 import 共享契约，没重定义
- 测试质量：覆盖主路径 + 失败路径，无 flake
- **集成门复核**：独立复跑 `integration_gates` 的 commands，核对 metrics / artifacts，并确认 `source_of_truth` / `delivery_goal` 指向需求或技术设计中的最终交付目标，而不是实现细节
- **改动分支覆盖**：独立复跑 `diff-cover`，本卡改动分支覆盖 ≥ gate 阈值

**区分四类发现**：
- **Blocker**（必修）：违反约束、破坏契约、功能缺失、测试不过、integration gate 缺失/失败/metrics 不达标/未锚定文档目标、**改动分支覆盖 < gate 阈值**
- **重要风险**（强烈建议修）：边界处理缺失、错误处理不当
- **普通建议**（可后续）：性能优化、代码风格、注释完善
- **风格偏好**（不拦）：个人习惯差异

#### 契约存在性核对
- 对 issue body 的「必消费契约」清单，逐个 grep 确认契约文件**已冻结**（存在且可 import）
- 区分"该 import 却自造"（违规，必须 blocker）与"契约尚未定义的合理本地占位"（放行，但提 known_issues）

#### 验收↔测试映射（强制产出表格）
- 每条「验收」锚定到具体 test 函数，产出一张映射表
- 每个 integration gate 锚定到 `source_of_truth` / `delivery_goal`，并在 `review_report.integration_gate_mapping` 里映射到复跑命令、metrics、artifacts 和证据
- 无对应 test / gate mapping = 覆盖缺口，必须在 comment 中标出
- **改动分支未被覆盖 = 覆盖缺口**：把 `diff-cover` 报告的未覆盖分支逐条列出；低于 gate 阈值则整体判 Blocker

### 5. 判决 + 写回

判决一律走 `submit-review`（唯一入口，禁止手敲 `metadata set`）。`review_report` 是结构化 JSON 文件（见 [Metadata 契约](#metadata-契约与编排引擎的接口)）。

> **硬门槛**：改动分支覆盖 < gate 阈值（缺省 90%）一律判 `blocked`，不接受"功能没问题先合、覆盖后补"——补在哪张卡就该在哪张卡过门。

- **`pass` / `pass-with-nits`**：无 blocker，可合并
  ```bash
  python3 scripts/agent_cli.py submit-review <issue-id> \
      --verdict pass --report-file review_report.json
  # submit-review 用 runner 同一套 validator 校验 review_report：
  # 报告不全（缺 acceptance/integration 映射、blockers 非空、未独立复跑）当场拒绝、非零退出。
  # 通过 → 写 review_verdict + review_report，并标 done。
  ```

- **`blocked`**：有 blocker，必须返工
  ```bash
  python3 scripts/agent_cli.py submit-review <issue-id> --verdict blocked \
      --report-file review_report.json    # report 可省；blocker 详情走下方 comment
  # 写 review_verdict=blocked 并失败隔离（转 blocked），回流给编排器/worker
  ```
  返工要点另写一条 prose comment（见第 6 步），评论不承载结构化证据。

### 6. 写 comment 汇总

```bash
# 使用引擎 CLI 添加评论
🔍 评审完成

审查范围：
- git diff 核对：<xx 文件>
- 独立复跑测试：<结果>
- 手工验证：<路径>

判决：<pass | blocked | pass-with-nits>

<如果 pass>
✅ Confirmed pass
- 需求对齐 ✓
- 设计对齐 ✓
- 测试覆盖 ✓

<如果 blocked>
❌ Blockers：
1. <精确描述>
2. ...

<如果 pass-with-nits>
✅ Pass，建议跟进：
- <nits>
"
```

### Reviewer 禁止事项

❌ **不要只读自述**：必须亲自 `git diff` + 复跑测试  
❌ **不要轻易说 confirmed pass**：覆盖范围不足时，说 "verified XX, unverified YY"  
❌ **不要变成实现者**：发现问题回流给 worker，不要自己重写  
❌ **不要把建议当 blocker**：区分必修 vs 可选，避免过度拦截

---

## Architect 执行清单（架构师专用）

当你的 agent role 是 `architect` 且被分配架构相关任务时：

### 作为 Worker（架构设计任务）

**适用场景**：
- Wave 0 共享契约设计
- 跨模块接口定义
- 架构模式选型
- 技术栈决策

**执行流程**：
1. **读全设计文档**：理解系统整体架构意图
2. **识别关键决策点**：
   - 模块边界在哪？
   - 数据流向如何？
   - 依赖方向是否合理？
   - 有哪些跨模块契约？
3. **产出架构制品**：
   - 共享契约代码（DTO/事件/枚举/错误）
   - 架构决策记录（ADR）
   - 模块依赖图
   - 接口规范文档
4. **验收标准**：
   - 契约代码可被 import
   - 契约不变量测试已写
   - 架构决策已文档化
   - 模块边界清晰可验证

### 作为 Reviewer（整体架构评审）

**适用场景**：
- Wave 2 集成后的整体架构评审
- 跨模块重构的架构一致性检查
- 关键架构约束的遵守情况审查

**评审重点**（架构层面，不是实现细节）：

#### 1. 模块边界清晰度
```bash
# 检查模块间依赖
find . -name "*.py" -exec grep -l "from.*import" {} \; | sort
# 验证：是否有越界 import？
```

#### 2. 契约遵守情况
```bash
# 检查是否有重定义契约
grep -r "class.*DTO" --include="*.py" | grep -v "shared/contracts"
# 验证：业务模块是否自己定义了应该 import 的契约？
```

#### 3. 依赖方向合理性
- 是否有循环依赖？
- 底层是否依赖上层？
- 共享契约是否被底层依赖？

#### 4. 设计模式一致性
- 错误处理模式是否统一？
- 数据访问模式是否一致？
- API 设计风格是否统一？

#### 5. 架构漂移检测
对照设计文档检查：
- 模块职责是否偏移？
- 新增的跨模块调用是否合理？
- 是否引入了设计之外的依赖？

**判决输出**：
```bash
# 使用引擎 CLI 添加评论
🏗️ 架构评审完成

### 模块边界
✅ 边界清晰，无越界 import
❌ 发现 module-A 直接依赖 module-B 内部实现（应通过契约）

### 契约遵守
✅ 所有 DTO 均从 shared/contracts import
✅ 无重定义契约

### 依赖方向
❌ 发现循环依赖：service-A ↔ service-B
建议：引入事件解耦

### 设计模式
⚠️  错误处理不统一：部分用异常，部分用 Result 类型
建议：统一为异常处理模式

### 架构漂移
✅ 无明显漂移

### 判决：BLOCKED
必修项：
1. 解除 service-A ↔ service-B 循环依赖
2. 修复 module-A 对 module-B 的直接依赖

建议项：
- 统一错误处理模式（可后续优化）
"

# 写入判决（结构化证据走 agent_cli，禁止手敲 metadata set）
python3 scripts/agent_cli.py submit-review <issue-id> \
    --verdict <pass|blocked> --report-file review_report.json
```

### Architect 禁止事项

❌ **不要陷入实现细节**：你关注架构层面，不是变量命名或算法优化  
❌ **不要自己重写代码**：发现问题标出来，回流给对应 worker  
❌ **不要过度设计**：架构服务于需求，不要为了"优雅"牺牲简单性  
❌ **不要脱离设计文档**：架构评审要对照设计文档，不是凭感觉

---

## Issue Body 识别（你的护栏）

并行编排的 issue body 有固定结构，这是你的"防跑偏锚点"：

| 区块 | 含义 | 你要做什么 |
|------|------|-----------|
| 🎯 **目标** | 这张卡交付什么 | 读完，理解意图 |
| **定位表** | 唯一口径文档 + 裁决 | 打开并**全文读完**对应章节 |
| 🚧 **范围边界（非目标）** | 明确"什么不归这张卡" | 记住，别越界 |
| **必消费契约** | 该用的共享类型 | 只 import 这些，禁止重定义 |
| 📚 **参考锚点** | 契约源/假件/范例/规范 | 需要时查阅 |
| **依赖** | blocked-by 指针 | Worker：确认已完成；Reviewer：知晓上下文 |
| 🚫 **红线** | 硬边界 | **最易踩，最显著，必守** |
| ✅ **验收** | 可验证的完成标准 | Worker：逐条勾；Reviewer：逐条验 |
| 🧪 **测试落点** | 测试写哪 | Worker：按指定位置写测试 |
| 🤖 **执行协议** | 过程规范 | 照做（分支基线、拆分时机） |

**关键**：约束、红线、非目标在 body 最前面 = 最显著 = 最容易被遵守。

## 常见误区清单（对照自查）

### Worker 常见误区
1. ❌ **跳过 Wave 0 地基**：开始实现前没确认契约已冻结 → 自己发明接口 → 集成时对不上
2. ❌ **没读完唯一口径文档**：只看 issue title 就开始写 → 写着写着就偏了
3. ❌ **顺手越界**：看到相邻模块的问题顺手改了 → scope 蔓延 → 后续卡冲突
4. ❌ **自审自放行**：自己觉得没问题就改 `done` → 绕过质量门 → 问题漏到集成
5. ❌ **伪造验证**：测试没通过，截图旧的或编个"应该可以" → 后续炸裂
6. ❌ **PR base 搞错**：打到 `master` 而不是集成分支 → 污染主干

### Reviewer 常见误区
1. ❌ **只读自述不看 diff**：worker 说"测试通过"就信了 → 漏掉关键问题
2. ❌ **不独立复跑测试**：相信 worker 截图 → 实际测试不通过/有 flake
3. ❌ **没对照设计文档**：只看代码不看口径 → 语义漂移没拦住
4. ❌ **把建议当 blocker**：代码风格、变量命名这种也拦 → 过度拦截 → 并行卡住
5. ❌ **变成实现者**：发现问题自己改 → 越界了，应该回流给 worker
6. ❌ **放任 scope 蔓延**：worker 越界做了非目标的活，reviewer 没拦 → 后续卡冲突

## Metadata 契约（与编排引擎的接口）

> **唯一写入口径**：下面的 `artifacts`/`verification`/`review_verdict`/`review_report` 一律由
> `scripts/agent_cli.py`（`submit-worker`/`submit-review`）写入——它负责把零散入参组装成这套
> 结构化 JSON、用 runner 同一套 validator 自校验、并把状态一起转好。**禁止手敲
> `<engine-cli> metadata set`**：手敲会冒出 dotted key（`artifacts.pr_url`）/ prose / JSON 三套
> 口径，runner 读不到就误判 blocked、甚至失败隔离整条 DAG。下面的 schema 是 agent_cli 的产物口径，
> 给你理解字段含义、以及组织 `--integration-gates-file` / `--report-file` 用。

**编排引擎写入**（你只读）：
- `worker`: worker agent 名
- `reviewer`: reviewer agent 名（可空 = 无 reviewer）
- `blocked_by`: 依赖 issue id 列表（JSON 数组字符串）
- `gate`: 自定义验收条件（可选，缺省 = "测试全绿 + 无 flake + **本卡改动分支覆盖 ≥ 90%**"）
  - **改动分支覆盖（diff branch coverage）是硬门槛**：只卡本卡新写/改动的分支，不卡整仓总分（避免被历史遗留代码稀释、避免靠测无关老代码刷分）。
  - 度量口径：`pytest --cov=<改动模块> --cov-branch --cov-report=xml` → `diff-cover coverage.xml --compare-branch=<集成分支> --fail-under=90`。
  - 节点可经 manifest `gate` 覆盖阈值（升/降需在 gate 文本写明理由）。

**Worker 写入**（Reviewer 和编排引擎读取）：
```json
{
  "artifacts": {
    "pr_url": "https://github.com/org/repo/pull/123",
    "branch": "agent/issue-slug",
    "commit": "abc123"
  },
  "verification": {
    "commands": [
      {
        "cmd": "pytest tests/user_api --cov=app.user --cov-branch --cov-report=xml",
        "exit_code": 0,
        "summary": "42 passed"
      },
      {
        "cmd": "diff-cover coverage.xml --compare-branch=feature/v1 --fail-under=90",
        "exit_code": 0,
        "coverage": 93
      }
    ],
    "integration_gates": [
      {
        "name": "user-api-contract",
        "source_of_truth": ["docs/design.md#user-api"],
        "delivery_goal": "User API returns documented envelopes and problem+json errors",
        "covers": ["route_contract"],
        "commands": [
          {
            "cmd": "pytest tests/integration/user_api",
            "exit_code": 0,
            "summary": "8 passed"
          }
        ],
        "metrics": {"route_contract_coverage": 100},
        "artifacts": ["coverage.xml"]
      }
    ],
    "pr_base": "feature/v1",
    "ci_status": "passed",
    "coverage": 93
  }
}
```

**Reviewer 写入**（编排引擎读取）：
```json
{
  "review_verdict": "pass-with-nits",
  "review_report": {
    "diff_reviewed": true,
    "tests_rerun": true,
    "integration_tests_rerun": true,
    "coverage_checked": true,
    "acceptance_mapping": [
      {
        "acceptance": "GET /users/:id returns 404 for missing users",
        "evidence": "tests/test_user_api.py::test_missing_user_404",
        "status": "pass"
      }
    ],
    "integration_gate_mapping": [
      {
        "gate": "user-api-contract",
        "source_of_truth": ["docs/design.md#user-api"],
        "delivery_goal": "User API returns documented envelopes and problem+json errors",
        "evidence": "tests/integration/test_user_api.py::test_frontend_backend_contract",
        "commands": [
          {"cmd": "pytest tests/integration/user_api", "exit_code": 0}
        ],
        "metrics": {"route_contract_coverage": 100},
        "artifacts": ["coverage.xml"],
        "status": "pass"
      }
    ],
    "blockers": [],
    "nits": ["Rename local helper in follow-up"]
  }
}
```

**不要发明第二套字段**。PR 链接统一放 `artifacts.pr_url`；单测/覆盖率命令结果统一放 `verification.commands`；集成门证据统一放 `verification.integration_gates`；验收映射统一放 `review_report.acceptance_mapping`；集成门映射统一放 `review_report.integration_gate_mapping`。

## 环境假设

- **仓库路径**：由 issue metadata 或编排引擎指定（通常是项目根目录）
- **集成分支**：由 issue metadata 指定（如 `feature/v1.0.0`），不是 `master`
- **Python 环境**：本机 python3 + pytest（或 issue 指定的测试框架）
- **引擎 CLI**：所选协作引擎的客户端已配置（先跑 `<engine-cli> --help` 确认可用命令）
- **agent_cli**：`scripts/agent_cli.py` 是读/写证据的唯一入口，与 orchestration skill 共用引擎层（依赖 `parallel-dev-orchestration` 同仓同级安装）。引擎类型/工作空间走 `--engine`/`--workspace` 或 `.env`/环境变量，与 run_dag 一致。
- **Git CLI**：`git` 可用；提 PR 用 `gh` 或项目约定的工具

## 失败处理

### Worker 失败
- 做不了 / 卡住 / 发现依赖有问题：
  ```bash
  python3 scripts/agent_cli.py block <issue-id> --reason "<坦诚说明原因+卡点>"
  # block 写一条 ❌ Blocked 评论并把 work item 标 blocked，回流给编排器
  ```
- 不要硬撑到 `in_review`，坦诚标 `blocked` 回流给编排器

### Reviewer blocked
- 发现 blocker：
  ```bash
  python3 scripts/agent_cli.py submit-review <issue-id> --verdict blocked \
      --report-file review_report.json
  # 写 review_verdict=blocked 并失败隔离；blocker 详情另写 prose comment
  ```
- 不要自己改，回流给 worker

---

## Dispatch Prompt 机制（编排引擎注入）

当编排引擎派发任务时，会注入以下上下文到 worker/reviewer 的 system prompt 或 issue comment：

### Worker Dispatch Prompt

```markdown
🎯 你被派发为 worker 执行此任务

**任务信息**：
- Issue ID: <issue-id>
- Issue Key: <issue-key>
- 任务标题: <title>
- 集成分支: <integration-branch>

**关键约束（必读）**：
1. **只读共享态**：契约、入口、映射表位于 `<共享契约路径>`，只 import，禁止修改或重定义
2. **守红线**：见 issue body 🚫 红线部分
3. **非目标边界**：见 issue body 🚧 范围边界部分
4. **唯一口径文档**：`<文档路径>` §<章节号>（全文读完对应章节）
5. **PR base**：必须指向 `<integration-branch>`，不是 master

**执行协议**：
参照 `parallel-dev-executor` skill 的 Worker 8 步清单

**完成标准**：
- 测试全绿（全量测试套件，不只本模块）
- **改动分支覆盖 ≥ gate 阈值（缺省 90%）**：`diff-cover coverage.xml --compare-branch=<集成分支> --fail-under=90` 退出码 0
- **集成测试全绿**：contract.integration_gates 每个 gate 都有 `verification.integration_gates[]` 证据，且锚定 source_of_truth / delivery_goal
- Worker 必须按 integration gate 的 source_of_truth / delivery_goal 生成证据，不得把实现细节当成交付目标
- PR 已产出并指向正确 base
- 经 `agent_cli submit-worker` 写入 `artifacts` + `verification`（含 commands/integration_gates/pr_base/coverage）并通过自校验
- work item 标 `done`（指派 reviewer + 转 `in_review` 由 runner harvest 完成）

**如遇阻塞**：
`agent_cli block <id> --reason ...` 标 `blocked` + 说明原因，不要硬撑
```

### Reviewer Dispatch Prompt

```markdown
🔍 你被派发为 reviewer 评审此任务

**任务信息**：
- Issue ID: <issue-id>
- Worker: <worker-agent-name>
- PR: <pr-url>（从 metadata.artifacts 读取）

**关键约束（必读）**：
1. **收活铁律**：先 `git diff <base>...<head>` 看真实改动，再跑测试，绝不只凭 worker 自述
1a. **集成门铁律**：逐个复跑 `verification.integration_gates` 的 commands，核对 metrics/artifacts，并按 source_of_truth / delivery_goal 判断是否覆盖真实交付目标
2. **只读共享态**：契约、入口、映射表位于 `<共享契约路径>`，审查时确认 worker 只 import 未重定义
3. **对照三份材料**：
   - Issue body 的唯一口径文档: `<文档路径>` §<章节号>
   - Issue body 的约束与红线
   - Git diff 的真实改动
4. **独立复跑测试**：不信截图，亲自 checkout 分支跑测试
5. **独立复跑改动分支覆盖**：亲自跑 `diff-cover`，不信 worker 报的数字；< gate 阈值 = Blocker

**评审重点**：
- 需求对齐：做了该做的，没做不该做的
- 设计对齐：架构、模式、命名符合设计文档
- 边界处理：错误、边界值、失败路径
- 契约遵守：只 import 共享契约，没重定义
- 测试质量：覆盖主路径 + 失败路径
- 改动分支覆盖：≥ gate 阈值（缺省 90%），未覆盖分支逐条列出

**判决输出**：
- `pass`: 无 blocker（含改动分支覆盖达标）→ 改 status 为 `done`
- `blocked`: 有 blocker → comment 详细问题 + 保持 `in_review`
- `pass-with-nits`: 可合并但有建议 → `review_report.nits`

**执行协议**：
参照 `parallel-dev-executor` skill 的 Reviewer 6 步清单

**Superpowers 增强**（如果可用）：
使用 `requesting-code-review` + `code-reviewer` agent 进行深度审查，传递上述"只读共享态/入口/映射表"要求
```

### Architect Dispatch Prompt

```markdown
🏗️ 你被派发为 architect 执行架构任务/评审

**任务信息**：
- Issue ID: <issue-id>
- 任务类型: <架构设计 | 架构评审>

**架构设计任务（作为 worker）**：
- 产出：共享契约代码 + 架构决策记录 + 模块依赖图
- 关注：模块边界、数据流向、依赖方向、跨模块契约
- 验收：契约可 import + 不变量测试已写 + 决策已文档化

**架构评审任务（作为 reviewer）**：
- 评审范围：模块边界清晰度、契约遵守、依赖方向、设计模式一致性、架构漂移
- 不关注：实现细节、变量命名、算法优化
- 判决：必修项（架构问题）vs 建议项（优化方向）

**执行协议**：
参照 `parallel-dev-executor` skill 的 Architect 执行清单
```

**注意**：这些 dispatch prompt 由编排引擎在派发时动态生成，不写入 issue body，而是注入到派发消息或 system prompt 中。

---

**一句话总结**：Worker 对着清单执行 + 留客观证据；Reviewer 先看 diff 真实改动 + 独立复跑 + 对照设计把关。两者配合，让"跑偏"在物理上做不到、或当场被拦下。
