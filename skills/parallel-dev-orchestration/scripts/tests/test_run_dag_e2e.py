"""
端到端测试（manifest 驱动）：对 mock 引擎跑一遍 start_new_run 完整链路。
真 multica CLI 的状态映射与命令面覆盖在 tests/test_run_dag_live_multica.py（gated by MULTICA_LIVE=1）。

断言：两层 DAG A->B 跑到全 done；manifest 节点回填 work_item_id、status=done；
幂等重跑已 done 且有 work_item_id 的节点 0 新建；blocked 重跑只重做该节点；reconcile 纠正状态。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import load_manifest, set_node, save_manifest
from engines import create_engine_from_config, WorkItemStatus
from run_dag import start_new_run, reconcile


# ==================== mock 引擎 helper ====================

def _make_mock_engine(state_dir):
    env = {
        "ENGINE_TYPE": "mock",
        "MOCK_WORKSPACE_ID": "ws",
        "MOCK_AUTO_COMPLETE": "true",
        "MOCK_AUTO_COMPLETE_DELAY": "0",
        "POLLING_INTERVAL": "1",
    }
    engine = create_engine_from_config("mock", "ws", **env)
    engine.config.polling_interval = 0.001  # 近即时但避免 elapsed+=0 死循环
    engine._members["sq"] = ["alice", "bob", "carol"]
    return engine


# ==================== shared manifest ====================

def _write_manifest(path):
    yaml_text = (
        "meta:\n"
        "  name: e2e-test\n"
        "  squad: sq\n"
        "nodes:\n"
        "  - id: A\n"
        "    worker: alice\n"
        "    title: Task A\n"
        "    description: 'Build backend for A'\n"
        "  - id: B\n"
        "    worker: bob\n"
        "    title: Task B\n"
        "    description: 'Build frontend for B'\n"
        "    blocked_by: [A]\n"
    )
    with open(path, "w") as f:
        f.write(yaml_text)


# ==================== tests ====================

def test_e2e_full_dag_done(tmp_path, monkeypatch):
    """两层 DAG A->B 跑到全 done；manifest 回填 work_item_id + status=done。"""
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path)

    engine = _make_mock_engine(str(tmp_path))
    import run_dag as rd
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    start_new_run(manifest_path, engine=engine)

    m = load_manifest(manifest_path)
    assert m.nodes["A"].status == "done", f"A 应 done，实际 {m.nodes['A'].status}"
    assert m.nodes["B"].status == "done", f"B 应 done，实际 {m.nodes['B'].status}"
    assert m.nodes["A"].work_item_id is not None, "A 应回填 work_item_id"
    assert m.nodes["B"].work_item_id is not None, "B 应回填 work_item_id"
    assert m.nodes["A"].work_item_id != m.nodes["B"].work_item_id, "A/B work_item_id 不同"


def test_e2e_idempotent_rerun(tmp_path, monkeypatch):
    """重跑已 done 且有 work_item_id 的节点：0 新建、精准 get_work_item。"""
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path)

    engine = _make_mock_engine(str(tmp_path))
    import run_dag as rd
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    # 第一遍
    start_new_run(manifest_path, engine=engine)
    m1 = load_manifest(manifest_path)
    a_id = m1.nodes["A"].work_item_id
    b_id = m1.nodes["B"].work_item_id

    count_before = len(engine._work_items)

    # 第二遍重跑
    start_new_run(manifest_path, engine=engine)

    count_after = len(engine._work_items)
    assert count_after == count_before, f"重跑不应新建 work item: before={count_before} after={count_after}"

    m2 = load_manifest(manifest_path)
    assert m2.nodes["A"].work_item_id == a_id, "A work_item_id 不变"
    assert m2.nodes["B"].work_item_id == b_id, "B work_item_id 不变"


def test_e2e_blocked_rerun_redoes_only_that(tmp_path, monkeypatch):
    """B 退回 blocked 重跑 -> 只重做 B，A 复用 done。"""
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path)

    engine = _make_mock_engine(str(tmp_path))
    import run_dag as rd
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    # 第一遍
    start_new_run(manifest_path, engine=engine)
    m1 = load_manifest(manifest_path)
    b_id = m1.nodes["B"].work_item_id
    assert m1.nodes["B"].status == "done"

    # 把 B 退回 blocked
    set_node(m1, "B", status="blocked")
    save_manifest(m1, manifest_path)

    # 平台侧也退回
    engine.update_status(b_id, WorkItemStatus.BLOCKED)

    # 第二遍
    start_new_run(manifest_path, engine=engine)

    m2 = load_manifest(manifest_path)
    assert m2.nodes["A"].status == "done", "A 保持 done"
    assert m2.nodes["B"].status == "done", f"重派后 B 应 done，实际 {m2.nodes['B'].status}"
    assert m2.nodes["B"].work_item_id == b_id, "B 复用原 work_item_id"


def test_e2e_reconcile_fixes_stale_status(tmp_path, monkeypatch):
    """reconcile：manifest 记 work_item_id 但 status 落后于平台（平台已 done）-> 补成 done。"""
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path)

    engine = _make_mock_engine(str(tmp_path))
    import run_dag as rd
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    # 第一遍跑到 done
    start_new_run(manifest_path, engine=engine)
    m1 = load_manifest(manifest_path)
    a_id = m1.nodes["A"].work_item_id
    assert m1.nodes["A"].status == "done"

    # 手改 manifest 让 status 落后
    set_node(m1, "A", status="todo")
    save_manifest(m1, manifest_path)

    # 只跑 reconcile，不跑 execute
    m2 = load_manifest(manifest_path)
    reconcile(engine, m2, manifest_path)

    m3 = load_manifest(manifest_path)
    assert m3.nodes["A"].status == "done", f"reconcile 应补成 done，实际 {m3.nodes['A'].status}"
    assert m3.nodes["A"].work_item_id == a_id, "work_item_id 不变"


def test_e2e_reconcile_clears_missing_work_item_id(tmp_path, monkeypatch):
    """reconcile：work_item_id 指向平台不存在的 item -> 清空待新建。"""
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path)

    engine = _make_mock_engine(str(tmp_path))
    import run_dag as rd
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    m = load_manifest(manifest_path)
    # 给 A 一个不存在的 work_item_id
    set_node(m, "A", work_item_id="99999", status="done")
    save_manifest(m, manifest_path)

    m2 = load_manifest(manifest_path)
    reconcile(engine, m2, manifest_path)

    m3 = load_manifest(manifest_path)
    assert m3.nodes["A"].work_item_id is None, "work_item_id 应被清空"
    assert m3.nodes["A"].status == "todo", "status 应被重置为 todo"


# ==================== 失败隔离 e2e ====================

def test_e2e_failure_isolation_terminates(tmp_path, monkeypatch):
    """A 失败 -> B 下游隔离标 blocked -> run 能终止（不 hang），digest 报告失败。

    之前 bug：downstream_of 算出的 blocked 节点不加入 failed，
    completed+failed>=total 永不成立 -> while True 死循环。
    """
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path)  # A -> B

    engine = _make_mock_engine(str(tmp_path))
    engine.set_fail_keys({"A"})  # A 模拟失败
    import run_dag as rd
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    start_new_run(manifest_path, engine=engine)

    m = load_manifest(manifest_path)
    assert m.nodes["A"].status == "blocked", f"A 应 blocked，实际 {m.nodes['A'].status}"
    assert m.nodes["B"].status == "blocked", f"B 应 blocked（失败隔离），实际 {m.nodes['B'].status}"
    # 关键：run 正常终止了（没 hang），否则 timeout 会 fail）


def test_e2e_fix_upstream_rerun_downstream_recovers(tmp_path, monkeypatch):
    """A 失败 -> B blocked -> 修好 A 重跑 -> A done 且 B 恢复执行到 done。

    之前 bug：reset 循环只把 blocked/failed 重置为 todo，漏了因上游失败被标 blocked 的下游 ->
    上游修好后重跑，B 永远停在 blocked 不会重跑。
    """
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path)  # A -> B

    engine = _make_mock_engine(str(tmp_path))
    import run_dag as rd
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    # 第一轮：A 失败 -> B blocked
    engine.set_fail_keys({"A"})
    start_new_run(manifest_path, engine=engine)

    m = load_manifest(manifest_path)
    assert m.nodes["A"].status == "blocked"
    assert m.nodes["B"].status == "blocked"

    # 修好 A：清掉 fail 注入
    engine.set_fail_keys(set())

    # 第二轮：重跑同一 manifest -> A 应 done，B 应从 blocked 恢复到 done
    start_new_run(manifest_path, engine=engine)

    m2 = load_manifest(manifest_path)
    assert m2.nodes["A"].status == "done", f"A 修好后应 done，实际 {m2.nodes['A'].status}"
    assert m2.nodes["B"].status == "done", f"B 应从 blocked 恢复到 done，实际 {m2.nodes['B'].status}"


# ==================== 接手在飞节点 e2e ====================

def test_e2e_harvest_inflight_node(tmp_path, monkeypatch):
    """接手在飞节点：manifest 记 A=in_progress（别的机器派的），reconcile 同步平台 done -> harvest 收割。

    之前 bug：reconcile 只同步 done 不同步 in_progress，接手方当 todo 重派 -> 双撞。
    且 execute_dag 没 harvest 分支，在飞节点变 done 后无人登记。
    """
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path)  # A -> B

    engine = _make_mock_engine(str(tmp_path))
    import run_dag as rd
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    # 模拟别的机器已建 A 的 work item 并派发到 in_progress
    item_a = engine.create_work_item(
        workspace_id="sq",
        title="Task A",
        description="Build backend for A",
        dag_key="A",
        worker="alice",
    )
    engine.update_status(item_a.id, WorkItemStatus.IN_PROGRESS)

    # 写 manifest：A 有 work_item_id + status=in_progress（接手方拉到的状态）
    m = load_manifest(manifest_path)
    set_node(m, "A", work_item_id=item_a.id, status="in_progress")
    save_manifest(m, manifest_path)

    # 平台上把 A 标 done + 写 artifacts（模拟 worker 在 B 机器跑时完成了）
    engine.update_status(item_a.id, WorkItemStatus.DONE)
    engine.update_work_item_metadata(item_a.id, artifacts={"pr": "https://mock.example.com/pr/1"})

    # 现在跑 start_new_run：reconcile 应同步 A=done，harvest 无需做（已是 done），B 正常派发到 done
    start_new_run(manifest_path, engine=engine)

    m2 = load_manifest(manifest_path)
    assert m2.nodes["A"].status == "done", f"A 应 done（reconcile 同步），实际 {m2.nodes['A'].status}"
    assert m2.nodes["A"].work_item_id == item_a.id, "A work_item_id 不变"
    assert m2.nodes["B"].status == "done", f"B 应 done，实际 {m2.nodes['B'].status}"


def test_e2e_reconcile_syncs_in_progress(tmp_path, monkeypatch):
    """reconcile 全量同步：平台 in_progress -> manifest 补成 in_progress（不当 todo 重派）。"""
    manifest_path = str(tmp_path / "dag.yaml")
    _write_manifest(manifest_path)

    engine = _make_mock_engine(str(tmp_path))
    import run_dag as rd
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    # 平台上建 A 并标 in_progress
    item_a = engine.create_work_item(
        workspace_id="sq",
        title="Task A",
        description="Build backend for A",
        dag_key="A",
        worker="alice",
    )
    engine.update_status(item_a.id, WorkItemStatus.IN_PROGRESS)

    # manifest 记 A 有 work_item_id 但 status=todo（过时）
    m = load_manifest(manifest_path)
    set_node(m, "A", work_item_id=item_a.id, status="todo")
    save_manifest(m, manifest_path)

    # 只跑 reconcile
    m2 = load_manifest(manifest_path)
    reconcile(engine, m2, manifest_path)

    m3 = load_manifest(manifest_path)
    assert m3.nodes["A"].status == "in_progress", (
        f"reconcile 应同步平台 in_progress，实际 {m3.nodes['A'].status}"
    )


# ==================== 并发派发 e2e ====================

def _write_parallel_manifest(path):
    """A、B 无依赖 + C 依赖 [A,B] — 构造并行 frontier。"""
    yaml_text = (
        "meta:\n"
        "  name: parallel-test\n"
        "  squad: sq\n"
        "nodes:\n"
        "  - id: A\n"
        "    worker: alice\n"
        "    title: Task A\n"
        "    description: 'Independent task A'\n"
        "  - id: B\n"
        "    worker: bob\n"
        "    title: Task Task B\n"
        "    description: 'Independent task B'\n"
        "  - id: C\n"
        "    worker: carol\n"
        "    title: Task C\n"
        "    description: 'Depends on A and B'\n"
        "    blocked_by: [A, B]\n"
    )
    with open(path, "w") as f:
        f.write(yaml_text)


def _make_mock_engine_slow(state_dir, delay=1):
    """Mock 引擎但 auto_complete_delay > 0，使外部能观察到并发 in_progress 窗口。"""
    env = {
        "ENGINE_TYPE": "mock",
        "MOCK_WORKSPACE_ID": "ws",
        "MOCK_AUTO_COMPLETE": "true",
        "MOCK_AUTO_COMPLETE_DELAY": str(delay),
        "POLLING_INTERVAL": "1",
    }
    engine = create_engine_from_config("mock", "ws", **env)
    engine.config.polling_interval = 0.05   # 快速轮询，但 delay 留出并发窗口
    engine._members["sq"] = ["alice", "bob", "carol"]
    engine.assign_log = []
    return engine


def test_e2e_parallel_dispatch_concurrent(tmp_path, monkeypatch):
    """A、B 无依赖 + C 依赖 [A,B]：A/B 并发启动（同一轮 dispatch），C 等两者 done 后才跑。"""
    manifest_path = str(tmp_path / "dag.yaml")
    _write_parallel_manifest(manifest_path)

    engine = _make_mock_engine_slow(str(tmp_path), delay=2)
    import run_dag as rd
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    start_new_run(manifest_path, engine=engine, max_parallel=4)

    m = load_manifest(manifest_path)
    assert m.nodes["A"].status == "done", f"A 应 done，实际 {m.nodes['A'].status}"
    assert m.nodes["B"].status == "done", f"B 应 done，实际 {m.nodes['B'].status}"
    assert m.nodes["C"].status == "done", f"C 应 done，实际 {m.nodes['C'].status}"

    # 验证 A/B worker 被同一轮 dispatch 派发（时间差极小）
    worker_assigns = [(dag, ts) for _, dag, role, ts in engine.assign_log if role == "worker"]
    a_ts = [ts for dag, ts in worker_assigns if dag == "A"]
    b_ts = [ts for dag, ts in worker_assigns if dag == "B"]
    assert a_ts and b_ts, f"应有两者的 worker assign 记录: {worker_assigns}"
    # 同一轮 dispatch 顺序执行，时间差应在 1s 以内（远小于 auto_complete_delay=2s）
    assert abs(a_ts[0] - b_ts[0]) < 1.0, (
        f"A/B 应同一轮并发派发，时间差 {abs(a_ts[0]-b_ts[0]):.3f}s 过大（串行了？）"
    )

    # C 的 worker assign 必须在 A、B 都 done 之后
    c_ts = [ts for dag, ts in worker_assigns if dag == "C"]
    assert c_ts, "C 应有 worker assign 记录"
    assert c_ts[0] > a_ts[0] + 1.5, (
        f"C 应在 A 完成后才派发，C ts={c_ts[0]:.3f} vs A ts={a_ts[0]:.3f}"
    )
    assert c_ts[0] > b_ts[0] + 1.5, (
        f"C 应在 B 完成后才派发，C ts={c_ts[0]:.3f} vs B ts={b_ts[0]:.3f}"
    )


def test_e2e_max_parallel_limits_concurrency(tmp_path, monkeypatch):
    """max_parallel=1 时 A、B 不并发：B 的 worker assign 必须在 A 完成之后。"""
    manifest_path = str(tmp_path / "dag.yaml")
    _write_parallel_manifest(manifest_path)

    engine = _make_mock_engine_slow(str(tmp_path), delay=2)
    import run_dag as rd
    monkeypatch.setattr(rd, "commit_manifest", lambda *a, **k: False)

    start_new_run(manifest_path, engine=engine, max_parallel=1)

    m = load_manifest(manifest_path)
    assert m.nodes["A"].status == "done", f"A 应 done，实际 {m.nodes['A'].status}"
    assert m.nodes["B"].status == "done", f"B 应 done，实际 {m.nodes['B'].status}"
    assert m.nodes["C"].status == "done", f"C 应 done，实际 {m.nodes['C'].status}"

    worker_assigns = [(dag, ts) for _, dag, role, ts in engine.assign_log if role == "worker"]
    a_ts = [ts for dag, ts in worker_assigns if dag == "A"]
    b_ts = [ts for dag, ts in worker_assigns if dag == "B"]
    assert a_ts and b_ts, f"应有两者的 worker assign 记录: {worker_assigns}"
    # max_parallel=1: B 必须在 A 完成（assign + delay）之后才被派发
    assert b_ts[0] > a_ts[0] + 1.5, (
        f"max_parallel=1 时 B 应在 A 完成后才派发，"
        f"B ts={b_ts[0]:.3f} vs A ts={a_ts[0]:.3f} (差 {b_ts[0]-a_ts[0]:.3f}s)"
    )
