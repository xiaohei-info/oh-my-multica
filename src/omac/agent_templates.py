"""仓库内置 Agent 模板发现与校验。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import yaml

from .engines.models import SkillPackage
from .errors import ValidationError
from .i18n import EN, resolve_language, ui


SkillTemplate = SkillPackage


@dataclass(frozen=True)
class AgentTemplate:
    id: str
    path: Path
    instructions: str
    skills: List[SkillTemplate]


def _default_root() -> Path:
    return Path(__file__).resolve().parent / "agents"


def _read_frontmatter(path: Path, language: str = EN) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValidationError(ui(
            f"Skill is missing YAML frontmatter: {path}",
            f"Skill 缺少 YAML frontmatter:{path}", language=language))
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValidationError(ui(
            f"Skill frontmatter is not closed: {path}",
            f"Skill frontmatter 未闭合:{path}", language=language))
    try:
        data = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as exc:
        raise ValidationError(ui(
            f"Invalid Skill frontmatter: {path}: {exc}",
            f"Skill frontmatter 无效:{path} —— {exc}", language=language)) from exc
    if not isinstance(data, dict):
        raise ValidationError(ui(
            f"Skill frontmatter must be a mapping: {path}",
            f"Skill frontmatter 必须是对象:{path}", language=language))
    return data


class AgentTemplateCatalog:
    """按目录约定读取模板；不包含 Runtime 或 OMAC 角色限制。"""

    def __init__(self, root: Optional[Path] = None, language: str = EN):
        self.root = Path(root) if root is not None else _default_root()
        self.language = resolve_language({"language": language})

    def _instruction_file(self, directory: Path) -> Path:
        localized = directory / f"instructions.{self.language}.md"
        if localized.is_file():
            return localized
        return directory / "instructions.md"

    def list_ids(self) -> List[str]:
        if not self.root.is_dir():
            raise ValidationError(ui(
                f"Agent template directory not found: {self.root}",
                f"Agent 模板目录不存在:{self.root}", language=self.language))
        return sorted(
            path.name for path in self.root.iterdir()
            if path.is_dir() and not path.name.startswith("_")
        )

    def get(self, template_id: str) -> AgentTemplate:
        if not template_id or Path(template_id).name != template_id:
            raise ValidationError(ui(
                f"Invalid Agent template name: {template_id!r}",
                f"非法 Agent 模板名:{template_id!r}", language=self.language))
        template_path = self.root / template_id
        if not template_path.is_dir() or template_path.name.startswith("_"):
            raise ValidationError(ui(
                f"Unknown Agent template '{template_id}'. Available: {', '.join(self.list_ids())}",
                f"未知 Agent 模板 '{template_id}',可选:{', '.join(self.list_ids())}",
                language=self.language))

        role_file = self._instruction_file(template_path)
        if not role_file.is_file():
            raise ValidationError(ui(
                f"Agent template is missing Instructions: {template_path}",
                f"Agent 模板缺少 Instructions:{template_path}", language=self.language))
        instruction_parts = []
        shared_file = self._instruction_file(self.root / "_shared")
        if shared_file.is_file():
            instruction_parts.append(shared_file.read_text(encoding="utf-8").strip())
        instruction_parts.append(role_file.read_text(encoding="utf-8").strip())
        instructions = "\n\n".join(part for part in instruction_parts if part)
        if not instructions:
            raise ValidationError(ui(
                f"Agent template Instructions are empty: {template_path}",
                f"Agent 模板 Instructions 为空:{template_path}", language=self.language))

        skills = []
        skills_path = template_path / "skills"
        if skills_path.exists() and not skills_path.is_dir():
            raise ValidationError(ui(
                f"Agent template skills must be a directory: {skills_path}",
                f"Agent 模板 skills 必须是目录:{skills_path}", language=self.language))
        if skills_path.is_dir():
            for skill_path in sorted(skills_path.iterdir()):
                if skill_path.is_symlink():
                    raise ValidationError(ui(
                        f"Skill symlinks are not allowed: {skill_path}",
                        f"Skill 不允许符号链接:{skill_path}", language=self.language))
                if not skill_path.is_dir():
                    continue
                skill_md = skill_path / "SKILL.md"
                if not skill_md.is_file():
                    raise ValidationError(ui(
                        f"Skill is missing SKILL.md: {skill_path}",
                        f"Skill 缺少 SKILL.md:{skill_path}", language=self.language))
                metadata = _read_frontmatter(skill_md, self.language)
                name = str(metadata.get("name") or "").strip()
                if not name:
                    raise ValidationError(ui(
                        f"Skill frontmatter is missing name: {skill_md}",
                        f"Skill frontmatter 缺少 name:{skill_md}", language=self.language))
                if name != skill_path.name:
                    raise ValidationError(ui(
                        f"Skill directory and name differ: {skill_path.name!r} != {name!r}",
                        f"Skill 目录名与 name 不一致:{skill_path.name!r} != {name!r}",
                        language=self.language))
                files = []
                for path in sorted(skill_path.rglob("*")):
                    if path.is_symlink():
                        raise ValidationError(ui(
                            f"Skill symlinks are not allowed: {path}",
                            f"Skill 不允许符号链接:{path}", language=self.language))
                    if path.is_file():
                        files.append(path)
                skills.append(SkillTemplate(
                    name=name,
                    description=str(metadata.get("description") or "").strip(),
                    path=skill_path,
                    files=tuple(files),
                ))
        return AgentTemplate(
            id=template_id,
            path=template_path,
            instructions=instructions,
            skills=skills,
        )
