"""Acceptance document and configuration rules of the final-acceptance loop (pure).

The loop itself (rounds, increments, the two acceptance tasks and their real `work submit` paths) runs on Docker Multica
with scripted agents: tests/it/test_it_acceptance_loop.py.
"""
import pytest

from omac.core.acceptance import load_acceptance_doc
from omac.core.manifest import Contract, Manifest, Node
from omac.errors import NeedsDecision
from omac.pipeline.acceptance import _resolve_operation_branch, acceptance_doc_path, resolve_acceptance_config


def test_acceptance_doc_rejects_one_generic_expected_reused_across_most_actions():
    actions = [
        {
            "step": f"执行不同业务动作 {i}",
            "how": f"在页面 {i} 执行动作",
            "expected": "证明本 Action step 中的每个子句成立，否则失败。",
        }
        for i in range(40)
    ]

    with pytest.raises(ValueError, match="expected.*reused by 40/40 actions"):
        load_acceptance_doc({
            "flows": [{"id": "flow-generic", "name": "通用模板", "actions": actions}],
        })


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
