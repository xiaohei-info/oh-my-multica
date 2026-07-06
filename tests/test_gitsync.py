"""gitsync:.omac 状态回写 git 的开关判定 + config 派单前门 + manifest 回写。

隔离区 agent 只能 clone main,信息来源只有远程仓库:
- config.yaml 必须已 push 到 main,否则 agent 读不到 → 派单前硬门(assert_config_pushed)
- manifest 是编排器状态,跨机 resume 靠它 → tick 后回写(commit_manifest)
"""
import os
import subprocess

import pytest

from omac.core.gitsync import sync_enabled, commit_manifest, assert_config_pushed
from omac.errors import ValidationError


# ==================== sync_enabled 判定矩阵 ====================

class TestSyncEnabled:
    def test_multica_default_on(self, monkeypatch):
        """未设 OMAC_GIT_SYNC 时:真实引擎(multica)默认开——架构要求 .omac 上 main。"""
        monkeypatch.delenv("OMAC_GIT_SYNC", raising=False)
        assert sync_enabled("multica") is True

    def test_mock_default_off(self, monkeypatch):
        """未设时:mock 默认关——本地跑不碰业务仓库 git。"""
        monkeypatch.delenv("OMAC_GIT_SYNC", raising=False)
        assert sync_enabled("mock") is False
        assert sync_enabled(None) is False

    def test_env_truthy_forces_on(self, monkeypatch):
        """OMAC_GIT_SYNC=1 覆盖:即便 mock 也开(测试/特殊场景)。"""
        monkeypatch.setenv("OMAC_GIT_SYNC", "1")
        assert sync_enabled("mock") is True

    def test_env_falsy_forces_off(self, monkeypatch):
        """OMAC_GIT_SYNC=0 覆盖:即便 multica 也关(逃生阀)。"""
        monkeypatch.setenv("OMAC_GIT_SYNC", "0")
        assert sync_enabled("multica") is False


# ==================== 真实临时 git 仓库(带 bare 远程) ====================

def _git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True, text=True)


def _make_repo(tmp_path):
    """建 work 仓 + bare 远程,分支 main,已推一次初始提交。返回 work 路径。"""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True,
                   capture_output=True, text=True)
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init")
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    _git(work, "checkout", "-b", "main")
    _git(work, "remote", "add", "origin", str(remote))
    (work / "README").write_text("x")
    _git(work, "add", "README")
    _git(work, "commit", "-m", "init")
    _git(work, "push", "-u", "origin", "main")
    return work


def _write_config(work):
    d = work / ".omac"
    d.mkdir(exist_ok=True)
    (d / "config.yaml").write_text("engine: multica\nworkspace: ws\n")
    return ".omac/config.yaml"


# ==================== assert_config_pushed 派单前门 ====================

class TestAssertConfigPushed:
    def test_missing_config_raises(self, tmp_path):
        work = _make_repo(tmp_path)
        with pytest.raises(ValidationError, match="config"):
            assert_config_pushed(".omac/config.yaml", branch="main", repo_root=str(work))

    def test_uncommitted_config_raises(self, tmp_path):
        work = _make_repo(tmp_path)
        _write_config(work)  # 写了但没 commit
        with pytest.raises(ValidationError, match="未提交|commit"):
            assert_config_pushed(".omac/config.yaml", branch="main", repo_root=str(work))

    def test_committed_but_unpushed_raises(self, tmp_path):
        work = _make_repo(tmp_path)
        _write_config(work)
        _git(work, "add", ".omac/config.yaml")
        _git(work, "commit", "-m", "add config")  # commit 了但没 push
        with pytest.raises(ValidationError, match="push|推送"):
            assert_config_pushed(".omac/config.yaml", branch="main", repo_root=str(work))

    def test_committed_and_pushed_passes(self, tmp_path):
        work = _make_repo(tmp_path)
        _write_config(work)
        _git(work, "add", ".omac/config.yaml")
        _git(work, "commit", "-m", "add config")
        _git(work, "push", "origin", "main")
        # 不抛即通过
        assert_config_pushed(".omac/config.yaml", branch="main", repo_root=str(work))


# ==================== commit_manifest 回写 ====================

class TestCommitManifest:
    def test_disabled_engine_skips(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OMAC_GIT_SYNC", raising=False)
        work = _make_repo(tmp_path)
        (work / ".omac").mkdir()
        (work / ".omac" / "m.yaml").write_text("nodes: {}\n")
        # mock 引擎不 sync:返回 False,不 commit
        assert commit_manifest(".omac/m.yaml", "msg", repo_root=str(work),
                               engine_type="mock") is False

    def test_multica_commits_and_pushes(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OMAC_GIT_SYNC", raising=False)
        work = _make_repo(tmp_path)
        (work / ".omac").mkdir()
        (work / ".omac" / "m.yaml").write_text("nodes: {}\n")
        assert commit_manifest(".omac/m.yaml", "manifest sync", repo_root=str(work),
                               engine_type="multica") is True
        # 已 push 到远程:本地无未推送提交
        out = subprocess.run(["git", "rev-list", "@{upstream}..HEAD"], cwd=str(work),
                             capture_output=True, text=True)
        assert out.stdout.strip() == ""

    def test_no_change_skips(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OMAC_GIT_SYNC", raising=False)
        work = _make_repo(tmp_path)
        (work / ".omac").mkdir()
        (work / ".omac" / "m.yaml").write_text("nodes: {}\n")
        commit_manifest(".omac/m.yaml", "first", repo_root=str(work), engine_type="multica")
        # 再来一次无改动:幂等跳过
        assert commit_manifest(".omac/m.yaml", "again", repo_root=str(work),
                               engine_type="multica") is False
