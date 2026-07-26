"""Multica 桥接层(切片 5)—— 只组合 WorkItemStore/AgentRuntime,不重建调度器。

纪律(§12.4):桥接层与 pipeline/CLI 一样,只能调用引擎的 WorkItemStore 与
AgentRuntime 接口;平台 CLI 调用永远封装在引擎适配器内。
"""
