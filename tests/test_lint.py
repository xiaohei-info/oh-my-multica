"""core.lint:成员池、依赖引用、reviewer 规则、contract 硬门、环检测。"""
from omac.core.lint import lint
from omac.core.manifest import Contract, Manifest, Node

POOL = {"alice", "bob"}


def _node(id, worker="alice", **kw):
    return Node(id=id, worker=worker, **kw)


def _manifest(*nodes):
    return Manifest(meta={}, nodes={n.id: n for n in nodes})


def test_clean_manifest_passes():
    errs = lint(_manifest(_node("a"), _node("b", worker="bob", blocked_by=["a"])), POOL)
    assert errs == []


def test_worker_not_in_pool():
    errs = lint(_manifest(_node("a", worker="ghost")), POOL)
    assert any("not in agent pool" in e for e in errs)


def test_unknown_dependency():
    errs = lint(_manifest(_node("a", blocked_by=["nope"])), POOL)
    assert any("unknown node" in e for e in errs)


def test_reviewer_must_differ():
    errs = lint(_manifest(_node("a", reviewer="alice")), POOL)
    assert any("reviewer must differ" in e for e in errs)


def test_cycle_detected():
    errs = lint(_manifest(_node("a", blocked_by=["b"]), _node("b", worker="bob", blocked_by=["a"])), POOL)
    assert any("cycle" in e for e in errs)


def test_declared_closeout_node_must_exist():
    manifest = _manifest(_node("a"))
    manifest.meta["closeout_node"] = "closeout"

    errs = lint(manifest, POOL)

    assert any("closeout_node" in e and "closeout" in e for e in errs)


def test_contract_hard_gates():
    contract = Contract(objective=None, acceptance=[], non_goals=[],
                        verification_commands=[], integration_gates=[], pr_base=None)
    errs = lint(_manifest(_node("a", contract=contract)), POOL)
    joined = "\n".join(errs)
    for needle in ("objective", "acceptance", "non_goals",
                   "verification_commands", "integration_gates", "pr_base",
                   "source_of_truth"):
        assert needle in joined


def _valid_contract(**over):
    """过 lint 的最小合法契约(每个硬门都满足)。"""
    base = dict(
        objective="实现 X", acceptance=["A 工作"], non_goals=["不做 Y"],
        source_of_truth=["docs/design.md#x"],
        verification_commands=["pytest -q"],
        integration_gates=[{
            "name": "g1", "layer": "L1", "delivery_goal": "d",
            "source_of_truth": ["docs/design.md#x"], "covers": ["route"],
            "acceptance_refs": ["A 工作"], "commands": ["pytest tests/int"],
        }],
        pr_base="feature/v1")
    base.update(over)
    return Contract(**base)


def test_source_of_truth_required_for_contract():
    """契约必须带实现层设计指针(source_of_truth),否则 worker 只能脑补设计。"""
    contract = _valid_contract(source_of_truth=[])
    errs = lint(_manifest(_node("a", contract=contract)), POOL)
    assert any("source_of_truth" in e for e in errs)


def test_valid_contract_passes_all_gates():
    """回归:补全 source_of_truth 的完整契约应零报错(硬门不误伤合法节点)。"""
    errs = lint(_manifest(_node("a", contract=_valid_contract())), POOL)
    assert errs == []
