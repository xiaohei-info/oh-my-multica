"""
幂等重跑测试（manifest 驱动）：同一个 manifest 用 mock 引擎跑两遍 start_new_run，
第二遍应当复用第一遍已 done 的节点（work_item_id 已回填 -> get_work_item 精准取，0 新建）。
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engines import create_engine_from_config, WorkItemStatus
from run_dag import start_new_run


import run_dag as rd

_real_commit = rd.commit_manifest
rd.commit_manifest = lambda *a, **k: False  # no-op for tests (no git repo)


def _write_manifest(path, members):
    yaml_text = (
        "meta:\n"
        "  name: idempotent-test\n"
        "  squad: sq\n"
        "nodes:\n"
        f"  - id: A\n"
        f"    worker: {members[0]}\n"
        f"    title: A\n"
        f"    description: task A\n"
        f"  - id: B\n"
        f"    worker: {members[1]}\n"
        f"    title: B\n"
        f"    description: task B\n"
        f"    blocked_by: [A]\n"
    )
    with open(path, "w") as f:
        f.write(yaml_text)


def test_rerun_reuses_done_nodes():
    """第二遍重跑：A/B 已 done + work_item_id 已回填 -> 0 个新建、直接精准取。"""
    with tempfile.TemporaryDirectory() as state_dir:
        env = {
            "ENGINE_TYPE": "mock",
            "MOCK_WORKSPACE_ID": "ws",
            "MOCK_AUTO_COMPLETE": "true",
            "MOCK_AUTO_COMPLETE_DELAY": "0",
            "POLLING_INTERVAL": "1",
        }
        engine = create_engine_from_config("mock", "ws", **env)
        engine.config.polling_interval = 0.001  # 近即时但避免 elapsed+=0 死循环
        engine._members["sq"] = ["alice", "bob"]

        manifest_path = os.path.join(state_dir, "dag.yaml")
        _write_manifest(manifest_path, ["alice", "bob"])

        # 第一遍
        start_new_run(manifest_path, engine=engine)

        ids_after_first = {it.id for it in engine._work_items.values()}
        assert len(ids_after_first) == 2, f"第一遍应建 2 个 work item，实际 {len(ids_after_first)}"

        # 确认第一遍两个都 done
        for key in ("A", "B"):
            node = engine._work_items
            # 通过 manifest 检查——重读 manifest 文件
        from core import load_manifest
        m = load_manifest(manifest_path)
        assert m.nodes["A"].status == "done", f"A 应 done，实际 {m.nodes['A'].status}"
        assert m.nodes["B"].status == "done", f"B 应 done，实际 {m.nodes['B'].status}"
        assert m.nodes["A"].work_item_id is not None, "A 应有 work_item_id"
        assert m.nodes["B"].work_item_id is not None, "B 应有 work_item_id"

        # 第二遍：同一 manifest 重跑
        start_new_run(manifest_path, engine=engine)

        ids_after_second = {it.id for it in engine._work_items.values()}
        assert ids_after_second == ids_after_first, (
            "第二遍重跑不应新建 work item！"
            f" 第一遍={ids_after_first} 第二遍={ids_after_second}"
        )


def test_rerun_with_blocked_node_redoes_only_that():
    """第一遍 A/B 都 done；把 B 退回 blocked 重跑 -> 只重做 B。"""
    with tempfile.TemporaryDirectory() as state_dir:
        env = {
            "ENGINE_TYPE": "mock",
            "MOCK_WORKSPACE_ID": "ws",
            "MOCK_AUTO_COMPLETE": "true",
            "MOCK_AUTO_COMPLETE_DELAY": "0",
            "POLLING_INTERVAL": "1",
        }
        engine = create_engine_from_config("mock", "ws", **env)
        engine.config.polling_interval = 0.001
        engine._members["sq"] = ["alice", "bob"]

        manifest_path = os.path.join(state_dir, "dag.yaml")
        _write_manifest(manifest_path, ["alice", "bob"])

        # 第一遍
        start_new_run(manifest_path, engine=engine)
        from core import load_manifest
        m1 = load_manifest(manifest_path)
        a_id = m1.nodes["A"].work_item_id
        b_id = m1.nodes["B"].work_item_id
        assert m1.nodes["A"].status == "done"
        assert m1.nodes["B"].status == "done"

        # leader 把 B 退回 blocked（改 manifest）
        from core import set_node, save_manifest
        set_node(m1, "B", status="blocked")
        save_manifest(m1, manifest_path)
        # 平台侧也退回 blocked
        engine.update_status(b_id, WorkItemStatus.BLOCKED)

        # 第二遍
        start_new_run(manifest_path, engine=engine)

        m2 = load_manifest(manifest_path)
        assert m2.nodes["A"].status == "done", "A 复用 done"
        assert m2.nodes["A"].work_item_id == a_id, "A work_item_id 不变"
        assert m2.nodes["B"].status == "done", f"重派后 B 应 done，实际 {m2.nodes['B'].status}"
        assert m2.nodes["B"].work_item_id == b_id, "B 复用原 work_item_id（不新建）"

        ids = {it.id for it in engine._work_items.values()}
        assert len(ids) == 2, f"重跑不应新建 work item，实际 {len(ids)} 个: {ids}"
