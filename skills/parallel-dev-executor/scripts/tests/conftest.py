import os
from pathlib import Path

import pytest

# 跑测试时，从 skill 根的 .env 自动补全 live 测试所需的环境变量（仅这两个键，
# 避免影响其它用例的隔离）。这样在装了 multica CLI 的机器上，只需「第一次」把
#   MULTICA_WORKSPACE_ID=...
#   MULTICA_TEST_SQUAD=...
# 写进 .env（gitignored、不进仓、不分叉代码），以后每次 pytest 都自动带上、
# live 测试持续开启。已显式 export 的优先（setdefault 不覆盖）。
_LIVE_KEYS = ("MULTICA_WORKSPACE_ID", "MULTICA_TEST_SQUAD")


def _load_live_env_from_dotenv(env_path=None):
    if env_path is None:
        env_path = Path(__file__).parent.parent.parent / ".env"
    if not Path(env_path).exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key in _LIVE_KEYS:
                os.environ.setdefault(key, value.strip().strip('"').strip("'"))


_load_live_env_from_dotenv()


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live_multica: marks tests that require a real multica CLI + daemon (opt-in, default skip)",
    )
