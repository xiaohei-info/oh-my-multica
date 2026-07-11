"""guide 分层 topic 走查:可加载、非空、无 skill 残留、omac 命令口径。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from omac.cli.commands.dag import DESCRIPTION as DAG_DESC
from omac.cli.commands.node import DESCRIPTION as NODE_DESC
from omac.cli.commands.work import DESCRIPTION as WORK_DESC
from omac.core.evidence import validate_review_evidence
from omac.core.manifest import Contract, Node
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
    for item in ["TDD", "contract.source_of_truth", "上游 issue", "deliverable/ref", "verification", "pr_base", "non_goals"]:
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


def test_scope_paths_are_non_exhaustive_primary_ownership_guidance() -> None:
    topics = {
        "orchestrator": load_role_topic("orchestrator"),
        "worker": load_role_topic("worker"),
        "reviewer": load_role_topic("reviewer"),
        "manifest": load_artifact_topic("manifest"),
    }
    for name, content in topics.items():
        assert "主要代码归属范围" in content, f"{name} missing primary scope guidance"
        assert "必要配套文件" in content, f"{name} missing supporting-file guidance"
    assert "不穷举" in topics["orchestrator"]
    assert "不是穷举文件白名单" in topics["manifest"]
    assert "PR 或 verification" in topics["worker"]
    assert "不因必要配套文件" in topics["reviewer"]


def test_authoring_guides_address_low_reasoning_budget_workers() -> None:
    topics = [
        load_role_topic("planner"),
        load_role_topic("orchestrator"),
        load_artifact_topic("design"),
        load_artifact_topic("acceptance"),
        load_artifact_topic("manifest"),
    ]
    for content in topics:
        for item in ["低推理预算", "隐含上下文", "边界条件"]:
            assert item in content, f"guide missing low-reasoning-budget guidance: {item}"


def test_every_role_guide_is_an_executable_agent_protocol() -> None:
    required = [
        "适用条件", "指令优先级", "权威输入", "执行步骤", "完成条件",
        "返工路径", "阻塞与升级", "禁止事项", "错误写法", "交付",
    ]
    for topic in ROLE_TOPICS:
        content = load_role_topic(topic)
        for heading in required:
            assert heading in content, f"role {topic} missing protocol section: {heading}"


def test_every_artifact_guide_is_a_validator_oriented_contract() -> None:
    required = ["使用场景", "最小合法示例", "字段语义", "校验硬门", "常见错误", "提交"]
    for topic in ARTIFACT_TOPICS:
        content = load_artifact_topic(topic)
        for heading in required:
            assert heading in content, f"artifact {topic} missing contract section: {heading}"


def test_all_guides_pin_instance_first_instruction_precedence() -> None:
    for label, content in _all_topics():
        assert "work show" in content, f"{label} must point to instance facts"
        assert "实例事实" in content or "实例上下文" in content, (
            f"{label} must say static guide cannot override task instance")


def test_evidence_artifact_defines_all_evidence_shapes() -> None:
    content = load_artifact_topic("evidence")
    for item in ["worker verification", "reviewer report", "final acceptance results", "acceptance_mapping"]:
        assert item in content, f"evidence artifact missing evidence anchor: {item}"


def test_evidence_reviewer_example_passes_actual_validator() -> None:
    content = load_artifact_topic("evidence")
    reviewer_section = content.split("## reviewer report", 1)[1]
    report_yaml = reviewer_section.split("```yaml", 1)[1].split("```", 1)[0]
    report = yaml.safe_load(report_yaml)
    contract = Contract(
        acceptance=["flow-login"],
        integration_gates=[{
            "name": "auth-e2e",
            "source_of_truth": ["docs/design.md#auth-flow"],
            "delivery_goal": "登录主链路可用",
            "commands": ["python3 -m pytest tests/e2e/test_login.py"],
            "required_metrics": {},
            "artifacts": [],
        }],
    )
    node = Node(id="auth", worker="alice", contract=contract)
    item = SimpleNamespace(review_verdict="pass", review_report=report)

    assert validate_review_evidence(node, item) == []


def test_recovery_topic_has_decision_flow() -> None:
    content = load_topic("recovery")
    for item in ["exit 20", "node retry", "node abandon", "失败隔离"]:
        assert item in content, f"recovery missing: {item}"


def test_dag_help_has_hard_constraints() -> None:
    _assert_no_residue(DAG_DESC, "dag --help")
    for item in ["前台阻塞", "重试显式", "失败隔离", "manifest 唯一口径"]:
        assert item in DAG_DESC, f"dag help missing hard constraint: {item}"


def test_work_help_is_agent_first_router_not_protocol_dump() -> None:
    _assert_no_residue(WORK_DESC, "work --help")
    for item in ["Agent", "实例事实", "guide_refs", "默认输出 JSON"]:
        assert item in WORK_DESC, f"work help missing Agent-first contract: {item}"
    assert "只读共享态" not in WORK_DESC


def test_node_help_has_hard_constraints() -> None:
    _assert_no_residue(NODE_DESC, "node --help")
    for item in ["重试显式", "失败隔离", "防假收尾"]:
        assert item in NODE_DESC, f"node help missing hard constraint: {item}"
