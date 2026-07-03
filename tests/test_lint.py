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


def test_contract_hard_gates():
    contract = Contract(objective=None, acceptance=[], non_goals=[],
                        verification_commands=[], integration_gates=[], pr_base=None)
    errs = lint(_manifest(_node("a", contract=contract)), POOL)
    joined = "\n".join(errs)
    for needle in ("objective", "acceptance", "non_goals",
                   "verification_commands", "integration_gates", "pr_base"):
        assert needle in joined
