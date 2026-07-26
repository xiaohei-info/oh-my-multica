"""PR readiness 交付门:WorkItemStore.validate_pr_ready_for_handoff 契约。

安全不变量(本文件锁定):
  - pipeline/CLI 不直接调 gh;PR ready 检查只经 WorkItemStore 数据面方法
    (§12.4 红线:平台 CLI 调用只允许封装在引擎适配器内);
  - 基类 fail-closed:非 GitHub URL 放行,GitHub URL 而引擎不支持时抛
    ValidationError(报错即教学);
  - mock 适配器提供可注入 payload 的可检查 stub(记录调用日志);
  - multica 适配器在适配器内封装 gh pr view,draft/非 OPEN/gh 缺失
    均抛带修复指令的 ValidationError。
"""
from __future__ import annotations

import subprocess

import pytest

from omac.engines import create_engine
from omac.engines.models import EngineConfig
from omac.engines.multica import MulticaStore
from omac.engines.store import WorkItemStore
from omac.errors import ValidationError

GITHUB_PR = "https://github.com/acme/snake/pull/1"


def _mock_store():
    config = EngineConfig(
        engine_type="mock", workspace_id="mock-workspace",
        extra={"MOCK_AUTO_COMPLETE": "false", "MOCK_AUTO_COMPLETE_DELAY": "0"})
    return create_engine("mock", config).store


def _multica_store():
    return MulticaStore(EngineConfig(engine_type="multica", workspace_id="ws"))


# ── 基类契约:fail-closed ─────────────────────────────────────────────────


class TestBaseStoreContract:
    def test_non_github_url_passes_without_engine_support(self):
        store = _mock_store()
        # 非 GitHub URL 无可校验,基类直接放行。
        WorkItemStore.validate_pr_ready_for_handoff(store, "https://x/pr/1")

    def test_github_url_fails_closed_on_unsupported_engine(self):
        store = _mock_store()
        with pytest.raises(ValidationError) as exc:
            WorkItemStore.validate_pr_ready_for_handoff(store, GITHUB_PR)
        assert "readiness" in str(exc.value).lower() or "ready" in str(exc.value).lower()


# ── mock 适配器:可注入 payload 的可检查 stub ──────────────────────────────


class TestMockStoreStub:
    def test_default_accepts_and_records_call(self):
        store = _mock_store()
        store.validate_pr_ready_for_handoff(GITHUB_PR)
        assert store.pr_readiness_log == [GITHUB_PR]

    def test_injected_draft_payload_rejects(self):
        store = _mock_store()
        store.pr_readiness_payload = {"isDraft": True}
        with pytest.raises(ValidationError) as exc:
            store.validate_pr_ready_for_handoff(GITHUB_PR)
        assert "draft" in str(exc.value).lower()
        assert "gh pr ready" in str(exc.value)

    def test_injected_non_open_state_rejects(self):
        store = _mock_store()
        store.pr_readiness_payload = {"isDraft": False, "state": "MERGED"}
        with pytest.raises(ValidationError) as exc:
            store.validate_pr_ready_for_handoff(GITHUB_PR)
        assert "OPEN" in str(exc.value)
        assert "MERGED" in str(exc.value)

    def test_injected_ready_payload_accepts(self):
        store = _mock_store()
        store.pr_readiness_payload = {"isDraft": False, "state": "OPEN"}
        store.validate_pr_ready_for_handoff(GITHUB_PR)
        assert store.pr_readiness_log == [GITHUB_PR]


# ── multica 适配器:gh 调用封装在适配器内 ──────────────────────────────────


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestMulticaAdapter:
    def test_non_github_url_skips_gh(self, monkeypatch):
        store = _multica_store()
        monkeypatch.setattr(
            "omac.engines.multica.subprocess.run",
            lambda *a, **k: pytest.fail("gh must not be called for non-GitHub URL"))
        store.validate_pr_ready_for_handoff("https://x/pr/1")

    def test_ready_pr_passes(self, monkeypatch):
        store = _multica_store()
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _Proc(stdout='{"isDraft": false, "state": "OPEN"}\n')

        monkeypatch.setattr("omac.engines.multica.subprocess.run", fake_run)
        store.validate_pr_ready_for_handoff(GITHUB_PR)
        assert calls == [["gh", "pr", "view", GITHUB_PR, "--json", "isDraft,state"]]

    def test_draft_pr_rejects_with_teaching_message(self, monkeypatch):
        store = _multica_store()
        monkeypatch.setattr(
            "omac.engines.multica.subprocess.run",
            lambda *a, **k: _Proc(stdout='{"isDraft": true}\n'))
        with pytest.raises(ValidationError) as exc:
            store.validate_pr_ready_for_handoff(GITHUB_PR)
        assert "draft" in str(exc.value).lower()
        assert "gh pr ready" in str(exc.value)

    def test_non_open_pr_rejects(self, monkeypatch):
        store = _multica_store()
        monkeypatch.setattr(
            "omac.engines.multica.subprocess.run",
            lambda *a, **k: _Proc(stdout='{"isDraft": false, "state": "CLOSED"}\n'))
        with pytest.raises(ValidationError) as exc:
            store.validate_pr_ready_for_handoff(GITHUB_PR)
        assert "OPEN" in str(exc.value)
        assert "CLOSED" in str(exc.value)

    def test_gh_missing_teaches_install(self, monkeypatch):
        store = _multica_store()

        def boom(*a, **k):
            raise FileNotFoundError("gh")

        monkeypatch.setattr("omac.engines.multica.subprocess.run", boom)
        with pytest.raises(ValidationError) as exc:
            store.validate_pr_ready_for_handoff(GITHUB_PR)
        assert "brew install gh && gh auth login" in str(exc.value)

    def test_gh_timeout_rejects(self, monkeypatch):
        store = _multica_store()

        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="gh", timeout=30)

        monkeypatch.setattr("omac.engines.multica.subprocess.run", boom)
        with pytest.raises(ValidationError) as exc:
            store.validate_pr_ready_for_handoff(GITHUB_PR)
        assert "timed out" in str(exc.value).lower() or "超时" in str(exc.value)

    def test_gh_failure_includes_detail(self, monkeypatch):
        store = _multica_store()
        monkeypatch.setattr(
            "omac.engines.multica.subprocess.run",
            lambda *a, **k: _Proc(returncode=1, stderr="auth required"))
        with pytest.raises(ValidationError) as exc:
            store.validate_pr_ready_for_handoff(GITHUB_PR)
        assert "auth required" in str(exc.value)

    def test_non_json_output_rejects(self, monkeypatch):
        store = _multica_store()
        monkeypatch.setattr(
            "omac.engines.multica.subprocess.run",
            lambda *a, **k: _Proc(stdout="not json"))
        with pytest.raises(ValidationError) as exc:
            store.validate_pr_ready_for_handoff(GITHUB_PR)
        assert "JSON" in str(exc.value)
