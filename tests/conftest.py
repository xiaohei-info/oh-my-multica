import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(autouse=True)
def _reset_mock_state():
    """每个测试前重置 MockStore 的模块级共享状态,保证隔离。"""
    from omac.engines.mock import MockStore
    MockStore.reset()
    # 测试默认 0 延迟:auto-complete 在首次 wake 即收敛,避免真实等待。
    # 通过 env 传播到所有 in-process main() MockStore 实例
    # (MockStore.__init__ 会按 config.extra 重设全局 delay,而 extra 取自 env)。
    os.environ["MOCK_AUTO_COMPLETE_DELAY"] = "0"
    yield
