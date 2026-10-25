"""Engine-facing models: configuration, workspaces, agents and runtime provisioning."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class RuntimeCapabilities:
    """Runtime observation/dispatch guarantees exposed to the pipeline."""

    stable_direct_run_identity: bool = False


@dataclass
class WorkspaceInfo:
    """工作空间信息"""
    id: str
    name: str
    description: Optional[str] = None
    member_count: int = 0


@dataclass
class ProjectInfo:
    """项目信息(Multica project:一个 omac 编排实例绑定一个 project,
    repo 同时挂 project resource 与 workspace registry,所有 issue 归入 project 下)。"""
    id: str
    title: str
    repos: List[str] = field(default_factory=list)   # 目标 repo URL


@dataclass(frozen=True)
class RuntimeTarget:
    """可承载 Agent 的运行时目标。"""
    id: str
    name: str
    type: str
    status: str


@dataclass(frozen=True)
class SkillPackage:
    """待上传 Skill 的完整目录。"""
    name: str
    description: str
    path: Path
    files: Tuple[Path, ...]


@dataclass(frozen=True)
class AgentProvisionSpec:
    """创建 Agent 所需的 Harness 中立输入。"""
    name: str
    description: str
    instructions: str
    runtime_id: str
    skills: List[SkillPackage] = field(default_factory=list)


@dataclass(frozen=True)
class AgentInfo:
    id: str
    name: str


@dataclass
class EngineConfig:
    """引擎配置。角色映射在 omac 侧(config.yaml),这里只有平台定位与轮询参数。"""
    engine_type: str            # 'multica' | 'mock'
    workspace_id: str
    project_id: Optional[str] = None   # multica 必填(issue 归入该 project);mock 忽略
    polling_interval: int = 30
    polling_interval_min: int = 10
    polling_interval_max: int = 300
    extra: Dict[str, Any] = field(default_factory=dict)
