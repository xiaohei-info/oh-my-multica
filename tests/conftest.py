import os
import sys
from pathlib import Path

import hashlib
import yaml

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from _support import budget  # noqa: E402

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
    if problems:
        report.outcome = "failed"
        report.longrepr = "resource budget exceeded: " + "; ".join(problems)


_RSS_KEY = pytest.StashKey[int]()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    item.stash[_RSS_KEY] = budget.peak_rss_bytes()
    yield


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


@pytest.fixture
def aiteam_849_legacy_snapshot():
    path = Path(__file__).parent / "fixtures" / (
        "aiteam_849_legacy_convergence_snapshot.yaml"
    )
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def seal_mock_delivery(store, item_id, pr_url, verification, *, phase=None):
    """Persist a complete controller-sealed delivery for MockStore tests."""
    from omac.core.taskmeta import DELIVERY_IDENTITY_SCHEMA, DeliveryIdentity

    head_sha = hashlib.sha256(pr_url.encode("utf-8")).hexdigest()
    kwargs = {
        "artifacts": {"pr_url": pr_url, "head_sha": head_sha},
        "verification": verification,
        "verification_source": yaml.safe_dump(
            verification, allow_unicode=True, sort_keys=False),
    }
    if phase is not None:
        kwargs["phase"] = phase
    store.update_work_item_metadata(item_id, **kwargs)
    item = store.get_work_item(item_id)
    ref = item.verification_ref
    task_id = f"test-run-{ref['attachment_id']}"
    ref["task_id"] = task_id
    store.update_work_item_metadata(
        item_id,
        delivery_identity=DeliveryIdentity(
            schema=DELIVERY_IDENTITY_SCHEMA,
            handoff_generation=f"test-handoff-{ref['attachment_id']}",
            worker=item.worker,
            agent_id=store.resolve_agent_id(item.worker),
            run_id=task_id,
            pr_url=pr_url,
            pr_head_sha=head_sha,
            verification_sha256=ref["sha256"],
            verification_attachment_id=ref["attachment_id"],
            verification_comment_id=ref["comment_id"],
            verification_uploader_id=ref.get("uploader_id"),
            verification_uploader_type=ref.get("uploader_type"),
            verification_task_id=task_id,
            verification_created_at=ref["created_at"],
        ),
    )
    return store.get_work_item(item_id)
