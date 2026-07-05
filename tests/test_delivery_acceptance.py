"""P4.3 总控验收外层循环 + DAG 增量扩展 e2e(§7.6).

基于 mock 引擎完成端到端走查:
  - mock e2e:注入首轮 2 项 fail -> 增量 2 个 fix 节点 -> 次轮全 pass -> exit 0
  - 增量并入:原 done 节点复用、新节点正确依赖、manifest 落盘可续跑
  - max_rounds 耗尽路径 exit 20
"""
import os

import pytest

from omac.core.acceptance import load_acceptance_doc
from omac.core.manifest import Manifest, Node, load_manifest, save_manifest
from omac.engines import create_engine
from omac.engines.mock import MockStore
from omac.engines.models import EngineConfig
from omac.pipeline.acceptance import (
    acceptance_doc_path, resolve_acceptance_config, AcceptanceOutcome,
    run_acceptance_loop,
)


REVIEWERS = ["alice", "bob"]
ORCHESTRATOR = "bob"


def _engine(**extra):
    base = {"MOCK_AUTO_COMPLETE": "true", "MOCK_AUTO_COMPLETE_DELAY": "0"}
    base.update(extra)
    config = EngineConfig(engine_type="mock", workspace_id="ws", extra=base)
    eng = create_engine("mock", config)
    # conftest 的 autouse fixture 会把 delay 重置为 2;这里显式回到 0
    MockStore.set_auto_complete(enabled=True, delay=0)
    return eng


def _done_manifest(path):
    """2 节点、全部 done 的 manifest(模拟内层 loop 已收敛)."""
    m = Manifest(meta={"name": "feature-x", "pr_base": "feature/v1"}, nodes={
        "a": Node(id="a", worker="alice", status="done", work_item_id="1"),
        "b": Node(id="b", worker="bob", blocked_by=["a"], status="done", work_item_id="2"),
    })
    save_manifest(m, path)
    return m


def _acceptance_doc(flows):
    """flows: [(id, name, actions_count)]."""
    flow_objs = []
    for fid, name, n in flows:
        flow_objs.append({
            "id": fid, "name": name,
            "actions": [
                {"step": f"step-{i}", "how": f"how-{i}", "expected": f"exp-{i}"}
                for i in range(n)
            ],
        })
    return load_acceptance_doc({"flows": flow_objs})


def _write_doc(tmp_path, doc):
    doc_path = os.path.join(str(tmp_path), ".orchestrator", "feature-x.acceptance.yaml")
    os.makedirs(os.path.dirname(doc_path), exist_ok=True)
    import yaml
    with open(doc_path, "w") as f:
        yaml.dump({"flows": [
            {"id": fl.id, "name": fl.name,
             "actions": [{"step": a.step, "how": a.how, "expected": a.expected}
                         for a in fl.actions]}
            for fl in doc.flows
        ]}, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return doc_path


# ── acceptance_doc_path / config ────────────────────────────────────

def test_doc_path_and_config():
    assert acceptance_doc_path("feature-x.yaml") == "feature-x.acceptance.yaml"
    assert acceptance_doc_path(".orchestrator/feature-x.yaml") == \
        ".orchestrator/feature-x.acceptance.yaml"
    cfg = resolve_acceptance_config(
        {"acceptance": {"max_rounds": 5}, "roles": {"acceptor": "alice"}})
    assert cfg.max_rounds == 5
    assert cfg.acceptor == "alice"


# ── e2e: mock e2e 2 fails -> 2 fixes -> all pass -> exit 0 ─────────

def test_e2e_two_fails_then_pass(tmp_path):
    path = str(tmp_path / "feature-x.yaml")
    manifest = _done_manifest(path)

    doc = _acceptance_doc([
        ("login-flow", "Login", 2),
        ("export-flow", "Export", 2),
        ("search-flow", "Search", 2),
    ])
    _write_doc(tmp_path, doc)

    engine = _engine()

    # Round 1: 2 fails; Round 2: all pass
    accepted = {
        "final-acceptance-r1": [
            {"id": "login-flow", "status": "pass"},
            {"id": "export-flow", "status": "fail", "notes": "csv broken"},
            {"id": "search-flow", "status": "fail", "notes": "timeout"},
        ],
        "final-acceptance-r2": [
            {"id": "login-flow", "status": "pass"},
            {"id": "export-flow", "status": "pass"},
            {"id": "search-flow", "status": "pass"},
        ],
    }
    increments = {
        "decompose-r1": Manifest(meta={}, nodes={
            "fix-export": Node(id="fix-export", worker="alice", blocked_by=["b"]),
            "fix-search": Node(id="fix-search", worker="bob", blocked_by=["b"]),
        }),
    }
    MockStore.set_acceptance_behaviors(accepted, increments)

    config = {
        "defaults": {"max_parallel": 4, "poll_interval": 0},
        "roles": {"reviewers": REVIEWERS, "orchestrator": ORCHESTRATOR},
        "acceptance": {"max_rounds": 3},
    }

    outcome = run_acceptance_loop(engine, manifest, path, doc, config)

    assert outcome.exit_code == 0
    assert outcome.rounds == 2
    assert "fix-export" in manifest.nodes
    assert "fix-search" in manifest.nodes
    assert manifest.nodes["fix-export"].blocked_by == ["b"]
    # Original done nodes untouched
    assert manifest.nodes["a"].status == "done"
    assert manifest.nodes["b"].status == "done"
    # Fix nodes got completed by inner loop
    assert manifest.nodes["fix-export"].status == "done"
    assert manifest.nodes["fix-search"].status == "done"


# ── e2e: all pass first round ──────────────────────────────────────

def test_e2e_all_pass_first_round(tmp_path):
    path = str(tmp_path / "fx.yaml")
    manifest = _done_manifest(path)
    doc = _acceptance_doc([("f1", "F1", 1), ("f2", "F2", 1)])
    _write_doc(tmp_path, doc)

    engine = _engine()
    MockStore.set_acceptance_behaviors({
        "final-acceptance-r1": [
            {"id": "f1", "status": "pass"},
            {"id": "f2", "status": "pass"},
        ],
    }, {})

    config = {
        "defaults": {"max_parallel": 4, "poll_interval": 0},
        "roles": {"reviewers": REVIEWERS, "orchestrator": ORCHESTRATOR},
    }
    outcome = run_acceptance_loop(engine, manifest, path, doc, config)
    assert outcome.exit_code == 0
    assert outcome.rounds == 1


# ── max_rounds exhausted -> exit 20 ────────────────────────────────

def test_max_rounds_exhausted(tmp_path):
    path = str(tmp_path / "fx.yaml")
    manifest = _done_manifest(path)
    doc = _acceptance_doc([("f1", "F1", 1), ("f2", "F2", 1)])
    _write_doc(tmp_path, doc)

    engine = _engine()
    # Always fail f2
    accepted = {
        "final-acceptance-r1": [
            {"id": "f1", "status": "pass"},
            {"id": "f2", "status": "fail", "notes": "stale"},
        ],
        "final-acceptance-r2": [
            {"id": "f1", "status": "pass"},
            {"id": "f2", "status": "fail", "notes": "still stale"},
        ],
    }
    increments = {
        "decompose-r1": Manifest(meta={}, nodes={
            "fix-f2-r1": Node(id="fix-f2-r1", worker="alice", blocked_by=["b"]),
        }),
        "decompose-r2": Manifest(meta={}, nodes={
            "fix-f2-r2": Node(id="fix-f2-r2", worker="alice", blocked_by=["b"]),
        }),
    }
    MockStore.set_acceptance_behaviors(accepted, increments)

    config = {
        "defaults": {"max_parallel": 4, "poll_interval": 0},
        "roles": {"reviewers": REVIEWERS, "orchestrator": ORCHESTRATOR},
        "acceptance": {"max_rounds": 2},
    }
    outcome = run_acceptance_loop(engine, manifest, path, doc, config)
    assert outcome.exit_code == 20
    assert outcome.rounds == 2
    assert "f2" in outcome.failed_items


# ── incremental merge resumable ────────────────────────────────────

def test_increment_persisted_resumable(tmp_path):
    path = str(tmp_path / "fx.yaml")
    manifest = _done_manifest(path)
    doc = _acceptance_doc([("f1", "F1", 1), ("f2", "F2", 1)])
    _write_doc(tmp_path, doc)

    engine = _engine()
    MockStore.set_acceptance_behaviors({
        "final-acceptance-r1": [
            {"id": "f1", "status": "pass"},
            {"id": "f2", "status": "fail", "notes": "x"},
        ],
        "final-acceptance-r2": [
            {"id": "f1", "status": "pass"},
            {"id": "f2", "status": "pass"},
        ],
    }, {
        "decompose-r1": Manifest(meta={}, nodes={
            "fix-f2": Node(id="fix-f2", worker="alice", blocked_by=["b"]),
        }),
    })

    config = {
        "defaults": {"max_parallel": 4, "poll_interval": 0},
        "roles": {"reviewers": REVIEWERS, "orchestrator": ORCHESTRATOR},
        "acceptance": {"max_rounds": 3},
    }
    outcome = run_acceptance_loop(engine, manifest, path, doc, config)
    assert outcome.exit_code == 0

    # Reload from disk: fix node should persist (resumable)
    reloaded = load_manifest(path)
    assert "fix-f2" in reloaded.nodes
    assert reloaded.nodes["fix-f2"].status == "done"
    assert reloaded.nodes["fix-f2"].blocked_by == ["b"]


# ── no acceptance -> skip ──────────────────────────────────────────

def test_no_acceptance_skips(tmp_path):
    """no_acceptance=True -> skip, exit 0, no rounds, no doc needed."""
    path = str(tmp_path / "fx.yaml")
    manifest = _done_manifest(path)
    doc = _acceptance_doc([("f1", "F1", 1)])  # won't be used
    engine = _engine()
    config = {"defaults": {"poll_interval": 0}}
    outcome = run_acceptance_loop(
        engine, manifest, path, doc, config, no_acceptance=True)
    assert outcome.exit_code == 0
    assert outcome.rounds == 0
