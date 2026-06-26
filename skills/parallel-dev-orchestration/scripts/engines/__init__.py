"""
引擎工厂和统一导出
"""
from typing import Dict, Type, Optional
from pathlib import Path
from .base import CollaborationEngine
from .models import EngineConfig, WorkspaceInfo, WorkItem, WorkItemStatus


class EngineFactory:
    """引擎工厂 - 根据类型创建引擎实例"""

    _engines: Dict[str, Type[CollaborationEngine]] = {}

    @classmethod
    def register(cls, engine_type: str, engine_class: Type[CollaborationEngine]):
        """注册引擎"""
        cls._engines[engine_type] = engine_class

    @classmethod
    def create(cls, config: EngineConfig) -> CollaborationEngine:
        """根据配置创建引擎实例

        Args:
            config: 引擎配置（包含 engine_type 和其他参数）

        Returns:
            引擎实例

        Raises:
            ValueError: 未知的引擎类型
        """
        engine_type = config.engine_type.lower()

        if engine_type not in cls._engines:
            available = ', '.join(cls._engines.keys())
            raise ValueError(
                f"未知的引擎类型: {engine_type}\n"
                f"可用引擎: {available}"
            )

        engine_class = cls._engines[engine_type]
        return engine_class(config)

    @classmethod
    def list_available_engines(cls) -> list:
        """列出所有可用引擎"""
        return list(cls._engines.keys())

    @classmethod
    def get_engine_class(cls, engine_type: str) -> Optional[Type[CollaborationEngine]]:
        """获取引擎类（用于访问类方法，如 get_required_env_vars）"""
        return cls._engines.get(engine_type.lower())


# 自动注册所有引擎
def _auto_register_engines():
    """自动发现并注册所有引擎"""
    from .multica import MulticaEngine
    from .github import GithubEngine
    from .mock import MockEngine

    EngineFactory.register('multica', MulticaEngine)
    EngineFactory.register('github', GithubEngine)
    EngineFactory.register('mock', MockEngine)


# 模块加载时自动注册
_auto_register_engines()


# 便捷函数
def create_engine_from_env(env_path: Optional[Path] = None) -> CollaborationEngine:
    """从 .env 文件创建引擎

    Args:
        env_path: .env 文件路径，None 则使用默认位置（skill 根目录）

    Returns:
        引擎实例
    """
    import os
    from pathlib import Path

    if env_path is None:
        # 默认位置：skill 根目录
        skill_root = Path(__file__).parent.parent.parent
        env_path = skill_root / ".env"

    if not env_path.exists():
        raise RuntimeError(
            f".env 文件不存在: {env_path}\n"
            f"请先运行 setup.py 进行配置:\n"
            f"  python scripts/setup.py"
        )

    # 加载 .env
    env_vars = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, value = line.split('=', 1)
                # 去掉引号
                value = value.strip().strip('"').strip("'")
                env_vars[key] = value

    # 创建配置
    config = EngineConfig.from_env(env_vars)

    # 创建引擎
    return EngineFactory.create(config)


def create_engine_from_config(
    engine_type: str,
    workspace_id: str,
    **extra_config
) -> CollaborationEngine:
    """直接从参数创建引擎（不读取 .env）

    Args:
        engine_type: 引擎类型（multica/github/mock）
        workspace_id: 工作空间 ID
        **extra_config: 其他配置参数

    Returns:
        引擎实例
    """
    config = EngineConfig(
        engine_type=engine_type,
        workspace_id=workspace_id,
        extra=extra_config
    )
    return EngineFactory.create(config)


# 导出所有公共接口
__all__ = [
    # 基类和模型
    'CollaborationEngine',
    'EngineConfig',
    'WorkspaceInfo',
    'WorkItem',
    'WorkItemStatus',

    # 工厂
    'EngineFactory',
    'create_engine_from_env',
    'create_engine_from_config',

    # 具体引擎（可选导入）
    'MulticaEngine',
    'GithubEngine',
    'MockEngine',
]


# 延迟导入具体引擎（避免循环依赖）
def __getattr__(name):
    if name == 'MulticaEngine':
        from .multica import MulticaEngine
        return MulticaEngine
    elif name == 'GithubEngine':
        from .github import GithubEngine
        return GithubEngine
    elif name == 'MockEngine':
        from .mock import MockEngine
        return MockEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
