"""guide — 知识分发 topic 文本(随包分发的 markdown)。"""
from __future__ import annotations

from importlib import resources
from pathlib import PurePosixPath

from ..i18n import CN, EN, resolve_language

TOPICS = ("workflow", "roles", "recovery")
ROLE_TOPICS = ("planner", "orchestrator", "worker", "reviewer", "acceptor")
ARTIFACT_TOPICS = ("design", "acceptance", "manifest", "evidence")


def _read_markdown(path: PurePosixPath, language: str) -> str:
    language = resolve_language({"language": language})
    if language == EN:
        path = PurePosixPath(EN) / path
    return (resources.files(__package__) / str(path)).read_text(encoding="utf-8")


def load_topic(name: str, *, language: str = CN) -> str:
    if name not in TOPICS:
        raise ValueError(f"unknown guide topic: {name}")
    return _read_markdown(PurePosixPath(f"{name}.md"), language)


def load_role_topic(name: str, *, language: str = CN) -> str:
    if name not in ROLE_TOPICS:
        raise ValueError(f"unknown guide role topic: {name}")
    return _read_markdown(PurePosixPath("roles") / f"{name}.md", language)


def load_artifact_topic(name: str, *, language: str = CN) -> str:
    if name not in ARTIFACT_TOPICS:
        raise ValueError(f"unknown guide artifact topic: {name}")
    return _read_markdown(PurePosixPath("artifacts") / f"{name}.md", language)
