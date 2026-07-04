"""guide 六 topic 走查:可加载、非空、无 skill 残留、omac 命令口径。"""
from __future__ import annotations

import pytest

from omac.cli.commands.dag import DESCRIPTION as DAG_DESC
from omac.cli.commands.work import DESCRIPTION as WORK_DESC
from omac.cli.commands.node import DESCRIPTION as NODE_DESC
from omac.guide import TOPICS, load_topic


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


@pytest.mark.parametrize("topic", sorted(TOPICS))
def test_topic_loadable_and_nonempty(topic: str) -> None:
    content = load_topic(topic)
    assert content.strip(), f"topic {topic} is empty"
    assert "#" in content, f"topic {topic} has no markdown headers"


@pytest.mark.parametrize("topic", sorted(TOPICS))
def test_topic_no_skill_residue(topic: str) -> None:
    _assert_no_residue(load_topic(topic), f"guide/{topic}.md")


@pytest.mark.parametrize("topic", sorted(TOPICS))
def test_topic_uses_omac_commands(topic: str) -> None:
    content = load_topic(topic)
    # 至少出现一次 omac 命令引用
    assert "omac " in content, f"topic {topic} never references omac commands"


def test_manifest_topic_has_methodology() -> None:
    content = load_topic("manifest")
    for heading in ["核心信念", "防跑偏", "七道防跑偏闸", "两级拆解", "依赖三原则"]:
        assert heading in content, f"manifest missing methodology section: {heading}"


def test_worker_topic_has_checklist() -> None:
    content = load_topic("worker")
    for item in ["契约先行", "TDD", "verification", "env_setup"]:
        assert item in content, f"worker missing: {item}"


def test_reviewer_topic_has_verdict() -> None:
    content = load_topic("reviewer")
    for item in ["收活铁律", "pass", "report", "blockers"]:
        assert item in content, f"reviewer missing: {item}"


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


def test_workflow_topic_has_lifecycle() -> None:
    content = load_topic("workflow")
    for item in ["omac init", "omac plan", "omac dag run", "exit 20"]:
        assert item in content, f"workflow missing lifecycle reference: {item}"


def test_roles_topic_has_all_roles() -> None:
    content = load_topic("roles")
    for role in ["planner", "orchestrator", "reviewer", "worker", "acceptor"]:
        assert role in content, f"roles missing role: {role}"


# --- P3.2 迁移补齐的额外守卫(reader blocker fix) ---


def test_worker_has_when_to_use() -> None:
    """何时用 / 不用 必须出现在 worker 协议中。"""
    content = load_topic("worker")
    for item in ["何时用", "不适用", "适用场景", "判断标准"]:
        assert item in content, f"worker missing when-to-use section: {item}"


def test_reviewer_has_when_to_use() -> None:
    content = load_topic("reviewer")
    for item in ["何时用", "不适用", "适用场景"]:
        assert item in content, f"reviewer missing when-to-use section: {item}"


def test_worker_has_issue_body_识别表() -> None:
    """issue body / 派发载荷完整结构表必须出现在 worker 协议中。"""
    content = load_topic("worker")
    for item in ["定位表", "必消费契约", "红线", "验收", "测试落点", "执行协议"]:
        assert item in content, f"worker missing issue body field: {item}"


def test_reviewer_has_issue_body_识别表() -> None:
    content = load_topic("reviewer")
    for item in ["定位表", "必消费契约", "红线", "验收", "测试落点", "唯一口径"]:
        assert item in content, f"reviewer missing issue body field: {item}"


def test_worker_has_env_assumptions() -> None:
    content = load_topic("worker")
    for item in ["环境假设", "集成分支", "Python", "Git"]:
        assert item in content, f"worker missing env assumption: {item}"


def test_reviewer_has_env_assumptions() -> None:
    content = load_topic("reviewer")
    for item in ["环境假设", "集成分支", "env_setup", "只读"]:
        assert item in content, f"reviewer missing env assumption: {item}"


def test_workflow_has_dispatch_template() -> None:
    """dispatch body 模板必须出现在 workflow guide 中(P2.3 同源锚点)。"""
    content = load_topic("workflow")
    for item in ["派发 body 模板", "dispatch", "Worker 派发", "Reviewer 派发", "Architect 派发"]:
        assert item in content, f"workflow missing dispatch template: {item}"


def test_dispatch_template_uses_omac_commands() -> None:
    """dispatch 模板必须使用 omac 命令口径(不能残留 agent_cli)。"""
    content = load_topic("workflow")
    dispatch_area = content[content.index("派发 body 模板"):]
    assert "agent_cli" not in dispatch_area, "dispatch template still references agent_cli(wrong tool)"
    assert "omac work submit" in dispatch_area, "dispatch template missing omac work submit"


def test_dispatch_no_skill_residue() -> None:
    _assert_no_residue(load_topic("workflow"), "guide/workflow.md")
