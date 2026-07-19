"""P4.3 总控验收外层循环 + DAG 增量扩展 e2e(§7.6).

基于 mock 引擎完成端到端走查:
  - mock e2e:注入首轮 2 项 fail -> 增量 2 个 fix 节点 -> 次轮全 pass -> exit 0
  - 增量并入:原 done 节点复用、新节点正确依赖、manifest 落盘可续跑
  - max_rounds 耗尽路径 exit 20
"""
import os
import yaml

import pytest

from omac.core.acceptance import load_acceptance_doc
from omac.core.manifest import Contract, Manifest, Node, load_manifest, save_manifest
from omac.engines import create_engine
from omac.engines.mock import MockStore
from omac.engines.models import EngineConfig
from omac.errors import NeedsDecision
from omac.pipeline.acceptance import (
    acceptance_doc_path, resolve_acceptance_config, AcceptanceOutcome,
    run_acceptance_loop, _resolve_operation_branch,
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
        "a": Node(id="a", worker="alice", status="done", work_item_id="wi-a"),
        "b": Node(id="b", worker="bob", blocked_by=["a"], status="done", work_item_id="wi-b"),
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
                {"id": f"action-{i}", "step": f"step-{i}",
                 "how": f"how-{i}", "expected": f"exp-{i}"}
                for i in range(n)
            ],
        })
    return load_acceptance_doc({"flows": flow_objs})


def _write_doc(tmp_path, doc):
    doc_path = os.path.join(str(tmp_path), ".omac", "feature-x.acceptance.yaml")
    os.makedirs(os.path.dirname(doc_path), exist_ok=True)
    with open(doc_path, "w") as f:
        yaml.dump({"flows": [
            {"id": fl.id, "name": fl.name,
             "actions": [{"id": a.id, "step": a.step,
                           "how": a.how, "expected": a.expected}
                         for a in fl.actions]}
            for fl in doc.flows
        ]}, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return doc_path


# ── acceptance_doc_path / config ────────────────────────────────────

def test_doc_path_and_config():
    assert acceptance_doc_path("feature-x.yaml") == "feature-x.acceptance.yaml"
    assert acceptance_doc_path(".omac/feature-x.yaml") == \
        ".omac/feature-x.acceptance.yaml"
    cfg = resolve_acceptance_config(
        {"acceptance": {"max_rounds": 5}, "roles": {"acceptor": "alice"}})
    assert cfg.max_rounds == 5
    assert cfg.acceptor == "alice"


def test_resolve_operation_branch_from_node_contracts():
    manifest = Manifest(meta={}, nodes={
        "a": Node(id="a", worker="alice", contract=Contract(pr_base="main")),
        "b": Node(id="b", worker="bob", contract=Contract(pr_base="main")),
    })

    assert _resolve_operation_branch(manifest) == "main"


def test_resolve_operation_branch_rejects_missing_or_conflicting_values():
    missing = Manifest(meta={}, nodes={"a": Node(id="a", worker="alice")})
    with pytest.raises(NeedsDecision, match="pr_base"):
        _resolve_operation_branch(missing)

    conflicting = Manifest(meta={}, nodes={
        "a": Node(id="a", worker="alice", contract=Contract(pr_base="main")),
        "b": Node(id="b", worker="bob", contract=Contract(pr_base="release")),
    })
    with pytest.raises(NeedsDecision, match="multiple pr_base"):
        _resolve_operation_branch(conflicting)


def test_final_acceptance_issue_has_complete_authoring_context(tmp_path):
    path = str(tmp_path / "fx.yaml")
    manifest = Manifest(meta={
        "name": "Demo",
        "plan_id": "p-demo",
        "source_issues": ["plan-issue", "acceptance-issue", "decompose-issue"],
        "closeout_node": "closeout",
    }, nodes={
        "build": Node(
            id="build", worker="alice", status="done",
            contract=Contract(pr_base="main"), work_item_id="build-issue"),
        "closeout": Node(
            id="closeout", worker="bob", status="done", blocked_by=["build"],
            contract=Contract(pr_base="main"), work_item_id="closeout-issue"),
    })
    save_manifest(manifest, path)
    doc = _acceptance_doc([("ACC-001", "Login", 1)])
    engine = _engine()
    project = engine.store.create_project(
        "ws", "demo", repo_urls=["git@github.com:owner/demo.git"])
    engine.store.config.project_id = project.id
    MockStore.set_acceptance_behaviors({
        "final-acceptance-p-demo-r1": [
            {"id": "ACC-001", "status": "pass", "evidence": "ok"},
        ],
    }, {})

    outcome = run_acceptance_loop(engine, manifest, path, doc, {
        "roles": {"reviewers": REVIEWERS, "orchestrator": ORCHESTRATOR},
    })

    assert outcome.exit_code == 0
    item = next(
        item for item in engine.store.list_work_items("ws")
        if item.kind == TaskKind.FINAL_ACCEPTANCE
    )
    env = f"OMAC_ENGINE=mock OMAC_WORKSPACE_ID=ws OMAC_PROJECT_ID={project.id}"
    assert env in item.description
    assert "PR base: `main`" in item.description
    assert f"omac work show {item.id} --output json" in item.description
    assert "omac work submit" not in item.description
    assert "git@github.com:owner/demo.git" in item.description
    assert "Final implementation delivery" in item.description
    assert item.contract["acceptance_doc"]["flows"][0]["id"] == "ACC-001"
    assert item.contract["repo_urls"] == ["git@github.com:owner/demo.git"]
    assert item.source_refs[-1]["issue_id"] == "closeout-issue"
    assert "acceptance_doc:" not in item.description


def test_incremental_decompose_issue_has_failed_flow_and_manifest_context(tmp_path):
    path = str(tmp_path / "fx.yaml")
    manifest = Manifest(meta={
        "name": "Demo",
        "source_issues": ["plan-issue", "acceptance-issue", "decompose-issue"],
    }, nodes={
        "a": Node(
            id="a", worker="alice", status="done",
            contract=Contract(pr_base="main"), work_item_id="work-a"),
    })
    save_manifest(manifest, path)
    doc = _acceptance_doc([("ACC-001", "Login", 1)])
    engine = _engine()
    MockStore.set_acceptance_behaviors({
        "final-acceptance-r1": [
            {"id": "ACC-001", "status": "fail", "notes": "broken"},
        ],
        "final-acceptance-r2": [
            {"id": "ACC-001", "status": "pass", "evidence": "ok"},
        ],
    }, {
        "decompose-r1": Manifest(meta={}, nodes={
            "fix-login": Node(id="fix-login", worker="alice", blocked_by=["a"]),
        }),
    })

    outcome = run_acceptance_loop(engine, manifest, path, doc, {
        "roles": {"reviewers": REVIEWERS, "orchestrator": ORCHESTRATOR},
        "acceptance": {"max_rounds": 2},
    })

    assert outcome.exit_code == 0
    item = next(
        item for item in engine.store.list_work_items("ws")
        if item.kind == TaskKind.DECOMPOSE
    )
    assert item.contract["mode"] == "incremental"
    assert item.contract["failed_items"] == ["ACC-001"]
    assert isinstance(item.contract["manifest"], dict)
    assert item.source_refs[-1]["label"] == "Acceptance trigger · Round 1"
    assert "ACC-001" in item.description
    assert "当前 Manifest" not in item.description


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


def test_acceptance_loop_uses_manifest_plan_id_in_dag_key(tmp_path):
    path = str(tmp_path / "fx.yaml")
    manifest = _done_manifest(path)
    manifest.meta["plan_id"] = "p-1234abcd"
    doc = _acceptance_doc([("f1", "F1", 1)])
    _write_doc(tmp_path, doc)

    engine = _engine()
    MockStore.set_acceptance_behaviors({
        "final-acceptance-p-1234abcd-r1": [
            {"id": "f1", "status": "pass"},
        ],
    }, {})

    config = {
        "defaults": {"max_parallel": 4, "poll_interval": 0},
        "roles": {"reviewers": REVIEWERS, "orchestrator": ORCHESTRATOR},
    }
    outcome = run_acceptance_loop(engine, manifest, path, doc, config)
    assert outcome.exit_code == 0


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


# ── entry-level: real work submit 路径 ─────────────────────────────

import json as _json
from omac.engines.models import WorkItemStatus as _WIS
from omac.core.taskmeta import TaskKind
from omac.engines.store import WorkItemStore
from omac.pipeline import dispatch as dispatch_mod


def _decompose_store(tmp_path):
    """造一个 mock 引擎 + 一个 DECOMPOSE mock work item。"""
    eng = _engine()
    store = eng.store
    item = store.create_work_item(
        workspace_id="mock-workspace",
        title="decompose decompose-r1",
        description="payload",
        dag_key="decompose-r1",
        worker="bob",
        kind=TaskKind.DECOMPOSE,
    )
    return eng, store, item


def test_decompose_submit_real_path(tmp_path):
    """real work submit --manifest-file 路径:decompose authoring 落 IN_REVIEW + deliverable。"""
    eng, store, item = _decompose_store(tmp_path)
    existing = Manifest(meta={"pr_base": "feature/v1"}, nodes={
        "b": Node(id="b", worker="bob", blocked_by=["a"], status="done"),
    })
    manifest = Manifest(meta={}, nodes={
        "fix-b": Node(id="fix-b", worker="alice", blocked_by=["b"]),
    })
    mpath = str(tmp_path / "increment.yaml")
    save_manifest(manifest, mpath)

    # 直接在真实 store 上调 dispatch.submit(manifest_file=..., base_manifest=existing)
    result = dispatch_mod.submit(
        store, item.id, manifest_file=mpath,
        agent_pool={"alice", "bob"}, base_manifest=existing,
    )
    assert result.kind == TaskKind.DECOMPOSE
    assert result.advanced_to == _WIS.IN_REVIEW

    updated = store.get_work_item(item.id)
    assert updated.status == _WIS.IN_REVIEW
    assert updated.deliverable is not None
    # deliverable 是 manifest 文本,可被解析
    parsed = yaml.safe_load(updated.deliverable)
    assert any(n["id"] == "fix-b" for n in parsed["nodes"])


def test_decompose_submit_rejects_unknown_blocked_by(tmp_path):
    """decompose authoring 引用不存在节点 → 校验失败,状态不动。"""
    eng, store, item = _decompose_store(tmp_path)
    manifest = Manifest(meta={}, nodes={
        "fix-b": Node(id="fix-b", worker="alice", blocked_by=["nonexistent"]),
    })
    mpath = str(tmp_path / "bad.yaml")
    save_manifest(manifest, mpath)

    with pytest.raises(Exception):
        dispatch_mod.submit(
            store, item.id, manifest_file=mpath,
            agent_pool={"alice"}, base_manifest=None,
        )
    # 失败不应推进状态
    assert store.get_work_item(item.id).status == _WIS.TODO


def _final_acceptance_store(tmp_path):
    eng = _engine()
    store = eng.store
    doc = _acceptance_doc([("f1", "F1", 1), ("f2", "F2", 1)])
    acceptance_doc_raw = {
        "flows": [
            {"id": f.id, "name": f.name,
             "actions": [{"id": a.id, "step": a.step,
                           "how": a.how, "expected": a.expected}
                         for a in f.actions]}
            for f in doc.flows
        ]
    }
    item = store.create_work_item(
        workspace_id="mock-workspace",
        title="final-acceptance final-acceptance-r1",
        description="payload",
        dag_key="final-acceptance-r1",
        worker="charlie",
        kind=TaskKind.FINAL_ACCEPTANCE,
    )
    store.set_node_contract(item.id, {"acceptance_doc": acceptance_doc_raw})
    return eng, store, item, acceptance_doc_raw


def test_final_acceptance_submit_real_path(tmp_path):
    """real work submit --acceptance-results-file 路径(contract 挂 acceptance_doc 才能过校验)。"""
    eng, store, item, _ = _final_acceptance_store(tmp_path)
    results = [
        {"id": "f1", "status": "pass", "evidence": "ok"},
        {"id": "f2", "status": "pass", "evidence": "ok"},
    ]
    rpath = str(tmp_path / "results.json")
    with open(rpath, "w") as f:
        _json.dump(results, f)

    result = dispatch_mod.submit(
        store, item.id, acceptance_results_file=rpath,
    )
    assert result.kind == TaskKind.FINAL_ACCEPTANCE
    assert result.advanced_to == _WIS.DONE

    updated = store.get_work_item(item.id)
    assert updated.status == _WIS.DONE
    assert updated.deliverable is not None
    parsed = yaml.safe_load(updated.deliverable)
    assert isinstance(parsed, list)
    assert parsed[0]["id"] == "f1"


def test_final_acceptance_submit_requires_contract(tmp_path):
    """final-acceptance × authoring 无 acceptance_doc contract → submit 报错(Blocker 1 验证)。"""
    eng = _engine()
    store = eng.store
    # 故意不挂 contract
    item = store.create_work_item(
        workspace_id="mock-workspace",
        title="final-acceptance no-contract",
        description="payload",
        dag_key="final-acceptance-x",
        worker="charlie",
        kind=TaskKind.FINAL_ACCEPTANCE,
    )
    results = [{"id": "f1", "status": "pass"}]
    rpath = str(tmp_path / "results.json")
    with open(rpath, "w") as f:
        _json.dump(results, f)

    with pytest.raises(Exception) as exc:
        dispatch_mod.submit(store, item.id, acceptance_results_file=rpath)
    assert "acceptance_doc" in str(exc.value)
