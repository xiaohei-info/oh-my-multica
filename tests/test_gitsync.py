"""gitsync:.omac 状态回写 git 的开关判定 + config 派单前门 + manifest 回写。

隔离区 agent 只能 clone main,信息来源只有远程仓库:
- config.yaml 必须已 push 到 main,否则 agent 读不到 → 派单前硬门(assert_config_pushed)
- manifest 是编排器状态,跨机 resume 靠它 → tick 后回写(commit_manifest)
"""
import os
import subprocess

import pytest

from omac.core.gitsync import sync_enabled, commit_manifest, ensure_config_synced
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


# ==================== ensure_config_synced 派单前自动同步 ====================

def _unpushed(work, path):
    """当前分支相对 upstream 有触及 path 的未推送提交 → True(未同步)。"""
    out = subprocess.run(["git", "rev-list", "@{upstream}..HEAD", "--", path],
                         cwd=str(work), capture_output=True, text=True)
    return bool(out.stdout.strip())


def _dirty(work, path):
    out = subprocess.run(["git", "status", "--porcelain", "--", path],
                         cwd=str(work), capture_output=True, text=True)
    return bool(out.stdout.strip())


class TestEnsureConfigSynced:
    def test_missing_config_raises(self, tmp_path):
        """config 不存在无法自动补 → 硬报错(引导 omac init)。"""
        work = _make_repo(tmp_path)
        with pytest.raises(ValidationError, match="config"):
            ensure_config_synced(".omac/config.yaml", repo_root=str(work),
                                 engine_type="multica")

    def test_uncommitted_config_auto_commits_and_pushes(self, tmp_path):
        """脏 config 不再报错:omac 自动 commit+push,派单前落到 origin/main。"""
        work = _make_repo(tmp_path)
        _write_config(work)  # 写了但没 commit
        ensure_config_synced(".omac/config.yaml", repo_root=str(work),
                             engine_type="multica")
        assert not _dirty(work, ".omac/config.yaml")
        assert not _unpushed(work, ".omac/config.yaml")

    def test_committed_but_unpushed_auto_pushes(self, tmp_path):
        """已提交但没推 → 自动补推(不用用户手动 git push)。"""
        work = _make_repo(tmp_path)
        _write_config(work)
        _git(work, "add", ".omac/config.yaml")
        _git(work, "commit", "-m", "add config")  # commit 了但没 push
        ensure_config_synced(".omac/config.yaml", repo_root=str(work),
                             engine_type="multica")
        assert not _unpushed(work, ".omac/config.yaml")

    def test_clean_and_pushed_is_noop(self, tmp_path):
        """已同步 → 幂等静默通过(不抛、不产生空提交)。"""
        work = _make_repo(tmp_path)
        _write_config(work)
        _git(work, "add", ".omac/config.yaml")
        _git(work, "commit", "-m", "add config")
        _git(work, "push", "origin", "main")
        head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(work),
                                     capture_output=True, text=True).stdout.strip()
        ensure_config_synced(".omac/config.yaml", repo_root=str(work),
                             engine_type="multica")
        head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(work),
                                    capture_output=True, text=True).stdout.strip()
        assert head_before == head_after  # 无空提交

    def test_sync_disabled_skips(self, tmp_path, monkeypatch):
        """sync 关(mock 引擎)→ 完全 no-op:脏 config 也不碰、不报错。"""
        monkeypatch.delenv("OMAC_GIT_SYNC", raising=False)
        work = _make_repo(tmp_path)
        _write_config(work)
        ensure_config_synced(".omac/config.yaml", repo_root=str(work),
                             engine_type="mock")
        assert _dirty(work, ".omac/config.yaml")  # 没被动过

    def test_push_rejected_raises(self, tmp_path):
        """push 被拒(远程分叉)是唯一无法自动修复的场景 → 硬报错引导手动解决。"""
        work = _make_repo(tmp_path)
        # 另一个 clone 推进 main,使 work 落后 → 之后 push 非快进被拒
        other = tmp_path / "other"
        subprocess.run(["git", "clone", str(tmp_path / "remote.git"), str(other)],
                       check=True, capture_output=True, text=True)
        _git(other, "config", "user.email", "o@o")
        _git(other, "config", "user.name", "o")
        _git(other, "checkout", "main")  # bare HEAD=master,显式检出 main
        (other / "README").write_text("advanced")
        _git(other, "add", "README")
        _git(other, "commit", "-m", "advance main")
        _git(other, "push", "origin", "main")
        # work 本地提交 config,但落后于远程 → push 被拒
        _write_config(work)
        with pytest.raises(ValidationError, match="push|推送|失败"):
            ensure_config_synced(".omac/config.yaml", repo_root=str(work),
                                 engine_type="multica")


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
