"""guide 分层 topic 走查:可加载、非空、无 skill 残留、omac 命令口径。"""
from __future__ import annotations

import pytest

from omac.cli.commands.dag import DESCRIPTION as DAG_DESC
from omac.cli.commands.node import DESCRIPTION as NODE_DESC
from omac.cli.commands.work import DESCRIPTION as WORK_DESC
from omac.guide import (
    ARTIFACT_TOPICS,
    ROLE_TOPICS,
    TOPICS,
    load_artifact_topic,
    load_role_topic,
    load_topic,
)


# skill 时代残留表述(出现 = 迁移不完整)
SKILL_RESIDUENCE = [
    "multica skill import",
    "agent skills add",
    "run_dag.py",
    "agent_cli",
    "sync_to_executor",
    "harvest",
    "frontier",
    "depends_on",  # 依赖字段已统一为 blocked_by
    "squad",  # 已改为 config 角色池
    "install.sh",
    "scripts/",  # 随 skill 分发的脚本目录
]


def _assert_no_residue(text: str, label: str) -> None:
    for needle in SKILL_RESIDUENCE:
        assert needle not in text.lower(), f"{label} contains skill residue: {needle}"


def _all_topics():
    for topic in TOPICS:
        yield f"guide/{topic}.md", load_topic(topic)
    for topic in ROLE_TOPICS:
        yield f"guide/roles/{topic}.md", load_role_topic(topic)
    for topic in ARTIFACT_TOPICS:
        yield f"guide/artifacts/{topic}.md", load_artifact_topic(topic)


@pytest.mark.parametrize("label,content", list(_all_topics()))
def test_topic_loadable_and_nonempty(label: str, content: str) -> None:
    assert content.strip(), f"{label} is empty"
    assert "#" in content, f"{label} has no markdown headers"


@pytest.mark.parametrize("label,content", list(_all_topics()))
def test_topic_no_skill_residue(label: str, content: str) -> None:
    _assert_no_residue(content, label)


@pytest.mark.parametrize("label,content", list(_all_topics()))
def test_topic_uses_omac_commands(label: str, content: str) -> None:
    assert "omac " in content, f"{label} never references omac commands"


def test_workflow_topic_is_mechanism_only() -> None:
    content = load_topic("workflow")
    for item in ["omac init", "omac plan create", "omac dag run", "exit 20", "omac guide role planner"]:
        assert item in content, f"workflow missing lifecycle reference: {item}"
    for item in ["plan create/resume exit 0", "manifest:", "下一步: omac dag run"]:
        assert item in content, f"workflow missing plan-to-dag handoff guidance: {item}"
    assert "Worker 派发" not in content
    assert "Reviewer 派发" not in content


def test_roles_topic_is_index_not_protocol_dump() -> None:
    content = load_topic("roles")
    for role in ["planner", "orchestrator", "reviewer", "worker", "acceptor"]:
        assert role in content, f"roles missing role: {role}"
    assert "architect 不是第六个机制角色" in content
    assert "omac guide role planner" in content
    assert "完整执行清单" not in content


def test_planner_role_has_design_and_acceptance_protocol() -> None:
    content = load_role_topic("planner")
    for item in ["设计方案", "验收文档", "核心数据", "模块边界", "跨模块契约", "验收映射"]:
        assert item in content, f"planner missing design protocol: {item}"


def test_orchestrator_role_has_wave_decomposition() -> None:
    content = load_role_topic("orchestrator")
    for item in ["Wave 0", "Wave 1", "Wave 2", "blocked_by", "contract"]:
        assert item in content, f"orchestrator missing decomposition anchor: {item}"


def test_worker_role_has_tdd_and_evidence() -> None:
    content = load_role_topic("worker")
    for item in ["TDD", "contract.source_of_truth", "verification", "pr_base", "non_goals"]:
        assert item in content, f"worker missing execution anchor: {item}"


def test_reviewer_role_has_verdict_and_independent_checks() -> None:
    content = load_role_topic("reviewer")
    for item in ["独立复跑", "pass", "reject", "review_goals", "coverage"]:
        assert item in content, f"reviewer missing review anchor: {item}"


def test_acceptor_role_has_final_acceptance_protocol() -> None:
    content = load_role_topic("acceptor")
    for item in ["final-acceptance", "acceptance-results", "pass/fail", "notes"]:
        assert item in content, f"acceptor missing final acceptance anchor: {item}"


def test_design_artifact_defines_markdown_frontmatter_schema() -> None:
    content = load_artifact_topic("design")
    for item in ["schema: omac.design/v1", "Markdown", "核心数据", "模块边界", "风险与兼容性"]:
        assert item in content, f"design artifact missing schema anchor: {item}"


def test_acceptance_artifact_defines_flow_action_schema() -> None:
    content = load_artifact_topic("acceptance")
    for item in ["schema: omac.acceptance/v1", "flows", "actions", "step", "how", "expected"]:
        assert item in content, f"acceptance artifact missing schema anchor: {item}"


def test_manifest_artifact_connects_contract_to_runtime() -> None:
    content = load_artifact_topic("manifest")
    for item in ["source_of_truth", "acceptance", "non_goals", "verification_commands", "integration_gates", "pr_base"]:
        assert item in content, f"manifest artifact missing contract anchor: {item}"


def test_evidence_artifact_defines_all_evidence_shapes() -> None:
    content = load_artifact_topic("evidence")
    for item in ["worker verification", "reviewer report", "final acceptance results", "acceptance_mapping"]:
        assert item in content, f"evidence artifact missing evidence anchor: {item}"


def test_recovery_topic_has_decision_flow() -> None:
    content = load_topic("recovery")
    for item in ["exit 20", "node retry", "node abandon", "失败隔离"]:
        assert item in content, f"recovery missing: {item}"


def test_dag_help_has_hard_constraints() -> None:
    _assert_no_residue(DAG_DESC, "dag --help")
    for item in ["前台阻塞", "重试显式", "失败隔离", "manifest 唯一口径"]:
        assert item in DAG_DESC, f"dag help missing hard constraint: {item}"


def test_work_help_has_hard_constraints() -> None:
    _assert_no_residue(WORK_DESC, "work --help")
    for item in ["唯一写入口", "证据门", "收活铁律", "只读共享态"]:
        assert item in WORK_DESC, f"work help missing hard constraint: {item}"


def test_node_help_has_hard_constraints() -> None:
    _assert_no_residue(NODE_DESC, "node --help")
    for item in ["重试显式", "失败隔离", "防假收尾"]:
        assert item in NODE_DESC, f"node help missing hard constraint: {item}"
