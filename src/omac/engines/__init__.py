"""engines — three ports: WorkItemStore (work items), CodeHost (pull requests, checks, merge)
and AgentRuntime (agent runs).

An engine combines one adapter per port; the pipeline calls only the port interfaces and never
shells out to platform CLIs. Adding a work-item platform or a code host means adding an adapter,
not changing the pipeline.
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
from .codehost import CodeHost
from .runtime import AgentRuntime
from .store import WorkItemStore

ENGINE_TYPES = ("multica",)


@dataclass
class Engine:
    """One engine = one adapter per port."""
    store: WorkItemStore
    runtime: AgentRuntime
    code_host: CodeHost


def _unknown_engine(engine_type: str) -> ValidationError:
    removed = ui(
        " The built-in `mock` engine was removed in 2.0; run against a Multica workspace "
        "(tests use a private Docker Multica, see tests/it).",
        " 内置的 `mock` 引擎已在 2.0 删除;请连接 Multica 工作区(测试使用私有 Docker Multica,见 tests/it)。",
    ) if engine_type == "mock" else ""
    return ValidationError(ui(
        f"Unknown engine type '{engine_type}'. Available: {', '.join(ENGINE_TYPES)}.{removed}",
        f"未知引擎类型 '{engine_type}',可选: {', '.join(ENGINE_TYPES)}。{removed}"))


def code_host_for(engine_type: str) -> CodeHost:
    """The code-host adapter that belongs with a work-item engine type."""
    if engine_type == "multica":
        from .github import GitHubCodeHost
        return GitHubCodeHost()
    raise _unknown_engine(engine_type)


def create_engine(engine_type: str, config: EngineConfig) -> Engine:
    """按类型装配 Engine。未知类型报 ValidationError(报错即教学)。"""
    if engine_type == "multica":
        from .multica import MulticaRuntime, MulticaStore
        store = MulticaStore(config)
        return Engine(store=store, runtime=MulticaRuntime(store), code_host=code_host_for(engine_type))
    raise _unknown_engine(engine_type)


def engine_from_config(
    config: Mapping[str, Any],
    *,
    engine: str | None = None,
    workspace: str | None = None,
    project: str | None = None,
    engine_extra: Mapping[str, Any] | None = None,
    workspace_optional: bool = False,
) -> Engine:
    """The one way the CLI assembles an engine: config.yaml < OMAC_* env < explicit arguments.

    ``workspace_optional`` serves `omac init`, which lists workspaces before one is chosen.
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
    return create_engine(engine_type, EngineConfig(
        engine_type=engine_type, workspace_id=workspace_id, project_id=project_id,
        polling_interval=(config.get("defaults") or {}).get("poll_interval", DEFAULTS["poll_interval"]),
        extra=extra or None))


__all__ = [
    "Engine", "create_engine", "code_host_for", "engine_from_config", "ENGINE_TYPES",
    "WorkItemStore", "AgentRuntime", "CodeHost",
    "AgentInfo", "AgentProvisionSpec", "RuntimeTarget", "SkillPackage",
    "EngineConfig", "RuntimeCapabilities", "WorkspaceInfo",
]
