"""
run_dag 失败旁路补测 — 全部用 MockEngine + 行为断言，不改源码。

覆盖 _harvest / start_new_run / execute_dag 中的失败旁路分支，目标分支覆盖率 >=90%。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import load_manifest, save_manifest, set_node
from engines import create_engine_from_config, WorkItemStatus
from run_dag import _harvest, start_new_run, execute_dag, reconcile
import run_dag as rd


# ==================== helpers ====================

def _make_mock_engine():
    """快速 MockEngine：auto-complete delay=0，polling 1ms。"""
    env = {
        "ENGINE_TYPE": "mock",
        "MOCK_WORKSPACE_ID": "ws",
        "MOCK_AUTO_COMPLETE": "true",
        "MOCK_AUTO_COMPLETE_DELAY": "0",
        "POLLING_INTERVAL": "1",
    }
    engine = create_engine_from_config("mock", "ws", **env)
    engine.config.polling_interval = 0.001
    engine._members["sq"] = ["alice", "bob", "carol"]
    return engine


def _write_manifest(path, *, squad="sq", reviewer=None, worker_a=None):
    """单节点 A 的 manifest，可配 reviewer。"""
    worker_a = worker_a or "alice"
    rev_line = f"    reviewer: {reviewer}\n" if reviewer else ""
    yaml_text = (
        "meta:\n"
        "  name: failure-test\n"
        f"  squad: {squad}\n"
        "nodes:\n"
        "  - id: A\n"
        f"    worker: {worker_a}\n"
        "    title: Task A\n"
        "    description: 'Test task'\n"
        f"{rev_line}"
    )
    with open(path, "w") as f:
        f.write(yaml_text)


def _write_manifest_two_node(path):
    """A->B 两层 DAG，squad=sq，members [alice, bob, carol]。"""
    yaml_text = (
        "meta:\n"
        "  name: failure-test\n"
        "  squad: sq\n"
        "nodes:\n"
        "  - id: A\n"
        "    worker: alice\n"
        "    title: Task A\n"
        "    description: 'Upstream'\n"
        "  - id: B\n"
        "    worker: bob\n"
        "    title: Task B\n"
        "    description: 'Downstream'\n"
        "    blocked_by: [A]\n"
    )
    with open(path, "w") as f:
        f.write(yaml_text)


# ==================== _harvest 失败旁路 ====================

# 1. get_work_item 抛异常 -> continue (run_dag.py:94-97)
def test_harvest_get_work_item_exception_continue(tmp_path, monkeypatch):
    """in_progress 节点但 work_item_id 指向不存在的 item -> _harvest 不 crash，manifest 不变。"""
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path)
    engine = _make_mock_engine()
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    m = load_manifest(manifest_path)
    set_node(m, "A", work_item_id="nonexistent", status="in_progress")
    save_manifest(m, manifest_path)

    completed, failed = set(), set()
    # 不应 raise
    changed = _harvest(engine, m, manifest_path, completed, failed)

    assert not changed, "work_item 不存在时应 continue，changed=False"
    assert "A" not in completed and "A" not in failed, "不存在 item 不应进任何集合"
    assert m.nodes["A"].status == "in_progress", "节点状态不应改变"


# 2. worker DONE 但缺 PR 产物 -> blocked (run_dag.py:113-117)
def test_harvest_done_missing_pr_blocked(tmp_path, monkeypatch):
    """worker DONE 但 item.artifacts 为空 -> blocked + failed 集合含该 key。"""
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path)
    engine = _make_mock_engine()
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    # 手动创建 work item 并标 DONE，但不设 artifacts
    item = engine.create_work_item(
        workspace_id="sq", title="Task A", description="Test",
        dag_key="A", worker="alice",
    )
    engine.update_status(item.id, WorkItemStatus.DONE)
    # 清掉自动完成时生成的 artifacts
    engine._work_items[item.id].artifacts = None

    m = load_manifest(manifest_path)
    set_node(m, "A", work_item_id=item.id, status="in_progress")
    save_manifest(m, manifest_path)

    completed, failed = set(), set()
    _harvest(engine, m, manifest_path, completed, failed)

    assert m.nodes["A"].status == "blocked", f"缺 PR 应 blocked，实际 {m.nodes['A'].status}"
    assert "A" in failed, "缺 PR 应进 failed 集合"
    assert "A" not in completed, "缺 PR 不应进 completed"
    # 平台侧也应被标 BLOCKED
    assert engine._work_items[item.id].status == WorkItemStatus.BLOCKED


# 3. worker FAILED -> blocked (run_dag.py:118-124)
def test_harvest_worker_failed_blocked(tmp_path, monkeypatch):
    """worker FAILED -> blocked + failed 集合含该 key + engine.update_status(BLOCKED) 被调用。"""
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path)
    engine = _make_mock_engine()
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    # 用 set_fail_keys 注入失败，建 item 并 assign 触发 auto_complete
    engine.set_fail_keys({"A"})
    item = engine.create_work_item(
        workspace_id="sq", title="Task A", description="Test",
        dag_key="A", worker="alice",
    )
    engine.assign_work_item(item.id, "alice", "worker")
    engine.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    # 触发 auto_complete（delay=0，assign 时已记录时间，get 时完成）
    engine.get_work_item(item.id)
    assert engine._work_items[item.id].status == WorkItemStatus.FAILED, (
        f"fail_keys 注入后应 FAILED，实际 {engine._work_items[item.id].status}"
    )

    m = load_manifest(manifest_path)
    set_node(m, "A", work_item_id=item.id, status="in_progress")
    save_manifest(m, manifest_path)

    completed, failed = set(), set()
    _harvest(engine, m, manifest_path, completed, failed)

    assert m.nodes["A"].status == "blocked", f"FAILED 应 blocked，实际 {m.nodes['A'].status}"
    assert "A" in failed, "FAILED 应进 failed 集合"
    assert engine._work_items[item.id].status == WorkItemStatus.BLOCKED, (
        "harvest 应调 update_status(BLOCKED)"
    )


# 4. in_review 但 verdict 未出 -> continue 等待 (run_dag.py:128-130)
def test_harvest_in_review_no_verdict_continue(tmp_path, monkeypatch):
    """in_review 节点但 item.review_verdict 为 None -> 不变，continue 等待。"""
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path)
    engine = _make_mock_engine()
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    # 创建 item 并标 IN_REVIEW，但不设 review_verdict
    item = engine.create_work_item(
        workspace_id="sq", title="Task A", description="Test",
        dag_key="A", worker="alice",
    )
    engine.update_status(item.id, WorkItemStatus.IN_REVIEW)
    assert engine._work_items[item.id].review_verdict is None

    m = load_manifest(manifest_path)
    set_node(m, "A", work_item_id=item.id, status="in_review")
    save_manifest(m, manifest_path)

    completed, failed = set(), set()
    changed = _harvest(engine, m, manifest_path, completed, failed)

    assert not changed, "verdict 未出时应 continue，changed=False"
    assert "A" not in completed and "A" not in failed, "无 verdict 不应进任何集合"
    assert m.nodes["A"].status == "in_review", "节点状态应保持 in_review"


# 5. reviewer reject (verdict 非 pass/pass-with-nits) -> blocked (run_dag.py:136-141)
def test_harvest_reviewer_reject_blocked(tmp_path, monkeypatch):
    """in_review 节点 verdict='needs-changes' -> blocked + failed 集合含该 key。"""
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path, reviewer="bob")
    engine = _make_mock_engine()
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    # 创建 item，标 IN_REVIEW，设 review_verdict='needs-changes'
    item = engine.create_work_item(
        workspace_id="sq", title="Task A", description="Test",
        dag_key="A", worker="alice", reviewer="bob",
    )
    engine.update_status(item.id, WorkItemStatus.IN_REVIEW)
    engine.update_work_item_metadata(item.id, review_verdict="needs-changes", review_comment="Fix it")

    m = load_manifest(manifest_path)
    set_node(m, "A", work_item_id=item.id, status="in_review")
    save_manifest(m, manifest_path)

    completed, failed = set(), set()
    _harvest(engine, m, manifest_path, completed, failed)

    assert m.nodes["A"].status == "blocked", f"reject 应 blocked，实际 {m.nodes['A'].status}"
    assert "A" in failed, "reject 应进 failed 集合"
    assert "A" not in completed, "reject 不应进 completed"
    assert engine._work_items[item.id].status == WorkItemStatus.BLOCKED, (
        "reject 应调 update_status(BLOCKED)"
    )


# ==================== start_new_run 失败旁路 ====================

# 6. manifest.meta 缺 squad -> SystemExit (run_dag.py:301-304)
def test_start_new_run_missing_squad_exit(tmp_path, monkeypatch):
    """manifest.meta 没有 squad -> sys.exit(1)。"""
    manifest_path = str(tmp_path / "dag.yaml")
    yaml_text = (
        "meta:\n"
        "  name: no-squad\n"
        "nodes:\n"
        "  - id: A\n"
        "    worker: alice\n"
        "    title: Task A\n"
        "    description: 'Test'\n"
    )
    with open(manifest_path, "w") as f:
        f.write(yaml_text)

    engine = _make_mock_engine()
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    with pytest.raises(SystemExit) as exc_info:
        start_new_run(manifest_path, engine=engine)
    assert exc_info.value.code == 1, "缺 squad 应 exit(1)"


# 7. Lint 失败 -> SystemExit (run_dag.py:328-332)
def test_start_new_run_lint_failure_exit(tmp_path, monkeypatch):
    """worker 不在 squad 成员池 -> lint 失败 -> sys.exit(1)。"""
    manifest_path = str(tmp_path / "dag.yaml")
    yaml_text = (
        "meta:\n"
        "  name: bad-worker\n"
        "  squad: sq\n"
        "nodes:\n"
        "  - id: A\n"
        "    worker: zombie\n"
        "    title: Task A\n"
        "    description: 'Test'\n"
    )
    with open(manifest_path, "w") as f:
        f.write(yaml_text)

    engine = _make_mock_engine()
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    with pytest.raises(SystemExit) as exc_info:
        start_new_run(manifest_path, engine=engine)
    assert exc_info.value.code == 1, "lint 失败应 exit(1)"


# 8. engine is None -> create_engine_from_env (run_dag.py:306-312)
def test_start_new_run_engine_none_calls_create_engine_from_env(tmp_path, monkeypatch):
    """engine=None 时 start_new_run 调 create_engine_from_env，用 monkeypatch 替换为 MockEngine。"""
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path)
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    mock_engine = _make_mock_engine()

    call_count = [0]
    def fake_create_engine_from_env():
        call_count[0] += 1
        return mock_engine

    monkeypatch.setattr(rd, "create_engine_from_env", fake_create_engine_from_env)

    start_new_run(manifest_path, engine=None)

    assert call_count[0] == 1, "应调 create_engine_from_env 一次"
    m = load_manifest(manifest_path)
    assert m.nodes["A"].status == "done"


# 9. MAX_PARALLEL 解析异常 -> 用默认值 (run_dag.py:319-322)
def test_start_new_run_max_parallel_parse_error_uses_default(tmp_path, monkeypatch):
    """extra['MAX_PARALLEL']='not_a_number' -> 不 crash，用默认 max_parallel=4。"""
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path)
    engine = _make_mock_engine()
    engine.config.extra["MAX_PARALLEL"] = "not_a_number"
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    # 不应 raise，正常跑完
    start_new_run(manifest_path, engine=engine)

    m = load_manifest(manifest_path)
    assert m.nodes["A"].status == "done", "解析异常时应回退默认值正常执行"


# ==================== execute_dag 失败旁路 ====================

# 10. slots == 0 满载等待 (run_dag.py:244-250)
def test_execute_dag_slots_zero_waiting(tmp_path, monkeypatch):
    """max_parallel=1，已有 1 个 in_flight，有 ready 节点 -> ready 不被派发，等在飞完成。"""
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest_two_node(manifest_path)  # A -> B
    engine = _make_mock_engine()
    # 用慢引擎让 auto_complete_delay > polling，制造可观察的满载窗口
    engine._auto_complete_delay = 5
    engine.config.polling_interval = 0.01
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    m = load_manifest(manifest_path)

    # 手动建 A 的 work item 并标 in_progress（模拟已派发）
    item_a = engine.create_work_item(
        workspace_id="sq", title="Task A", description="Upstream",
        dag_key="A", worker="alice",
    )
    set_node(m, "A", work_item_id=item_a.id, status="in_progress")
    engine.assign_work_item(item_a.id, "alice", "worker")
    engine.update_status(item_a.id, WorkItemStatus.IN_PROGRESS)
    save_manifest(m, manifest_path)

    # 在线程里延迟触发 auto_complete，避免测试 hang
    import threading
    import time as _time
    def delayed_complete():
        _time.sleep(0.2)
        engine._auto_complete_delay = 0
        engine._auto_complete_check(item_a.id)
    t = threading.Thread(target=delayed_complete, daemon=True)
    t.start()

    # execute_dag with max_parallel=1: A 在飞，B 是 ready 但 slots=0 -> 等 A 完成
    # 最终应正常结束（A done 后 B 被派发到 done）
    execute_dag(engine, m, manifest_path, max_parallel=1)

    m2 = load_manifest(manifest_path)
    assert m2.nodes["A"].status == "done", f"A 应完成，实际 {m2.nodes['A'].status}"
    assert m2.nodes["B"].status == "done", f"B 应在 A 完成后被派发到 done，实际 {m2.nodes['B'].status}"
    # 关键：B 的 worker assign 发生在 A done 之后（slots=0 旁路保证 B 等 A 释放 slot）
    worker_assigns = [(dag, ts) for _, dag, role, ts in engine.assign_log if role == "worker"]
    a_ts = [ts for dag, ts in worker_assigns if dag == "A"]
    b_ts = [ts for dag, ts in worker_assigns if dag == "B"]
    assert a_ts and b_ts, f"应有 A 和 B 的 worker assign 记录: {worker_assigns}"
    assert b_ts[0] > a_ts[0], "B 应在 A 之后被派发（slots=0 等待旁路生效）"
    assert a_ts and b_ts, f"应有 A 和 B 的 worker assign 记录: {worker_assigns}"
    assert b_ts[0] > a_ts[0], "B 应在 A 之后被派发（slots=0 等待旁路生效）"


# ==================== reviewer 过渡旁路（半覆盖分支补齐）====================

# 11. worker DONE + has_pr + reviewer -> 过渡到 in_review (run_dag.py:105-108, 145-148)
def test_harvest_worker_done_with_reviewer_transitions_to_in_review(tmp_path, monkeypatch):
    """worker DONE + 有 PR + 有 reviewer -> assign reviewer + 转 in_review。"""
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path, reviewer="bob")
    engine = _make_mock_engine()
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    # 创建 item 并标 DONE + 设 artifacts（模拟 worker 完成）
    item = engine.create_work_item(
        workspace_id="sq", title="Task A", description="Test",
        dag_key="A", worker="alice", reviewer="bob",
    )
    engine.update_status(item.id, WorkItemStatus.DONE)
    engine.update_work_item_metadata(item.id, artifacts={"pr": "https://mock.example.com/pr/1"})

    m = load_manifest(manifest_path)
    set_node(m, "A", work_item_id=item.id, status="in_progress")
    save_manifest(m, manifest_path)

    completed, failed = set(), set()
    _harvest(engine, m, manifest_path, completed, failed)

    assert m.nodes["A"].status == "in_review", f"应过渡到 in_review，实际 {m.nodes['A'].status}"
    assert "A" not in completed, "过渡到 review 不应直接进 completed"
    assert "A" not in failed, "过渡到 review 不应进 failed"
    # pending_review 执行后应调用 assign_work_item(reviewer) + update_status(IN_REVIEW)
    reviewer_assigns = [(dag, role) for _, dag, role, _ in engine.assign_log if role == "reviewer"]
    assert ("A", "reviewer") in reviewer_assigns, "应 assign reviewer"
    assert engine._work_items[item.id].status == WorkItemStatus.IN_REVIEW, (
        "平台 item 应被标 IN_REVIEW"
    )


# 12. in_review verdict=pass -> done (run_dag.py:131-135)
def test_harvest_in_review_pass_done(tmp_path, monkeypatch):
    """in_review 节点 verdict='pass' -> done + completed 集合含该 key。"""
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path, reviewer="bob")
    engine = _make_mock_engine()
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    # 创建 item，标 IN_REVIEW，设 review_verdict='pass'
    item = engine.create_work_item(
        workspace_id="sq", title="Task A", description="Test",
        dag_key="A", worker="alice", reviewer="bob",
    )
    engine.update_status(item.id, WorkItemStatus.IN_REVIEW)
    engine.update_work_item_metadata(item.id, review_verdict="pass", review_comment="LGTM")

    m = load_manifest(manifest_path)
    set_node(m, "A", work_item_id=item.id, status="in_review")
    save_manifest(m, manifest_path)

    completed, failed = set(), set()
    _harvest(engine, m, manifest_path, completed, failed)

    assert m.nodes["A"].status == "done", f"verdict=pass 应 done，实际 {m.nodes['A'].status}"
    assert "A" in completed, "pass 应进 completed"
    assert "A" not in failed, "pass 不应进 failed"
    assert engine._work_items[item.id].status == WorkItemStatus.DONE, (
        "harvest 应调 update_status(DONE)"
    )


# 13. pass-with-nits 也应批准 -> done (run_dag.py:131-135)
def test_harvest_in_review_pass_with_nits_done(tmp_path, monkeypatch):
    """in_review 节点 verdict='pass-with-nits' -> done（属批准集合）。"""
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path, reviewer="bob")
    engine = _make_mock_engine()
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    item = engine.create_work_item(
        workspace_id="sq", title="Task A", description="Test",
        dag_key="A", worker="alice", reviewer="bob",
    )
    engine.update_status(item.id, WorkItemStatus.IN_REVIEW)
    engine.update_work_item_metadata(item.id, review_verdict="pass-with-nits", review_comment="Good")

    m = load_manifest(manifest_path)
    set_node(m, "A", work_item_id=item.id, status="in_review")
    save_manifest(m, manifest_path)

    completed, failed = set(), set()
    _harvest(engine, m, manifest_path, completed, failed)

    assert m.nodes["A"].status == "done", f"pass-with-nits 应 done，实际 {m.nodes['A'].status}"
    assert "A" in completed


# 14. in_progress 节点平台状态为 BLOCKED -> blocked (run_dag.py _harvest BLOCKED 分支)
def test_harvest_worker_blocked_on_platform(tmp_path, monkeypatch):
    """in_progress 节点，平台回读 BLOCKED -> 标 blocked + failed.add。

    multica FAILED 映射到 "blocked" 后，真平台回读是 BLOCKED 而非 FAILED。
    _harvest 必须识别 BLOCKED 并隔离，否则节点卡在 in_progress 永远不被收割。
    """
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path)
    engine = _make_mock_engine()
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    item = engine.create_work_item(
        workspace_id="sq", title="Task A", description="Test",
        dag_key="A", worker="alice",
    )
    engine.update_status(item.id, WorkItemStatus.IN_PROGRESS)
    # 模拟真平台报 blocked（不经过 FAILED）
    engine.update_status(item.id, WorkItemStatus.BLOCKED)

    m = load_manifest(manifest_path)
    set_node(m, "A", work_item_id=item.id, status="in_progress")
    save_manifest(m, manifest_path)

    completed, failed = set(), set()
    _harvest(engine, m, manifest_path, completed, failed)

    assert m.nodes["A"].status == "blocked", f"BLOCKED 应 -> blocked，实际 {m.nodes['A'].status}"
    assert "A" in failed, "BLOCKED 应进 failed 集合"
    assert "A" not in completed
