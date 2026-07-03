"""omac — oh-my-agent-cluster 的确定性编排 CLI。

架构分层(docs/omac-cli-design.md §3):
    cli/       命令树 / help / 退出码 / 输出层
    pipeline/  plan 流水线 / dag loop / 派发·回收
    core/      manifest · lint · graph · evidence · config
    engines/   WorkItemStore + AgentRuntime 双接口(multica · mock)
    guide/     知识分发 topic 文本
"""

__version__ = "0.1.0"
