# 中文社区发布物料

不同社区不应该机械复制同一篇推广稿。下面保留同一组可核验事实，但调整叙事重点和篇幅。

规则与目标版块于 2026 年 7 月 16 日核对：

- V2EX 分享创造：https://www.v2ex.com/go/create
- LINUX DO 社区准则：https://linux.do/guidelines
- LINUX DO 开源推广公告：https://linux.do/t/topic/1776670

## V2EX：分享创造

### 标题

做了一个把多 Coding Agent 从“并行写代码”推进到“完整交付”的开源工具

### 正文

这段时间我一直在重度使用 Codex、Claude Code 这类 Coding Agent。Agent 多开以后，写代码的
吞吐量确实上去了，但另一个问题反而越来越明显：到底由谁判断整个需求真的完成了？

单个任务看起来都能做。麻烦通常出在任务之间：需求在几轮对话后发生偏移，多个 Agent 修改了
相互冲突的边界，测试结果只存在于作者总结里，Reviewer 沿着作者提供的解释走了一遍，或者一个
长任务超时以后，下一轮只能重新猜前面做到哪里。

所以我做了 oh-my-multica。它不是新的 Coding Agent，而是构建在 Multica 之上的软件交付控制层。

Multica 负责 Workspace、工作项、任务队列、Agent Runtime 和持久化执行记录；oh-my-multica 负责
把需求推进成经过设计、验收定义、动态 DAG 拆解、开发、独立评审、CI、合并和最终验收的软件变更。

这里有一个我比较在意的设计选择：

- 需要推理的地方交给 Agent，包括理解需求、设计、拆解、编码、评审和验收。
- 外层 Loop 交给确定性程序，包括依赖计算、ready nodes、结果收集、证据校验、有界返工、恢复和
  完成判断。

也就是说，DAG 是 Agent 根据当前仓库动态规划的，但“接下来谁能跑、什么证据足够、失败还能重做
几次、什么时候真的结束”不是由一个监督 Agent 根据当前上下文临场发挥。

我又用它完整交付了一个公开的 Webhook Inbox：从一个生产约束目标动态规划出五节点 DAG，最终
合并 5 个 PR，通过 86 个测试，覆盖率 97.18%。第一轮最终验收因为验收源仍启动旧入口，只有
2/11 flows 通过；Loop 没有把项目标记为完成，修正事实来源并从头重跑后才以 11/11 和 exit 0 收敛。

项目早期 v1 基础是在 Multica 上完成的：29 个工作项全部完成，共记录 168 次 Agent 执行，关联
27 个 Pull Request。其中 15 次执行失败、8 次发生重试，最终 26 个 PR 合并，1 个被后续实现替代。
我把失败过程也放进了案例，没有只截一条成功路径。

仓库里还放了一份本地 mock demo，不需要 Multica 账号和模型 Token。它会故意让一个节点失败，
返回 exit 20，然后 retry 同一个 DAG 并最终 4/4 收敛。

GitHub：https://github.com/xiaohei-info/oh-my-multica

中文 README：https://github.com/xiaohei-info/oh-my-multica/blob/main/README.zh-CN.md

真实 demo：https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox

端到端案例：https://github.com/xiaohei-info/oh-my-multica/blob/main/docs/case-studies/webhook-inbox-end-to-end.zh-CN.md

我现在最想听到的不是“支持一下”，而是这几个问题：安装在哪里卡住、哪些概念没说清楚、你的仓库
形态哪里不符合当前假设，以及这套 Loop 在真实任务中是减少监督，还是制造了新的麻烦。

## LINUX DO：开源推广

本项目认可 [LINUX DO](https://linux.do) 为开源项目提供的讨论空间和开源推广规则。发布前必须再次
核对当日规则，并满足以下条件：

- 使用“开源推广”标签和社区要求的声明格式。
- 项目保持完整开源；当前许可证为 MIT。
- 由于本文经过 AI 协助撰写和润色，项目介绍部分必须以截图形式发布，不能直接粘贴 Markdown。
- 不在帖子里引流其他群组或社区。
- 同一账号遵守每周推广频率限制。

### 声明模板

```text
本帖使用社区开源推广，符合推广要求。我申明并遵循社区要求的以下内容：

- 我的帖子已经打上“开源推广”标签：是
- 我的开源项目完整开源，无未开源部分：是
- 我的开源项目已链接认可 LINUX DO 社区：是
- 我帖子内的项目介绍，AI 生成、润色内容部分已截图发出：是
- 以上选择我承诺是永久有效的，接受社区和佬友监督：是
```

项目介绍使用 `docs/assets/linux-do-launch-card.png`，评论区补充 GitHub 链接和真实案例链接。不要把
上面的 V2EX 正文直接复制过去。

## 掘金：技术长文

### 标题

多 Coding Agent 并行以后，谁来判断软件真的交付完成？

### 摘要

Coding Agent 已经能承担大量编码工作，但复杂软件交付不等于多开几个终端。本文从一个包含 29 个
工作项、168 次 Agent 执行和 27 个 Pull Request 的真实项目记录出发，讨论动态 DAG 规划、
确定性 Loop、Harness Engineering、独立评审和最终验收如何组合成一条可恢复的交付链。

### 正文结构

1. **并行写代码以后出现的新瓶颈**：上下文、依赖、证据、评审与完成判断。
2. **为什么选择 Multica 作为基础设施**：它已经解决 Workspace、Runtime、工作项与执行记录。
3. **Agent 动态规划 + 程序确定性推进**：模型负责需要推理的部分，程序负责交付状态机。
4. **Loop Engineering × Harness Engineering**：前馈约束、反馈信号、可恢复状态和停止条件。
5. **真实端到端交付**：五节点 DAG、5 个合并 PR、86 tests、97.18% 覆盖率和 11/11 最终验收。
6. **失败为什么比精选 Demo 更有信息量**：第一轮 2/11、验收源修正、超时和被替代的实现。
7. **模型如何分层配置**：强模型做设计与判断，高性价比模型做大量有边界的执行任务。
8. **它不保证什么**：错误需求和无效测试不会因为有 Loop 就自动变正确。
9. **如何体验**：GitHub Tag 安装、真实 Multica 前置条件与无需账号的 mock demo。

长文可以以中文端到端案例
[`webhook-inbox-end-to-end.zh-CN.md`](../case-studies/webhook-inbox-end-to-end.zh-CN.md)
为事实主体，用早期建设记录补充规模背景，再结合 README 中 Loop Engineering 与 Harness Engineering
的两节。不要把 README 从头到尾重新排版后当作文章。

## 发布节奏

- 先发 Multica 社区，修复第一批安装和理解问题。
- V2EX 与掘金分开发布，至少间隔一天，避免无法及时回复。
- LINUX DO 只有在规则声明、截图素材和项目认可链接都满足时发布。
- 每个平台都记录来源和反馈，不用 Star 数替代真实使用结果。
