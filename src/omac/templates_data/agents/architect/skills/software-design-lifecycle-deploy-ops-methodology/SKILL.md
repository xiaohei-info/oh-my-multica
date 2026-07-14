---
name: software-design-lifecycle-deploy-ops-methodology
description: "Use when an architect must run the deployment-and-operations handoff stage, preserve the full lifecycle-stage doctrine for deploy/ops, and turn completed design into physical topology, rollout, operations, monitoring, emergency, and rehearsal deliverables."
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [architect, lifecycle, deployment, operations, handoff]
    related_skills: [arch-lifecycle-delivery, arch-lifecycle-deploy-ops-deployment-arch-diagramming, arch-lifecycle-tech-detailed-methodology]
---

# Deploy / Ops Methodology

## Overview

This is the `architect` profile's **部署运维阶段方法论 skill**.

## When to Use

Use when:
- 概要设计与详细设计已 review 确认
- the architect must hand design into deployment and operational reality

## Canonical Stage Doctrine (Full Preservation)

# 四、部署运维

**时间**：概要设计文档、详细设计文档等在技术人员review确认后（前后端rd、数据rd、qa、pm）

**输入**：概要设计文档、详细设计文档

**输出**：部署运维文档

**过程**：

| 步骤 | 问题 | 答案 |
| --- | --- | --- |
| 物理架构 | 硬件与网络拓扑 | 部署架构图 |
| 部署方案 | 是否支持分批发布、灰度发布、金丝雀发布？上线顺序、验证步骤、回滚入口是什么？ | 上线方案 / SOP |
| 运维手册 | 运维步骤、运维工具、运维措施、故障处理 SOP | |
| 监控与告警 | 核心监控指标、告警阈值、通知方式、值班人与升级路径是什么？ | 监控告警清单 |
| 应急预案 | 出现故障后如何止血、回滚、扩容、降级、切流、恢复与核对？ | 应急预案 |
| 容量与演练 | 是否有容量评估、压测方案、容灾演练、回滚演练？ | 演练记录 / 计划 |

## Architect Execution Layer

- do not hand off a design whose runtime assumptions are still implicit
- make rollback, degrade, recovery, and rehearsal paths explicit
- ensure ownership and operational boundaries are clear
- treat monitoring and emergency handling as first-class outputs

## Companion Diagram / Artifact Skills

- `arch-lifecycle-deploy-ops-deployment-arch-diagramming`

## Verification Checklist

- [ ] Physical topology / deployment shape is explicit
- [ ] Rollout and rollback expectations are explicit
- [ ] Monitoring / alerting / emergency assumptions are explicit
- [ ] Capacity and rehearsal expectations are explicit
- [ ] Design handoff is operationally actionable

