# 设计文档格式

设计文档建议使用 Markdown 正文 + YAML frontmatter。frontmatter 给 omac 和后续 agent 做结构锚定,
正文给人读。

```md
---
schema: omac.design/v1
title: 示例功能
problem: 解决哪个真实问题
non_goals:
  - 不重构无关模块
flows:
  - flow-login
risk_level: medium
---

# 设计文档

## 背景

## 目标与非目标

## 业务流程

## 核心数据

## 模块边界

## 跨模块契约

## 风险与兼容性

## 验收映射
```

## 必备内容

- 业务流程必须能映射到验收文档 flow。
- 核心数据必须说明所有权和修改路径。
- 模块边界必须说明依赖方向。
- 跨模块契约必须说明 DTO、事件、错误和状态。
- 风险与兼容性必须说明会影响哪些现有行为。

不强制 DDD。可以使用领域语言,但不能用方法论名词替代具体数据、边界和契约。
