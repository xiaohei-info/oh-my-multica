"""Structured machine feedback stored outside platform metadata."""
from __future__ import annotations

import json
from typing import Any, Iterable

from ..i18n import ui


MACHINE_FEEDBACK_SCHEMA = "omac.machine-feedback/v1"


def build_machine_feedback(gate: str, errors: Iterable[str]) -> dict[str, Any]:
    items = [str(error) for error in errors]
    return {
        "schema": MACHINE_FEEDBACK_SCHEMA,
        "gate": gate,
        "error_count": len(items),
        "errors": items,
    }


def dump_machine_feedback(feedback: dict[str, Any]) -> str:
    return json.dumps(feedback, ensure_ascii=False, indent=2)


def parse_machine_feedback(source: str) -> dict[str, Any] | None:
    try:
        value = json.loads(source)
    except (TypeError, ValueError):
        return None
    return value if is_machine_feedback(value) else None


def is_machine_feedback(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    errors = value.get("errors")
    return (
        value.get("schema") == MACHINE_FEEDBACK_SCHEMA
        and isinstance(value.get("gate"), str)
        and bool(value.get("gate"))
        and isinstance(errors, list)
        and all(isinstance(error, str) for error in errors)
        and value.get("error_count") == len(errors)
    )


def machine_feedback_summary(item_id: str, feedback: dict[str, Any]) -> str:
    count = feedback["error_count"]
    return ui(
        f"Machine gate found {count} issue(s). The complete structured feedback "
        "is stored in an attachment, not truncated in metadata. Run "
        f"`omac work show {item_id} --output json` and read "
        "`context.machine_feedback` before resubmitting.",
        f"机器门发现 {count} 个问题。完整结构化反馈保存在附件中，metadata 中未做截断。"
        f"重新提交前请运行 `omac work show {item_id} --output json` 并读取 "
        "`context.machine_feedback`。",
    )
