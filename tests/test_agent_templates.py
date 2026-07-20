"""Agent 模板目录发现与内容校验。"""
import re
from pathlib import Path

import pytest

from omac.agent_templates import AgentTemplateCatalog
from omac.errors import ValidationError


def _write_skill(root: Path, name: str) -> None:
    skill = root / name
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} description\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    (skill / "references" / "guide.md").write_text("guide\n", encoding="utf-8")


def test_catalog_combines_shared_and_role_instructions_and_discovers_full_skills(tmp_path):
    root = tmp_path / "agents"
    (root / "_shared").mkdir(parents=True)
    (root / "_shared" / "instructions.md").write_text("shared rules\n", encoding="utf-8")
    role = root / "worker"
    (role / "skills").mkdir(parents=True)
    (role / "instructions.md").write_text("worker rules\n", encoding="utf-8")
    _write_skill(role / "skills", "quality")

    catalog = AgentTemplateCatalog(root)

    assert catalog.list_ids() == ["worker"]
    template = catalog.get("worker")
    assert template.instructions == "shared rules\n\nworker rules"
    assert [skill.name for skill in template.skills] == ["quality"]
    assert template.skills[0].description == "quality description"
    assert [p.relative_to(template.skills[0].path).as_posix()
            for p in template.skills[0].files] == ["SKILL.md", "references/guide.md"]


def test_catalog_selects_english_or_chinese_instruction_mirror(tmp_path):
    root = tmp_path / "agents"
    shared = root / "_shared"
    role = root / "worker"
    shared.mkdir(parents=True)
    role.mkdir()
    (shared / "instructions.md").write_text("共享规则\n", encoding="utf-8")
    (shared / "instructions.en.md").write_text("shared rules\n", encoding="utf-8")
    (role / "instructions.md").write_text("工作规则\n", encoding="utf-8")
    (role / "instructions.en.md").write_text("worker rules\n", encoding="utf-8")

    assert AgentTemplateCatalog(root, language="en").get("worker").instructions == (
        "shared rules\n\nworker rules")
    assert AgentTemplateCatalog(root, language="cn").get("worker").instructions == (
        "共享规则\n\n工作规则")


def test_catalog_rejects_skill_without_skill_md(tmp_path):
    root = tmp_path / "agents"
    (root / "_shared").mkdir(parents=True)
    (root / "_shared" / "instructions.md").write_text("shared\n", encoding="utf-8")
    role = root / "worker"
    (role / "skills" / "broken").mkdir(parents=True)
    (role / "instructions.md").write_text("worker\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="SKILL.md"):
        AgentTemplateCatalog(root).get("worker")


def test_catalog_rejects_skill_symlink(tmp_path):
    root = tmp_path / "agents"
    (root / "_shared").mkdir(parents=True)
    (root / "_shared" / "instructions.md").write_text("shared\n", encoding="utf-8")
    role = root / "worker"
    (role / "skills").mkdir(parents=True)
    (role / "instructions.md").write_text("worker\n", encoding="utf-8")
    _write_skill(role / "skills", "quality")
    outside = tmp_path / "outside.md"
    outside.write_text("private\n", encoding="utf-8")
    (role / "skills" / "quality" / "outside.md").symlink_to(outside)

    with pytest.raises(ValidationError, match="symlinks"):
        AgentTemplateCatalog(root).get("worker")


def test_repo_catalog_uses_agents_directory_directly_under_omac_package():
    catalog = AgentTemplateCatalog()

    assert catalog.root == Path(__file__).resolve().parents[1] / "src" / "omac" / "agents"


def test_repo_catalog_contains_native_profile_templates_with_skill_counts():
    catalog = AgentTemplateCatalog()

    expected = {
        "architect": 40,
        "backend-eng": 13,
        "data-rd": 0,
        "frontend-eng": 13,
        "orchestrator": 0,
        "pm": 7,
        "reviewer": 0,
    }
    assert catalog.list_ids() == sorted(expected)
    assert {template_id: len(catalog.get(template_id).skills)
            for template_id in expected} == expected
    assert {"acceptor", "backend", "frontend", "planner", "worker"}.isdisjoint(expected)
    for template_id in expected:
        template = catalog.get(template_id)
        assert template.instructions
        assert "OMAC" in template.instructions
        assert not (template.path / "AGENTS.md").exists()


def test_repo_templates_keep_full_generic_profile_rules_without_runtime_bindings():
    expected_roles = {
        "architect": "Architect",
        "backend-eng": "Backend Engineer",
        "data-rd": "Data RD",
        "frontend-eng": "Frontend Engineer",
        "orchestrator": "Orchestrator",
        "pm": "PM",
        "reviewer": "Reviewer",
    }

    forbidden_runtime_text = (
        "角色叠加层",
        "本 SOUL",
        "Hermes",
        "/home/ubuntu/",
        "oh-my-openagent",
        "Codex ACP",
        "ACP coding agent",
        "当前加载的 orchestration skill",
    )

    chinese_catalog = AgentTemplateCatalog(language="cn")
    english_catalog = AgentTemplateCatalog(language="en")
    for template_id, role_name in expected_roles.items():
        template_path = chinese_catalog.get(template_id).path
        assert (template_path / "instructions.md").is_file()
        assert (template_path / "instructions.en.md").is_file()

        chinese_role = (template_path / "instructions.md").read_text(encoding="utf-8")
        english_role = (template_path / "instructions.en.md").read_text(encoding="utf-8")
        assert not re.search(r"[\u3400-\u9fff]", english_role)
        assert len(english_role.splitlines()) >= len(chinese_role.splitlines()) * 0.9

        chinese = chinese_catalog.get(template_id).instructions
        assert f"# {role_name}" in chinese
        assert "# 通用规约" in chinese
        assert "# 风险边界" in chinese
        assert "# 工具与协作偏好" in chinese
        assert "# 输出纪律" in chinese

        english = english_catalog.get(template_id).instructions
        assert f"# {role_name}" in english
        assert "# General rules" in english
        assert "# Risk boundaries" in english
        assert "# Tool and collaboration preferences" in english
        assert "# Output discipline" in english

        for instructions in (chinese, english):
            for forbidden in forbidden_runtime_text:
                assert forbidden not in instructions


def test_orchestrator_template_is_standalone_and_has_no_profile_migration_notes():
    for language in ("cn", "en"):
        instructions = AgentTemplateCatalog(language=language).get("orchestrator").instructions
        assert "v3.0" not in instructions
        assert "看板编排员" not in instructions
        assert "kanban-dev-orchestration" not in instructions
        assert "parallel-dev-orchestration-multica" not in instructions
        assert "execution mechanism is provided by" not in instructions.lower()


def test_repo_templates_require_complete_delivery_and_real_business_tests():
    chinese = AgentTemplateCatalog(language="cn")
    english = AgentTemplateCatalog(language="en")

    for template_id in ("backend-eng", "frontend-eng", "data-rd"):
        cn = chinese.get(template_id).instructions
        en = english.get(template_id).instructions
        for item in ("真实业务行为", "骨架", "占位", "假数据"):
            assert item in cn, f"{template_id} missing Chinese quality rule: {item}"
        for item in ("real business behavior", "skeleton", "placeholder", "synthetic data"):
            assert item in en.lower(), f"{template_id} missing English quality rule: {item}"


def test_reviewer_template_requires_one_complete_review_pass():
    chinese = AgentTemplateCatalog(language="cn").get("reviewer").instructions
    english = AgentTemplateCatalog(language="en").get("reviewer").instructions.lower()

    for item in ("发现第一个 blocker 后继续", "完整 diff", "一次性报告"):
        assert item in chinese, f"reviewer missing Chinese complete-review rule: {item}"
    for item in (
        "continue after finding the first blocker",
        "complete diff",
        "report all issues in one review",
    ):
        assert item in english, f"reviewer missing English complete-review rule: {item}"
