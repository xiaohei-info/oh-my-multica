# Webhook Inbox：一次真实的端到端交付

oh-my-multica 接收到一个需求：交付一个满足生产约束的 Webhook Inbox，用于验证、保存并去重第三方
系统发送的 Webhook 事件。

Planner 和 Orchestrator Agent 规划交付 DAG，Worker Agent 并行实现各个节点，Reviewer Agent 独立
验证每项改动，Acceptor Agent 验收集成后的服务。确定性交付 Loop 负责依赖、证据门、合并条件和最终
收敛。

最终交付的是一个 FastAPI + SQLite 服务。它能够验证 HMAC-SHA256 签名，在重试和并发投递时避免
重复记录，拒绝同一事件 ID 下的冲突内容，并提供事件查询、健康检查和非 root 容器运行能力。

完整需求、Agent 协作过程、公开 Pull Request、业务行为和验证结果见
[Demo README](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/blob/main/README.zh-CN.md)。
