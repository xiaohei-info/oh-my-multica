import os
import sys
from pathlib import Path

import hashlib
import yaml

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


@pytest.fixture
def aiteam_849_legacy_snapshot():
    path = Path(__file__).parent / "fixtures" / (
        "aiteam_849_legacy_convergence_snapshot.yaml"
    )
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture
def contracts_platform_resource_snapshot():
    path = Path(__file__).parent / "fixtures" / (
        "contracts_platform_resource_invalid_ledger_snapshot.yaml"
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
