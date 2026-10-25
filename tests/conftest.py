import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from _support import budget  # noqa: E402
from _support import fake_platform  # noqa: E402

fake_platform.install()  # before test modules do `from omac.engines import create_engine`

_session_tmp_bytes = 0


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Enforce the per-test budgets of tests/_support/budget.py (PLAN 6.2)."""
    global _session_tmp_bytes
    outcome = yield
    report = outcome.get_result()
    if call.when != "call" or not report.passed or Path(item.fspath).name in budget.EXEMPT_FILES:
        return
    tmp = item.funcargs.get("tmp_path")
    tmp_bytes = budget.tree_bytes(tmp) if tmp else 0
    _session_tmp_bytes += tmp_bytes
    problems = budget.check_call(
        seconds=call.duration, tmp_bytes=tmp_bytes, session_tmp_bytes=_session_tmp_bytes,
        rss_before=item.stash.get(_RSS_KEY, 0), rss_after=budget.peak_rss_bytes(),
        is_docker=item.get_closest_marker("docker") is not None)
    if problems and os.environ.get("OMAC_BUDGET_REPORT"):  # calibration: list overages instead of failing
        with open(os.environ["OMAC_BUDGET_REPORT"], "a", encoding="utf-8") as out:
            out.write(f"{item.nodeid}\t{'; '.join(problems)}\n")
    elif problems:
        report.outcome = "failed"
        report.longrepr = "resource budget exceeded: " + "; ".join(problems)


_RSS_KEY = pytest.StashKey[int]()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    item.stash[_RSS_KEY] = budget.peak_rss_bytes()
    yield


@pytest.fixture(scope="session")
def _gh_stub_dir(tmp_path_factory):
    """Unit tests never reach a real GitHub: any `gh` the product runs is a no-op that succeeds."""
    directory = tmp_path_factory.mktemp("gh-stub")
    stub = directory / "gh"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    return directory


@pytest.fixture(autouse=True)
def _no_real_gh(_gh_stub_dir, monkeypatch, request):
    if "tests/it" not in str(request.fspath):
        monkeypatch.setenv("PATH", f"{_gh_stub_dir}{os.pathsep}{os.environ['PATH']}")


@pytest.fixture(autouse=True)
def _reset_mock_state():
    """每个测试前重置 MockStore 的模块级共享状态,保证隔离。"""
    fake_platform.MockStore.reset()
    # 测试默认 0 延迟:auto-complete 在首次 wake 即收敛,避免真实等待。
    # 通过 env 传播到所有 in-process main() MockStore 实例
    # (MockStore.__init__ 会按 config.extra 重设全局 delay,而 extra 取自 env)。
    os.environ["MOCK_AUTO_COMPLETE_DELAY"] = "0"
    yield


@pytest.fixture
def legacy_convergence_snapshot():
    from _support.samples import legacy_convergence_snapshot as build
    return build()


