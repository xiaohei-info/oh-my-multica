"""源链接(source linkage)与五阶段投影 —— Stage 4 schema/validators。

两类纯校验/投影,不改 pipeline 行为:
  - validate_source_linkage:manifest meta.source 人工看板指针(project + issue),
    error-list 校验器(空列表 = 合法),与 evidence 校验器同风格;
  - project_stage:平台状态 → 五阶段(intake/plan/build/verify/done)确定性投影。
    blocked/failed 是异常态,不是生命周期阶段 → ValidationError(exit 5,
    报错即教学)。不涉及 label。
"""
from __future__ import annotations

from ..errors import ValidationError
from ..i18n import ui

# 五阶段生命周期投影(确定性,无 label 参与)
STAGES = ("intake", "plan", "build", "verify", "done")

STATUS_TO_STAGE = {
    "backlog": "intake",
    "todo": "plan",
    "in_progress": "build",
    "in_review": "verify",
    "done": "done",
}

_SOURCE_KEYS = ("project", "issue")


def validate_source_linkage(meta: dict) -> list[str]:
    """校验 manifest meta.source(人工看板指针)。

    source 可选;存在时必须是映射,且恰好包含非空字符串键
    project 与 issue(未知键报错)。返回失败消息列表;空列表 = 合法。
    """
    source = meta.get("source") if isinstance(meta, dict) else None
    if source is None:
        return []
    if not isinstance(source, dict):
        return [f"meta.source must be a mapping (project/issue); got {type(source).__name__}"]
    errors = []
    for key in _SOURCE_KEYS:
        value = source.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"meta.source.{key} must be a non-empty string")
    for key in source:
        if key not in _SOURCE_KEYS:
            errors.append(f"meta.source has unknown key: {key}")
    return errors


def project_stage(status: str) -> str:
    """把平台状态确定性投影到五阶段之一。

    未知状态(含 blocked/failed)→ ValidationError(exit 5):
    blocked/failed 是异常态,不是生命周期阶段,投影时必须显式处理,
    不允许悄悄归入某个阶段。
    """
    stage = STATUS_TO_STAGE.get(status)
    if stage is not None:
        return stage
    valid = ", ".join(STATUS_TO_STAGE)
    raise ValidationError(ui(
        f"Unknown status {status!r}; cannot project to a lifecycle stage. "
        "blocked/failed are exception states, not lifecycle stages — handle "
        f"them explicitly instead of projecting. Valid statuses: {valid}",
        f"未知状态 {status!r},无法投影到生命周期阶段。"
        "blocked/failed 是异常态,不是生命周期阶段 —— 请显式处理,不要投影。"
        f"合法状态: {valid}"))
