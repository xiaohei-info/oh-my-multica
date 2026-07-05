"""guide — 知识分发 topic 文本(随包分发的 markdown)。"""
from __future__ import annotations

from importlib import resources

TOPICS = ("workflow", "manifest", "roles", "worker", "reviewer", "recovery")


def load_topic(name: str) -> str:
    if name not in TOPICS:
        raise ValueError(f"unknown guide topic: {name}")
    return (resources.files(__package__) / f"{name}.md").read_text(encoding="utf-8")
