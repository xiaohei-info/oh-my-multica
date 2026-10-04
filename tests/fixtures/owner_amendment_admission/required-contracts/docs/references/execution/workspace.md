# Workspace 契约参考

本文定义 Workspace 领域的生命周期、能力与安全要求。Workspace Provider 是平台内部 `WorkspacePort` 实现，不是首版公开 ExtensionPackage；它不理解 Project 角色、Workflow 状态、AgentRun、Git Review 或 Gate。

共同记法见 [Reference 共同约定](../conventions.md)，设计边界见 [Workspace 详细设计](../../design/detailed/07-workspace-detailed-design.md)。

## 1. WorkspaceRequest

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| request_id | 是 | 创建请求幂等身份 | 重放不得产生重复 Workspace |
| workspace_purpose | 是 | build、review、planning、acceptance 等通用用途 | 不包含产品角色枚举 |
| source | 否 | `WorkspaceSource` tagged union，显式区分 Git commit、Artifact 和 Snapshot | 必须固定不可变 ID/commit/digest |
| persistence | 是 | ephemeral 或 persistent | 与 Lineage 连续性要求一致 |
| resource_requirements | 是 | CPU、内存、GPU、存储和拓扑 | Provider 必须完整满足 |
| security_requirements | 是 | 隔离、网络、凭证与数据要求 | 不支持时 Fail Closed |
| retention_policy | 是 | 释放、保留和清理规则 | 不能依赖隐式默认值 |
| idempotency_key | 是 | 逻辑 Workspace 身份 | 不以当前时间单独生成 |

## 2. Workspace Capability

| Capability | 含义 | 不支持时的上层行为 |
| --- | --- | --- |
| Persistent | 跨 AgentRun 保留文件状态 | 使用 Git/Artifact 重建，不假设本地状态存在 |
| SuspendResume | 暂停并恢复同一 Workspace | 保持运行或释放后重建 |
| SnapshotRestore | 生成和恢复文件系统快照 | 使用 Git commit、Artifact 或重新构建恢复 |
| WarmPool | 预热可快速分配的 Workspace | 接受正常创建延迟 |
| StableEndpoint | 生命周期内具有稳定网络端点 | 不暴露依赖稳定端点的上层能力 |
| StrongIsolation | 提供强于共享进程的隔离 | Policy 要求时拒绝较弱 Provider |
| NetworkPolicyEnforcement | 强制网络模式和允许端点 | 无法证明时拒绝创建 |
| DedicatedPlacement | 放置到专用节点或隔离域 | 数据分类要求时拒绝不支持的 Provider |

首版默认 Provider 使用 Kubernetes Pod 与 PVC；Kubernetes Agent Sandbox 可以作为未来系统内置实现，不改变 WorkspacePort 合同。Project 用户只配置 Workspace 策略，不选择 Provider。

## 3. WorkspaceHandle

| 字段 | 必需 | 含义 | 关键约束 |
| --- | --- | --- | --- |
| workspace_id | 是 | Workspace 稳定身份 | 不编码 Project 或 Agent 角色 |
| provider_key | 是 | 平台内置 WorkspaceProvider 实现身份 | 由系统根据发行版和策略解析；用户不填写，生命周期内不切换 |
| state | 是 | Allocating、Ready、Suspended、Releasing、Released、Lost | 一个枚举一种含义 |
| endpoints[] | 否 | `WorkspaceEndpoint` 列表，显式包含 kind、URI 和访问能力 | 受授权和保留策略限制 |
| persistence_handle | 否 | Provider 拥有的 PVC、Snapshot 或等价不透明句柄 | 上层只保存和回传，不解释 Provider 私有格式 |
| observed_capabilities[] | 是 | 实际提供的 Capability | 必须覆盖请求中的 Required 能力 |
| created_at / expires_at | 是 | 生命周期时间 | 时间必须显式输入 |

Workspace Adapter 是产品模型到 WorkspacePort 的唯一映射边界。Runtime SDK 与 Orchestrator Kernel 不直接依赖 WorkspacePort。
