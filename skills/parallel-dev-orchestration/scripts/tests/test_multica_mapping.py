"""
MulticaEngine 状态映射单元测试 — 不需要真 CLI，纯函数验证。

守护 issue #232 修复的两条契约 bug：
1. _status_to_multica(FAILED) == "blocked"（不是 "failed"，真 CLI 无 failed 状态）
2. _multica_to_status("cancelled") == BLOCKED（防止已取消 issue 被当 todo 重派）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engines.multica import MulticaEngine
from engines.models import WorkItemStatus, EngineConfig

# _status_to_multica / _multica_to_status 是实例方法，用 dummy config 建实例
_dummy = MulticaEngine(EngineConfig(engine_type="multica", workspace_id="dummy"))


def test_status_to_multica_failed_maps_to_blocked():
    """FAILED -> "blocked"，不是 "failed"（真 CLI 合法值无 failed）。"""
    assert _dummy._status_to_multica(WorkItemStatus.FAILED) == "blocked"


def test_multica_to_status_cancelled_maps_to_blocked():
    """cancelled -> BLOCKED，不是默认 TODO（防止已取消 issue 被重派）。"""
    assert _dummy._multica_to_status("cancelled") == WorkItemStatus.BLOCKED


def test_status_to_multica_full_table():
    """全状态正向映射对照真 CLI 合法值。"""
    assert _dummy._status_to_multica(WorkItemStatus.TODO) == "todo"
    assert _dummy._status_to_multica(WorkItemStatus.IN_PROGRESS) == "in_progress"
    assert _dummy._status_to_multica(WorkItemStatus.IN_REVIEW) == "in_review"
    assert _dummy._status_to_multica(WorkItemStatus.DONE) == "done"
    assert _dummy._status_to_multica(WorkItemStatus.BLOCKED) == "blocked"


def test_multica_to_status_full_table():
    """全状态反向映射。"""
    assert _dummy._multica_to_status("todo") == WorkItemStatus.TODO
    assert _dummy._multica_to_status("in_progress") == WorkItemStatus.IN_PROGRESS
    assert _dummy._multica_to_status("in_review") == WorkItemStatus.IN_REVIEW
    assert _dummy._multica_to_status("done") == WorkItemStatus.DONE
    assert _dummy._multica_to_status("blocked") == WorkItemStatus.BLOCKED
    assert _dummy._multica_to_status("cancelled") == WorkItemStatus.BLOCKED
