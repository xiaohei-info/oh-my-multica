---
name: software-design-lifecycle-deploy-ops-deployment-arch-diagramming
description: "Use when an architect in the deploy-and-ops stage must produce or review the 部署架构图 / 物理架构图 so that physical topology, deployment boundaries, and run-stage assumptions are visible during handoff."
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [architect, lifecycle, deployment, operations, deployment-architecture]
    related_skills: [arch-lifecycle-deploy-ops-methodology]
---

# Deploy / Ops Deployment Architecture Diagramming

## Overview

This skill owns **部署运维阶段的部署架构图 / 物理架构图**.

## Canonical Deploy / Ops Slice (Preserved)

| 物理架构 | 硬件与网络拓扑 | 部署架构图 |

## What This Artifact Must Answer

- where the system runs
- what physical or deployment boundaries exist
- what network/topology assumptions the run-stage audience needs

## Verification Checklist

- [ ] Physical / deployment boundaries are explicit
- [ ] Network/topology assumptions are explicit
- [ ] The artifact supports rollout / rollback / operations understanding

