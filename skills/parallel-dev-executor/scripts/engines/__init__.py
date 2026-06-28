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

# 配置项（非敏感 ID/开关）。认证不在此列：github 用 `gh auth login`，multica 用其自身登录。
_CONFIG_KEYS = (
    'ENGINE_TYPE',
    'MULTICA_WORKSPACE_ID', 'MULTICA_SQUAD_ID',
    'GITHUB_REPO', 'GITHUB_TOKEN',
    'MOCK_WORKSPACE_ID',
    'ORCH_GIT_SYNC', 'MAX_PARALLEL',
    'POLLING_INTERVAL', 'POLLING_INTERVAL_MIN', 'POLLING_INTERVAL_MAX',
)


def _read_dotenv(env_path: Path) -> Dict[str, str]:
    """读取 .env 文件为 dict；文件不存在返回空 dict（.env 可选，不报错）。"""
    vars_: Dict[str, str] = {}
    if not env_path or not env_path.exists():
        return vars_
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, value = line.split('=', 1)
                vars_[key.strip()] = value.strip().strip('"').strip("'")
    return vars_


def _validate_config(config: EngineConfig) -> None:
    """缺必填项时报清晰可操作的错误（告诉该设哪个 env / 哪个命令行参数）。"""
    if config.engine_type in ('multica', 'github') and not config.workspace_id:
        key, flag = {
            'multica': ('MULTICA_WORKSPACE_ID', '--workspace <workspace-id>'),
            'github': ('GITHUB_REPO', '--workspace <owner/repo>'),
        }[config.engine_type]
        raise RuntimeError(
            f"{config.engine_type} 引擎缺少 workspace。\n"
            f"  → 设环境变量 {key}=...（可写进 .env），或命令行传 {flag}。\n"
            f"  认证不在这里配：github 用 `gh auth login`，multica 用 multica 登录。"
        )


def create_engine_from_env(
    env_path: Optional[Path] = None,
    *,
    engine_type: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> CollaborationEngine:
    """解析配置并创建引擎。环境变量为唯一配置面，三层合并：

    优先级（低→高）：`.env` 文件（可选） < 进程环境变量(export) < 命令行参数。

    `.env` 不存在不报错（可选）；认证交给各 CLI（gh/multica），不在配置面。
    缺必填项（engine 的 workspace）抛清晰错误。
    """
    import os
    if env_path is None:
        env_path = Path(__file__).parent.parent.parent / ".env"

    merged = _read_dotenv(env_path)                  # base: .env 文件
    for k in _CONFIG_KEYS:                            # 覆盖: 进程环境(export 优先于 .env)
        if k in os.environ:
            merged[k] = os.environ[k]
    if engine_type:                                  # 覆盖: 命令行 --engine
        merged['ENGINE_TYPE'] = engine_type

    # 把 .env 里的运行时开关暴露成进程环境，使 git_sync_enabled() 等读 os.environ 的逻辑生效
    # （已显式 export 的优先，不覆盖）
    if 'ORCH_GIT_SYNC' in merged:
        os.environ.setdefault('ORCH_GIT_SYNC', str(merged['ORCH_GIT_SYNC']))

    config = EngineConfig.from_env(merged)
    if workspace_id:                                 # 覆盖: 命令行 --workspace
        config.workspace_id = workspace_id
    _validate_config(config)
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
