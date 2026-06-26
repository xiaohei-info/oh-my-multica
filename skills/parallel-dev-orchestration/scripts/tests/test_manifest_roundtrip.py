"""
manifest roundtrip 测试：save_manifest -> load_manifest 保真（work_item_id/status/依赖/body 不丢）；
set_node 只改指定字段。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import load_manifest, save_manifest, set_node, Manifest
from core.manifest import Node


def test_roundtrip_preserves_all_fields(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(
        "meta:\n"
        "  name: rt-test\n"
        "  squad: dev team\n"
        "  integration_branch: feature/v1\n"
        "nodes:\n"
        "  - id: M0\n"
        "    worker: agent-be\n"
        "    title: Backend task\n"
        "    description: 'Full issue body for M0'\n"
        "  - id: M1\n"
        "    worker: agent-fe\n"
        "    blocked_by: [M0]\n"
        "    title: Frontend task\n"
        "    description: 'Full issue body for M1'\n"
        "    reviewer: agent-rev\n"
        "    risk: high\n"
        "    gate:\n"
        "      min_reviews: 1\n"
        "    contract:\n"
        "      objective: Implement frontend task\n"
        "      source_of_truth:\n"
        "        - docs/design.md#frontend\n"
        "      required_contracts:\n"
        "        - skills/parallel-dev-orchestration/scripts/tests/test_manifest_roundtrip.py\n"
        "      acceptance:\n"
        "        - UI renders API result\n"
        "      non_goals:\n"
        "        - Do not rewrite backend\n"
        "      verification_commands:\n"
        "        - pytest tests/frontend\n"
        "      pr_base: feature/v1\n"
        "      coverage_gate: 91\n"
        "    work_item_id: '42'\n"
        "    status: done\n"
    )
    m = load_manifest(str(p))

    # 验证 load 后字段齐全
    assert m.nodes["M0"].worker == "agent-be"
    assert m.nodes["M0"].description == "Full issue body for M0"
    assert m.nodes["M0"].work_item_id is None
    assert m.nodes["M0"].status == "todo"

    assert m.nodes["M1"].blocked_by == ["M0"]
    assert m.nodes["M1"].reviewer == "agent-rev"
    assert m.nodes["M1"].risk == "high"
    assert m.nodes["M1"].gate == {"min_reviews": 1}
    assert m.nodes["M1"].contract.objective == "Implement frontend task"
    assert m.nodes["M1"].contract.required_contracts == ["skills/parallel-dev-orchestration/scripts/tests/test_manifest_roundtrip.py"]
    assert m.nodes["M1"].contract.coverage_gate == 91
    assert m.nodes["M1"].work_item_id == "42"
    assert m.nodes["M1"].status == "done"

    # save 回去
    save_manifest(m, str(p))
    m2 = load_manifest(str(p))

    # 验证 roundtrip 保真
    assert m2.nodes["M0"].worker == "agent-be"
    assert m2.nodes["M0"].description == "Full issue body for M0"
    assert m2.nodes["M0"].work_item_id is None
    assert m2.nodes["M0"].status == "todo"

    assert m2.nodes["M1"].blocked_by == ["M0"]
    assert m2.nodes["M1"].reviewer == "agent-rev"
    assert m2.nodes["M1"].risk == "high"
    assert m2.nodes["M1"].gate == {"min_reviews": 1}
    assert m2.nodes["M1"].contract.objective == "Implement frontend task"
    assert m2.nodes["M1"].contract.source_of_truth == ["docs/design.md#frontend"]
    assert m2.nodes["M1"].contract.acceptance == ["UI renders API result"]
    assert m2.nodes["M1"].contract.non_goals == ["Do not rewrite backend"]
    assert m2.nodes["M1"].contract.verification_commands == ["pytest tests/frontend"]
    assert m2.nodes["M1"].contract.pr_base == "feature/v1"
    assert m2.nodes["M1"].contract.coverage_gate == 91
    assert m2.nodes["M1"].work_item_id == "42"
    assert m2.nodes["M1"].status == "done"

    # meta 也保真
    assert m2.meta["name"] == "rt-test"
    assert m2.meta["squad"] == "dev team"
    assert m2.meta["integration_branch"] == "feature/v1"


def test_set_node_only_changes_specified_fields(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(
        "meta:\n  squad: sq\nnodes:\n"
        "  - id: A\n    worker: alice\n    description: body-A\n"
        "    work_item_id: '10'\n    status: in_progress\n"
    )
    m = load_manifest(str(p))

    # 只改 status
    set_node(m, "A", status="done")
    assert m.nodes["A"].status == "done"
    assert m.nodes["A"].work_item_id == "10"  # 未动
    assert m.nodes["A"].worker == "alice"  # 未动
    assert m.nodes["A"].description == "body-A"  # 未动

    # 只改 work_item_id
    set_node(m, "A", work_item_id="99")
    assert m.nodes["A"].work_item_id == "99"
    assert m.nodes["A"].status == "done"  # 未动

    # 两者都改
    set_node(m, "A", work_item_id="100", status="blocked")
    assert m.nodes["A"].work_item_id == "100"
    assert m.nodes["A"].status == "blocked"


def test_set_node_unknown_key_raises(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("meta:\n  squad: sq\nnodes:\n  - id: A\n    worker: alice\n")
    m = load_manifest(str(p))
    try:
        set_node(m, "nonexistent", status="done")
        assert False, "应抛 KeyError"
    except KeyError:
        pass
