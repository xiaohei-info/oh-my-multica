---
name: software-design-demand-survey-methodology
description: "Use when an architect must start at the demand-research stage, turn a business problem into a researchable architecture problem, and preserve the full lifecycle-stage doctrine for what to ask, what to answer, and what the business-research document must contain."
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [architect, lifecycle, demand-survey, requirements, research]
    related_skills: [arch-lifecycle-delivery, arch-lifecycle-solution-design-methodology, writing-skills]
---

# Demand Survey Methodology

## Overview

This is the `architect` profile's **需求调研阶段方法论 skill**.

It is the correct entry point when the architect must start from the original business problem rather than from an already-approved design package.

## When to Use

Use when:
- the business problem is still vague
- there is no reliable demand-research document yet
- the architect needs to clarify current state, target state, benefits, risks, red lines, and ROI before solution design starts

## Canonical Stage Doctrine (Full Preservation)

# 一、需求调研

**时间**：需求或者问题发生并提出时

**输入**：业务方&各参与方问题、需求与痛点

**输出**：业务需求调研文档

**过程**：

需求承接过程中，另外一个目的是**帮业务方解决问题而不仅仅是回答问题**，多聊几句多往前走几步，了解其核心诉求、前因后果后进行全局规划。一些无脑需求需要让需求方梳理清楚**现状是什么样的、希望做成什么样、能够带来哪些收益**，由此进行开发工作量评估与ROI确认。

|角色|阶段|类型|问题|答案|
|---|---|---|---|---|
|业务方|- 需求了解 **基于什么背景有什么问题，想要我们做什么**（我们能做什么）|who|什么团队、做什么业务？||
|||why|业务背景是什么？||
||||现状是什么/有什么问题（核心诉求）？||
||||希望做什么改变并带来哪些收益？||
||||这个需求是否涉及资金安全、权限、合规、审计、外部监管或其他高风险场景？||
|研发|- 需求评估 基于问题和期望，**评估哪些能解决**、哪些不能解决|what|评估哪些能解决？ _**用什么方案解，收益是什么**_||
||||评估哪些不能解决？ _有没有其他方式解，业务能否接受_||
||||这件事情的业务红线是什么？哪些结果是绝对不能接受的？||
||||如果方案失败，失败成本是什么？影响范围、影响对象、恢复要求分别是什么？||
||||开发工作量评估与ROI确认||
||- 方案梳理 不用一上来就满足所有诉求，避免投入没有产出|how|面向业务方可理解的解决方案是什么？ _提供结果预期，给出衡量指标_||
||||推进策略是什么？ _优先满足当前最迫切、最能给解决问题的部分_||
||||有哪些todo事项、跟进人是谁、截止日期是什么时候？||
||结果交付||业务需求调研文档 _包括需求背景、需求评估、方案确认、调研总结等_||

## Architect Execution Layer

### What this stage really accomplishes
This stage converts business complaint / wish / requirement into a designable problem statement.

### Architect obligations
- identify whether the ask is a symptom or a root problem
- force current-state vs target-state clarity
- surface business red lines, failure cost, and risk exposure
- refuse to enter solution design on a vague research package

### Exit condition
Do not leave this stage until the architect can clearly state:
- what problem is actually being solved
- why it matters now
- what result counts as success
- what cannot be broken
- what constraints shape the design space

## Companion Skills

Typical next step:
- `arch-lifecycle-solution-design-methodology`

## Common Pitfalls

1. Treating this as passive note-taking.
2. Letting vague business wording pass into design as-is.
3. Skipping ROI / red-line / failure-cost clarification.
4. When the user already provided a long mechanism draft, discussion note, or architecture底稿, falsely claiming the core business problem is still unclear without first extracting the underlying business ask from the document itself.

## Draft-first demand extraction pattern

When the input is not a blank request but an existing讨论稿 / 机制梳理 / 设计底稿, do **not** immediately bounce the user back into generic需求澄清.

Use this order:
1. first restate the business ask you extracted from the draft in one sentence
2. separate **already clear business goal** from **still-missing formal decision points**
3. identify whether the current document is really a 需求调研文档, a 解决方案底稿, or a 机制设计草案
4. only ask follow-up questions about the gaps that block solution design or scope freeze

For multi-agent / agent-cluster drafts specifically, a useful distinction is:
- the business goal may already be clear (for example: let a lead agent dynamically recruit and recursively coordinate specialists to finish complex tasks)
- while the missing pieces are often runtime semantics, governance boundaries, success metrics, or MVP scope

The architect should explicitly acknowledge that difference instead of treating the whole request as still vague.

## Verification Checklist

- [ ] Current state is explicit
- [ ] Target state is explicit
- [ ] ROI or success metric is explicit
- [ ] Business red lines and failure cost are explicit
- [ ] Demand-research output is strong enough to enter solution design

