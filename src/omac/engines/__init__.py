"""engines — WorkItemStore(数据面)+ AgentRuntime(执行面)双接口。

设计文档 §12.3:接口 P1 即按最终形状落地;一期实现 multica + mock,
两者各自同时实现两个接口。接入新平台 = 新增 Store 实现 + 选定 Runtime 方案,
不动接口与 pipeline。

纪律(§12.4 红线):pipeline 代码只调 Store/Runtime 接口,
绝不直接 shell out 平台 CLI。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from ..core.config import DEFAULTS, ENV_ENGINE, ENV_WORKSPACE, resolve_engine_settings
from ..errors import ValidationError
from ..i18n import ui
from .models import (
    AgentInfo,
    AgentProvisionSpec,
    EngineConfig,
    RuntimeCapabilities,
    RuntimeTarget,
    SkillPackage,
    WorkspaceInfo,
)
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
    raise ValidationError(ui(
        f"Unknown engine type '{engine_type}'. Available: {', '.join(ENGINE_TYPES)}",
        f"未知引擎类型 '{engine_type}',可选: {', '.join(ENGINE_TYPES)}"))


def engine_from_config(
    config: Mapping[str, Any],
    *,
    engine: str | None = None,
    workspace: str | None = None,
    project: str | None = None,
    engine_extra: Mapping[str, Any] | None = None,
    default_extra: Mapping[str, Any] | None = None,
    workspace_optional: bool = False,
) -> Engine:
    """The one way the CLI assembles an engine: config.yaml < OMAC_* env < explicit arguments.

    ``workspace_optional`` serves `omac init`, which lists workspaces before one is chosen.
    ``default_extra`` is used only when neither config nor environment supplied any extra settings.
    """
    if workspace_optional:
        engine_type = engine or os.environ.get(ENV_ENGINE) or config.get("engine")
        workspace_id, project_id = workspace or "", project
        if not engine_type:
            resolve_engine_settings(dict(config), engine=engine, workspace="-")  # raises the teaching error
    else:
        engine_type, workspace_id, project_id = resolve_engine_settings(
            dict(config), engine=engine, workspace=workspace, project=project)
    extra = dict(config.get("engine_extra") or {})
    if config.get("workspace_slug"):
        extra["workspace_slug"] = config["workspace_slug"]
    extra.update(engine_extra or {})
    # MOCK_* passthrough disappears together with the mock engine (Phase 8).
    extra.update({k: v for k, v in os.environ.items()
                  if k.startswith(("OMAC_", "MOCK_")) and k not in (ENV_ENGINE, ENV_WORKSPACE)})
    return create_engine(engine_type, EngineConfig(
        engine_type=engine_type, workspace_id=workspace_id, project_id=project_id,
        polling_interval=(config.get("defaults") or {}).get("poll_interval", DEFAULTS["poll_interval"]),
        extra=extra or dict(default_extra or {})))


__all__ = [
    "Engine", "create_engine", "engine_from_config", "ENGINE_TYPES",
    "WorkItemStore", "AgentRuntime",
    "AgentInfo", "AgentProvisionSpec", "RuntimeTarget", "SkillPackage",
    "EngineConfig", "RuntimeCapabilities", "WorkspaceInfo",
]
