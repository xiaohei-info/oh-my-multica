"""engines — WorkItemStore(数据面)+ AgentRuntime(执行面)双接口。

设计文档 §12.3:接口 P1 即按最终形状落地;一期实现 multica + mock,
两者各自同时实现两个接口。接入新平台 = 新增 Store 实现 + 选定 Runtime 方案,
不动接口与 pipeline。

纪律(§12.4 红线):pipeline 代码只调 Store/Runtime 接口,
绝不直接 shell out 平台 CLI。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..errors import ValidationError
from .models import EngineConfig, WorkItem, WorkItemStatus, WorkspaceInfo
from .runtime import AgentRuntime
from .store import WorkItemStore

ENGINE_TYPES = ("multica", "mock")


@dataclass
class Engine:
    """一个协作引擎 = 数据面 + 执行面。"""
    store: WorkItemStore
    runtime: AgentRuntime


def create_engine(engine_type: str, config: EngineConfig) -> Engine:
    """按类型装配 Engine。未知类型报 ValidationError(报错即教学)。"""
    if engine_type == "mock":
        from .mock import MockRuntime, MockStore
        store = MockStore(config)
        return Engine(store=store, runtime=MockRuntime(store))
    if engine_type == "multica":
        from .multica import MulticaRuntime, MulticaStore
        store = MulticaStore(config)
        return Engine(store=store, runtime=MulticaRuntime(store))
    raise ValidationError(
        f"未知引擎类型 '{engine_type}',可选: {', '.join(ENGINE_TYPES)}")


__all__ = [
    "Engine", "create_engine", "ENGINE_TYPES",
    "WorkItemStore", "AgentRuntime",
    "EngineConfig", "WorkItem", "WorkItemStatus", "WorkspaceInfo",
]
