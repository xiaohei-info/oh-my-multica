"""Stage 4(切片:schema/config + validators)RED 测试:
Node.ui / Node.runner 契约校验 + manifest load/save 往返 + lint 接线。

语义:
  - ui / runner 块可选:缺省时 load 为 None、save 不写回(向后兼容,
    未启用适配的 manifest 字节行为不变);
  - validate_ui_contract / validate_runner_metadata:error-list 校验器
    (空列表 = 合法),与 evidence 校验器同风格;
  - load_manifest 不自动跑这两个校验器(向后兼容);lint 在块存在时接线;
  - runner V1 只校验与记录,不做 lease 强制执行;
    lease_expires_at 无 lease_holder → 报错(fail closed)。
"""
from __future__ import annotations

import pytest

from omac.core.lint import lint
from omac.core.manifest import (
    load_manifest,
    save_manifest,
    validate_runner_metadata,
    validate_ui_contract,
)

BASIC = """\
meta:
  name: demo
nodes:
  - id: a
    worker: alice
"""

WITH_BLOCKS = """\
meta:
  name: demo
nodes:
  - id: a
    worker: alice
    ui:
      design_doc: docs/design/a.md
      visual_reference: assets/a-mock.png
      desktop:
        viewport: "1440x900"
        screenshot: shots/a-desktop.png
      mobile:
        viewport: "390x844"
        screenshot: shots/a-mobile.png
      visual_acceptance: artifacts/visual/a.md
    runner:
      runner_class: gpu-heavy
      preferred_host: artemis
      lease_holder: worker-7
      expected_tip: "0123456789abcdef0123456789abcdef01234567"
      lease_expires_at: "2026-08-01T00:00:00Z"
"""

GOOD_UI = {
    "design_doc": "docs/design/a.md",
    "visual_reference": "assets/a-mock.png",
    "desktop": {"viewport": "1440x900", "screenshot": "shots/a-desktop.png"},
    "mobile": {"viewport": "390x844", "screenshot": "shots/a-mobile.png"},
    "visual_acceptance": "artifacts/visual/a.md",
}

GOOD_RUNNER = {
    "runner_class": "gpu-heavy",
    "preferred_host": "artemis",
    "actual_host": "artemis-2",
    "lease_holder": "worker-7",
    "expected_tip": "0123456789abcdef0123456789abcdef01234567",
    "lease_expires_at": "2026-08-01T00:00:00Z",
}


def _write(tmp_path, content, name="m.yaml"):
    p = tmp_path / name
    p.write_text(content)
    return str(p)


def _dump_text(manifest, tmp_path):
    path = str(tmp_path / "out.yaml")
    save_manifest(manifest, path)
    with open(path, encoding="utf-8") as f:
        return f.read()


# ── D/E. Node 字段 load/save 往返 ────────────────────────────────────────

def test_node_ui_runner_roundtrip(tmp_path):
    path = _write(tmp_path, WITH_BLOCKS)
    m = load_manifest(path)
    node = m.nodes["a"]
    assert node.ui == GOOD_UI
    assert node.runner == {
        "runner_class": "gpu-heavy",
        "preferred_host": "artemis",
        "lease_holder": "worker-7",
        "expected_tip": "0123456789abcdef0123456789abcdef01234567",
        "lease_expires_at": "2026-08-01T00:00:00Z",
    }
    save_manifest(m, path)
    m2 = load_manifest(path)
    assert m2.nodes["a"].ui == node.ui
    assert m2.nodes["a"].runner == node.runner


def test_blocks_default_to_none_and_dump_omits_them(tmp_path):
    """向后兼容:未启用适配的 manifest 不含 ui/runner,dump 也不写回。"""
    m = load_manifest(_write(tmp_path, BASIC))
    assert m.nodes["a"].ui is None
    assert m.nodes["a"].runner is None
    text = _dump_text(m, tmp_path)
    assert "ui:" not in text
    assert "runner:" not in text


def test_partial_blocks_roundtrip(tmp_path):
    """ui / runner 相互独立:只给一个时另一个仍为 None 且不写回。"""
    content = BASIC.replace(
        "    worker: alice\n",
        "    worker: alice\n    runner:\n      runner_class: default\n")
    m = load_manifest(_write(tmp_path, content))
    assert m.nodes["a"].runner == {"runner_class": "default"}
    assert m.nodes["a"].ui is None
    text = _dump_text(m, tmp_path)
    assert "runner_class: default" in text
    assert "ui:" not in text


# ── D. validate_ui_contract ─────────────────────────────────────────────

def test_ui_contract_valid():
    assert validate_ui_contract(GOOD_UI) == []


@pytest.mark.parametrize("ui", [
    "docs/design/a.md",        # 非映射
    ["design_doc"],
    42,
])
def test_ui_contract_rejects_non_mapping(ui):
    assert validate_ui_contract(ui)


@pytest.mark.parametrize("ui", [
    {},                                        # 全缺
    {**GOOD_UI, "design_doc": ""},             # 空字符串
    {**GOOD_UI, "visual_reference": "  "},     # 纯空白
    {**GOOD_UI, "visual_acceptance": 42},      # 非字符串
    {**GOOD_UI, "desktop": "1440x900"},        # desktop 非映射
    {**GOOD_UI, "desktop": {"viewport": "1440x900"}},           # 缺 screenshot
    {**GOOD_UI, "desktop": {"screenshot": "s.png"}},            # 缺 viewport
    {**GOOD_UI, "mobile": {"viewport": "", "screenshot": "s.png"}},  # 空 viewport
    {k: v for k, v in GOOD_UI.items() if k != "mobile"},        # 缺 mobile
    {k: v for k, v in GOOD_UI.items() if k != "visual_acceptance"},  # 缺独立视觉验收
])
def test_ui_contract_rejected(ui):
    errors = validate_ui_contract(ui)
    assert errors, f"expected errors for {ui!r}"
    assert all(isinstance(e, str) for e in errors)


def test_ui_contract_reports_one_error_per_problem():
    errors = validate_ui_contract({})
    # design_doc / visual_reference / desktop / mobile / visual_acceptance 各一条
    assert len(errors) == 5


# ── E. validate_runner_metadata ─────────────────────────────────────────

def test_runner_minimal_valid():
    assert validate_runner_metadata({"runner_class": "default"}) == []


def test_runner_full_valid():
    assert validate_runner_metadata(GOOD_RUNNER) == []


def test_runner_accepts_offset_timestamp():
    runner = {**GOOD_RUNNER, "lease_expires_at": "2026-08-01T08:00:00+08:00"}
    assert validate_runner_metadata(runner) == []


@pytest.mark.parametrize("runner", [
    "gpu-heavy",                                  # 非映射
    {},                                           # 缺 runner_class
    {"runner_class": ""},                         # 空字符串
    {"runner_class": "  "},                       # 纯空白
    {"runner_class": 42},                         # 非字符串
    {"runner_class": "x", "preferred_host": ""},  # 可选字段为空
    {"runner_class": "x", "actual_host": 42},     # 可选字段非字符串
    {"runner_class": "x", "lease_holder": []},    # 可选字段非字符串
    # expected_tip:严格 40 位小写 hex
    {"runner_class": "x", "expected_tip": "abc"},
    {"runner_class": "x", "expected_tip": "0" * 39},
    {"runner_class": "x", "expected_tip": "0" * 41},
    {"runner_class": "x", "expected_tip": "A" * 40},
    {"runner_class": "x", "expected_tip": "g" * 40},
    {"runner_class": "x", "expected_tip": 42},
    # lease_expires_at:必须可解析为 ISO-8601
    {"runner_class": "x", "lease_holder": "w", "lease_expires_at": "not-a-date"},
    {"runner_class": "x", "lease_holder": "w", "lease_expires_at": "2026-13-01T00:00:00Z"},
    {"runner_class": "x", "lease_holder": "w", "lease_expires_at": 42},
    # lease_expires_at 无 lease_holder → fail closed
    {"runner_class": "x", "lease_expires_at": "2026-08-01T00:00:00Z"},
])
def test_runner_rejected(runner):
    errors = validate_runner_metadata(runner)
    assert errors, f"expected errors for {runner!r}"
    assert all(isinstance(e, str) for e in errors)


def test_runner_lease_expiry_without_holder_mentions_holder():
    errors = validate_runner_metadata(
        {"runner_class": "x", "lease_expires_at": "2026-08-01T00:00:00Z"})
    assert any("lease_holder" in e for e in errors)


# ── lint 接线(块存在时校验,不存在时行为不变)─────────────────────────────

def test_lint_clean_manifest_without_blocks(tmp_path):
    m = load_manifest(_write(tmp_path, BASIC))
    assert lint(m, {"alice"}) == []


def test_lint_accepts_valid_blocks(tmp_path):
    m = load_manifest(_write(tmp_path, WITH_BLOCKS))
    assert lint(m, {"alice"}) == []


def test_lint_flags_invalid_ui(tmp_path):
    content = BASIC.replace(
        "    worker: alice\n",
        "    worker: alice\n    ui:\n      design_doc: docs/a.md\n")
    m = load_manifest(_write(tmp_path, content))
    errors = lint(m, {"alice"})
    assert errors
    assert all("node a" in e for e in errors)


def test_lint_flags_invalid_runner(tmp_path):
    content = BASIC.replace(
        "    worker: alice\n",
        "    worker: alice\n    runner:\n      lease_expires_at: \"2026-08-01T00:00:00Z\"\n")
    m = load_manifest(_write(tmp_path, content))
    errors = lint(m, {"alice"})
    assert errors
    assert all("node a" in e for e in errors)


# ── 向后兼容:load_manifest 不自动跑这两个校验器 ───────────────────────────

def test_load_manifest_does_not_validate_blocks(tmp_path):
    """畸形 ui/runner 块仍可按原样 load(校验留给显式校验器/lint)。"""
    content = BASIC.replace(
        "    worker: alice\n",
        "    worker: alice\n    ui: not-a-mapping\n    runner: {}\n")
    m = load_manifest(_write(tmp_path, content))
    assert m.nodes["a"].ui == "not-a-mapping"
    assert m.nodes["a"].runner == {}
