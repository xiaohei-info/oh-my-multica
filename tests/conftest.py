import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from _support import budget, shard  # noqa: E402

_session_tmp_bytes = 0


def pytest_addoption(parser):
    parser.addoption("--shard", default=None, metavar="I/N",
                     help="run only every N-th test file (sorted by path), starting at the I-th: CI splits tests/it this way")


def pytest_collection_modifyitems(config, items):
    spec = config.getoption("--shard")
    if spec:
        keep = shard.mine((str(item.fspath) for item in items), spec)
        dropped = [item for item in items if str(item.fspath) not in keep]
        config.hook.pytest_deselected(items=dropped)
        items[:] = [item for item in items if str(item.fspath) in keep]


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
        kind=budget.kind_of(m.name for m in item.iter_markers()))
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
    # A symlink to `true`, not a fresh script: macOS scans a newly written executable on first run (~0.3 s).
    (directory / "gh").symlink_to(shutil.which("true"))
    return directory


@pytest.fixture(autouse=True)
def _no_real_gh(_gh_stub_dir, monkeypatch, request):
    if "tests/it" not in str(request.fspath):
        monkeypatch.setenv("PATH", f"{_gh_stub_dir}{os.pathsep}{os.environ['PATH']}")


@pytest.fixture(autouse=True)
def _instant_waits(monkeypatch, request):
    """The clock is an external boundary: unit tests do not wait out observation intervals or retry backoff."""
    if "tests/it" in str(request.fspath):
        return
    from omac.pipeline import loop
    monkeypatch.setattr(loop, "_HANDOFF_OBSERVATION_INTERVAL", 0.0)
    monkeypatch.setattr(loop, "_TRANSIENT_RUNTIME_RETRY_BACKOFF_SECONDS", 0.0)


@pytest.fixture(autouse=True)
def _no_disk_flush(monkeypatch, request):
    """Disk durability is an external boundary: unit tests do not wait for fsync (a killed process still
    leaves only whole files, which is what tests/test_manifest*.py check)."""
    if "tests/it" not in str(request.fspath):
        monkeypatch.setattr(os, "fsync", lambda _fd: None)


@pytest.fixture
def legacy_convergence_snapshot():
    from _support.samples import legacy_convergence_snapshot as build
    return build()


