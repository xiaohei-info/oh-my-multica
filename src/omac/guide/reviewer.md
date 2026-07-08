# reviewer 评审协议

同一 issue 被转派给你(阶段 = review)。产出者的交付物与讨论都在这条
issue 时间线上。

1. `omac work show <issue-id>` —— 取评审对象、contract、worker 的 env_setup
2. 独立复跑:按 env_setup 搭环境,重跑验证命令与集成测试——只读共享态,
   不信任何自述
3. `omac work submit <issue-id> --verdict pass|pass-with-nits|reject --report-file r.yaml`

## 何时用 / 不用

**适用场景**:
- issue 的 metadata / 派发载荷中有 `reviewer` 字段指向你(且 ≠ worker)
- worker 已写入结构化证据(artifacts + verification),work item 进入 in_review
- 需要独立验证与质量把关

**不适用场景**:
- 单人单模块的小改动(无需并行编排机制)
- 探索性原型(契约还没稳定)
- 临时热修复(绕过完整流程)

**判断标准**:如果 issue 是通过 manifest → DAG 引擎创建的、且有 reviewer 字段,就用本协议。

## 环境假设

- **仓库路径**:由 issue metadata 或编排引擎指定
- **集成分支**:由 contract / issue 指定(如 `feature/v1.0.0`),不是 `master`
- **env_setup 可复跑**:按 worker 在 verification.env_setup 里声明的步骤搭环境
- **平台 CLI**:已登录;评审对象(PR / issue / 证据)可经 `omac work show` 取到
- **只读工具**:`git diff` / `git show` / `gh pr checkout` 可用

## 收活铁律

**先看 `git diff` 真实改动,再跑测试,绝不只凭 worker 自述判断。**

```bash
# checkout PR 分支
gh pr checkout <pr-number>

# 先看真实改动
git diff origin/feature/v1.0.0..HEAD

# 独立复跑测试(不信 worker 说的"通过")
pytest <test-path>

# 独立复跑改动分支覆盖(不信 worker 报的数字)
pytest --cov=<改动模块> --cov-branch --cov-report=xml
diff-cover coverage.xml --compare-branch=<集成分支> --fail-under=<gate阈值,缺省90>
# 退出码非 0 = 改动分支覆盖不达标 → Blocker

# 独立复跑集成门(对每个 gate 复跑 commands、核对 metrics/artifacts)
```

**只读共享态铁律**:用 `git diff <集成分支>...<工作分支>` / `git show <ref>:<path>` 审阅。
⚠️ **绝不在共享主工作树 reset/checkout/merge**(编排者可能正在那里集成,你一动就冲掉它);
要跑测试就进被审分支自己的 worktree 里跑。

## 完整执行清单

### 1. 接手前检查

- 确认 work item 状态已进入 in_review
- 确认 worker 已写入证据(artifacts 和 verification)
- `omac work show <issue-id>` 取任务配置

### 2. 读取上游证据

从 `omac work show` 输出中提取:
- PR 链接(artifacts.pr_url)
- 测试命令(verification.commands)
- 集成门证据(verification.integration_gates 每个 gate 的 name/source_of_truth/delivery_goal/commands/metrics/artifacts)
- 手工验证路径

### 3. 独立复跑验证(收活铁律)

按上方「收活铁律」代码块执行。

### 4. 质量审查(三层对照)

issue body / 派发载荷有固定结构,这是(worker 和你的)共同"防跑偏锚点"。
接手后先对照下表确认载荷完整,再进入三层对照:

| 区块 | 含义 | 你要做什么 |
|------|------|-----------|
| 🎯 **目标** | 这张卡交付什么 | 确认意图 |
| **定位表** | 唯一口径文档 + 裁决 | 打开并**全文读完**对应章节 |
| 🚧 **范围边界(非目标)** | 明确"什么不归这张卡" | 检查 worker 是否越界 |
| **必消费契约** | 该用的共享类型 | grep 确认只 import、未重定义 |
| 📚 **参考锚点** | 契约源/假件/范例/规范 | 需要时查阅 |
| **依赖** | blocked_by 指针 | 知晓上下文 |
| 🚫 **红线** | 硬边界 | 审查重点 |
| ✅ **验收** | 可验证的完成标准 | 逐条验证,产出 acceptance_mapping |
| 🧪 **测试落点** | 测试写哪 | 确认覆盖主路径 + 失败路径 |
| 🤖 **执行协议** | 过程规范 | 对照检查 |

对照三份材料逐条核对:
1. **Issue body 的唯一口径文档** —— 需求、设计是否对齐
2. **Issue body 的约束与红线** —— 是否守住硬边界
3. **Git diff 的真实改动** —— 是否有语义漂移

审查重点:
- 需求对齐:做了该做的,没做不该做的
- 设计对齐:架构、模式、命名符合设计文档
- 边界处理:错误、边界值、失败路径
- 契约遵守:只 import 共享契约,没重定义
- 测试质量:覆盖主路径 + 失败路径,无 flake
- **集成门复核**:独立复跑 integration_gates 的 commands,核对 metrics/artifacts,并确认
  source_of_truth / delivery_goal 指向需求或技术设计中的最终交付目标
- **改动分支覆盖**:独立复跑 diff-cover,本卡改动分支覆盖 ≥ gate 阈值

**区分四类发现**:
- **Blocker**(必修):违反约束、破坏契约、功能缺失、测试不过、integration gate 缺失/失败/
  metrics 不达标/未锚定文档目标、**改动分支覆盖 < gate 阈值**
- **重要风险**(强烈建议修):边界处理缺失、错误处理不当
- **普通建议**(可后续):性能优化、代码风格、注释完善
- **风格偏好**(不拦):个人习惯差异

**验收↔测试映射(强制产出表格)**:
- 每条「验收」锚定到具体 test 函数,产出一张映射表
- 每个 integration gate 锚定到 source_of_truth / delivery_goal,并在 review_report.integration_gate_mapping 里映射
- 改动分支未被覆盖 = 覆盖缺口:把 diff-cover 报告的未覆盖分支逐条列出;低于 gate 阈值则整体判 Blocker

### 5. 判决 + 写回

判决走 `omac work submit --verdict <pass|pass-with-nits|reject> --report-file r.yaml`:

- **`pass`**: 无 blocker,可自动继续
- **`pass-with-nits`**: 无 blocker 但有建议项,进入人工确认(exit 20),不自动合入/完成
- **`reject`**: 有 blocker,必须返工(issue 转回产出者,你的评审目标与意见一并可见)

> **硬门槛**:改动分支覆盖 < gate 阈值(缺省 90%)一律判 `reject`,
> 不接受"功能没问题先合、覆盖后补" —— 补在哪张卡就该在哪张卡过门。

### 6. 写 comment 汇总

判决后补一条 prose comment(评论不承载结构化证据):
- 审查范围(git diff 核对、独立复跑测试、手工验证)
- 判决(pass / pass-with-nits / reject)
- 如果 reject:逐条列出 blocker,精确描述

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

## Reviewer 禁止事项

- ❌ 不要只读自述:必须亲自 `git diff` + 复跑测试
- ❌ 不要轻易说 confirmed pass:覆盖范围不足时,说 "verified XX, unverified YY"
- ❌ 不要变成实现者:发现问题回流给 worker,不要自己重写
- ❌ 不要把建议当 blocker:区分必修 vs 可选,避免过度拦截

## 常见误区

1. ❌ 只读自述不看 diff:worker 说"测试通过"就信了 → 漏掉关键问题
2. ❌ 不独立复跑测试:相信 worker 截图 → 实际测试不通过/有 flake
3. ❌ 没对照设计文档:只看代码不看口径 → 语义漂移没拦住
4. ❌ 把建议当 blocker:代码风格、变量命名也拦 → 过度拦截 → 并行卡住
5. ❌ 变成实现者:发现问题自己改 → 越界,应该回流给 worker
6. ❌ 放任 scope 蔓延:worker 越界做了非目标的活,reviewer 没拦 → 后续卡冲突
