"""Stage 4(切片:schema/config + validators)RED 测试:
源链接(manifest meta.source)、机器隔离配置(machine 块)、五阶段投影。

语义:
  - validate_source_linkage:error-list 校验器,空列表 = 合法;
  - resolve_machine:machine 块缺省为 {project: None, namespace: None}
    (无机器隔离);只设其一 → ValidationError(exit 5,隔离必须成对);
  - project_stage:状态 → 五阶段确定性投影;blocked/failed 等异常态不是
    生命周期阶段 → ValidationError(exit 5,报错即教学)。
"""
from __future__ import annotations

import pytest

from omac.core.config import DEFAULT_MACHINE, resolve_machine
from omac.core.linkage import (
    STAGES,
    STATUS_TO_STAGE,
    project_stage,
    validate_source_linkage,
)
from omac.errors import ValidationError


# ── A. validate_source_linkage ───────────────────────────────────────────

def test_source_absent_is_valid():
    assert validate_source_linkage({}) == []
    assert validate_source_linkage({"name": "demo"}) == []


def test_source_none_is_valid():
    assert validate_source_linkage({"source": None}) == []


def test_source_valid():
    assert validate_source_linkage(
        {"source": {"project": "atlas", "issue": "AT-123"}}) == []


@pytest.mark.parametrize("source", [
    "atlas/AT-123",        # 非映射
    ["atlas", "AT-123"],
    42,
    {"project": "atlas"},                    # 缺 issue
    {"issue": "AT-123"},                     # 缺 project
    {},                                      # 两者都缺
    {"project": "", "issue": "AT-123"},      # 空字符串
    {"project": "atlas", "issue": "  "},     # 纯空白
    {"project": 42, "issue": "AT-123"},      # 非字符串
    {"project": "atlas", "issue": "AT-123", "repo": "x"},  # 未知键
])
def test_source_rejected(source):
    errors = validate_source_linkage({"source": source})
    assert errors, f"expected errors for {source!r}"
    assert all(isinstance(e, str) for e in errors)


def test_source_reports_one_error_per_problem():
    errors = validate_source_linkage({"source": {}})
    assert len(errors) == 2  # project 与 issue 各一条


# ── B. resolve_machine ───────────────────────────────────────────────────

def test_machine_defaults_no_isolation():
    assert resolve_machine({}) == {"project": None, "namespace": None}
    assert DEFAULT_MACHINE == {"project": None, "namespace": None}


def test_machine_configured():
    resolved = resolve_machine(
        {"machine": {"project": "atlas", "namespace": "team-a"}})
    assert resolved == {"project": "atlas", "namespace": "team-a"}


@pytest.mark.parametrize("raw", [
    {"machine": "atlas"},                             # 非映射
    {"machine": {"project": "atlas"}},                # 只设 project
    {"machine": {"namespace": "team-a"}},             # 只设 namespace
    {"machine": {"project": "", "namespace": "team-a"}},   # 空字符串
    {"machine": {"project": "atlas", "namespace": 42}},    # 非字符串
    {"machine": {"project": True, "namespace": "team-a"}}, # 布尔非字符串
])
def test_machine_rejects_malformed(raw):
    with pytest.raises(ValidationError) as exc:
        resolve_machine(raw)
    assert exc.value.exit_code == 5


# ── C. 五阶段投影 ────────────────────────────────────────────────────────

def test_stages_tuple():
    assert STAGES == ("intake", "plan", "build", "verify", "done")


def test_status_to_stage_mapping():
    assert STATUS_TO_STAGE == {
        "backlog": "intake",
        "todo": "plan",
        "in_progress": "build",
        "in_review": "verify",
        "done": "done",
    }
    assert set(STATUS_TO_STAGE.values()) == set(STAGES)


@pytest.mark.parametrize("status,stage", list(STATUS_TO_STAGE.items()))
def test_project_stage_known(status, stage):
    assert project_stage(status) == stage
    assert stage in STAGES


@pytest.mark.parametrize("status", [
    "blocked",     # 异常态,不是生命周期阶段
    "failed",      # 异常态,不是生命周期阶段
    "merged",      # 未定义状态
    "",
    "TODO",        # 大小写敏感
])
def test_project_stage_unknown_rejected(status):
    with pytest.raises(ValidationError) as exc:
        project_stage(status)
    assert exc.value.exit_code == 5
    # 报错即教学:列出合法状态
    message = str(exc.value)
    for known in STATUS_TO_STAGE:
        assert known in message


def test_project_stage_teaches_blocked_is_exception_state():
    with pytest.raises(ValidationError) as exc:
        project_stage("blocked")
    assert "blocked" in str(exc.value)
