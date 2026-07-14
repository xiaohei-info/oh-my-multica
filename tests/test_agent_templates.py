"""Agent 模板目录发现与内容校验。"""
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


def test_repo_catalog_contains_nine_templates_with_multica_skill_counts():
    catalog = AgentTemplateCatalog()

    expected = {
        "planner": 40,
        "orchestrator": 0,
        "worker": 13,
        "reviewer": 0,
        "acceptor": 7,
        "architect": 40,
        "backend": 13,
        "frontend": 13,
        "pm": 7,
    }
    assert catalog.list_ids() == sorted(expected)
    assert {template_id: len(catalog.get(template_id).skills)
            for template_id in expected} == expected
    for template_id in expected:
        template = catalog.get(template_id)
        assert template.instructions
        assert "OMAC" in template.instructions
        assert not (template.path / "AGENTS.md").exists()
