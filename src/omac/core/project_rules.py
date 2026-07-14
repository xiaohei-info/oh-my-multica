"""把 planner 评审通过的项目规范合并到仓库根 AGENTS.md。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..errors import NeedsDecision, ValidationError
from ..i18n import ui


START_MARKER = "<!-- OMAC:PROJECT_RULES:START -->"
END_MARKER = "<!-- OMAC:PROJECT_RULES:END -->"


@dataclass(frozen=True)
class AgentsSnapshot:
    exists: bool
    content: str


def read_agents_snapshot(path: str = "AGENTS.md") -> AgentsSnapshot:
    target = Path(path)
    if not target.exists():
        return AgentsSnapshot(False, "")
    content = target.read_text(encoding="utf-8")
    _managed_bounds(content)
    return AgentsSnapshot(True, content)


def _managed_bounds(content: str) -> tuple[int, int] | None:
    starts = content.count(START_MARKER)
    ends = content.count(END_MARKER)
    if starts == 0 and ends == 0:
        return None
    if starts != 1 or ends != 1:
        raise ValidationError(ui(
            "AGENTS.md has incomplete or duplicate OMAC project-rules markers. "
            "Keep exactly one START/END pair, then retry.",
            "AGENTS.md 中 OMAC 项目规范标记不完整或重复。"
            "请保留且仅保留一组 START/END 标记后重试。"))
    start = content.index(START_MARKER)
    end = content.index(END_MARKER)
    if end < start:
        raise ValidationError(ui(
            "AGENTS.md has reversed OMAC project-rules markers. Put START before END, then retry.",
            "AGENTS.md 中 OMAC 项目规范标记顺序反了。请将 START 放在 END 前再重试。"))
    return start, end + len(END_MARKER)


def merge_project_rules(content: str, project_rules: str) -> str:
    rules = project_rules.strip()
    if not rules:
        raise ValidationError(ui(
            "Project rules are empty; the planner must submit a non-empty --project-rules-file.",
            "项目规范为空；planner 必须通过 --project-rules-file 提交非空内容。"))
    block = f"{START_MARKER}\n{rules}\n{END_MARKER}"
    bounds = _managed_bounds(content)
    if bounds is not None:
        start, end = bounds
        return content[:start] + block + content[end:]
    if not content:
        return block + "\n"
    separator = "\n" if content.endswith("\n") else "\n\n"
    return content + separator + block + "\n"


def write_project_rules(
    project_rules: str,
    snapshot: AgentsSnapshot,
    path: str = "AGENTS.md",
) -> None:
    target = Path(path)
    exists = target.exists()
    current = target.read_text(encoding="utf-8") if exists else ""
    if exists != snapshot.exists or current != snapshot.content:
        raise NeedsDecision(ui(
            "AGENTS.md changed while the plan was running. OMAC did not overwrite it. "
            "Review the changes, then run `omac plan resume --plan-id <id>`.",
            "plan 运行期间 AGENTS.md 已发生变化，OMAC 未覆盖该文件。"
            "请检查改动后运行 `omac plan resume --plan-id <id>`。"),
            report={"path": path, "reason": "agents_changed_during_plan"})
    target.write_text(
        merge_project_rules(current, project_rules), encoding="utf-8")
