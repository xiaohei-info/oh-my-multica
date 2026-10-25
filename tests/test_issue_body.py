"""Issue body rendering and the packaged smoke manifest: pure checks, no platform."""
from pathlib import Path

import pytest

from omac.core.manifest import Contract, Node, load_manifest
from omac.core.taskmeta import TaskKind
from omac.pipeline.dispatch import render_issue_body


@pytest.fixture()
def sample_contract() -> Contract:
    return Contract(
        objective="Exercise the data plane end-to-end.",
        acceptance=["work item created", "status can progress to done"],
        non_goals=["do not assign to a real agent", "do not wake any runtime"],
        verification_commands=["pytest", "--version"],
        pr_base="feature/live-smoke",
        coverage_gate=90,
    )


class TestBootstrapBody:
    def test_human_sections_and_single_agent_entry(self, sample_contract):
        node = Node(id="live-proto", worker="alice", reviewer="bob",
                    title="Proto node", contract=sample_contract)
        body = render_issue_body(node, sample_contract, TaskKind.DEVELOP,
                                 "ISSUE-REAL-1000")
        assert "omac work show ISSUE-REAL-1000 --output json" in body
        assert "omac work submit ISSUE-REAL-1000" not in body
        assert "omac guide" not in body
        assert "## 任务摘要" in body
        assert "## 完成标准" in body
        assert "## 非目标" in body
        assert "## 硬约束" not in body

    def test_pr_base_is_human_readable_without_reviewer_protocol(self, sample_contract):
        node = Node(id="live-proto-2", worker="alice", reviewer="carol",
                    title="Proto 2", contract=sample_contract)
        body = render_issue_body(node, sample_contract, TaskKind.DEVELOP, "ID")
        assert "PR 基线: `feature/live-smoke`" in body
        assert "carol" not in body


FIXTURES = Path(__file__).resolve().parent / "fixtures"
SMOKE_LIVE = FIXTURES / "smoke_live_manifest.yaml"


def test_smoke_live_manifest_loads():
    if not SMOKE_LIVE.exists():
        pytest.skip(f"fixture 不存在: {SMOKE_LIVE}")
    manifest = load_manifest(str(SMOKE_LIVE))
    assert "live-smoke-A" in manifest.nodes
    node = manifest.nodes["live-smoke-A"]
    assert node.worker
    assert node.contract is not None
    assert node.contract.objective
    assert node.contract.verification_commands
    assert node.contract.pr_base == "feature/live-smoke"
