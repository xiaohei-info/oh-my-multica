"""gitsync:.omac 状态回写 git 的开关判定 + config 派单前门 + manifest 回写。

隔离区 agent 只能 clone main,信息来源只有远程仓库:
- config.yaml 必须已 push 到 main,否则 agent 读不到 → 派单前硬门(assert_config_pushed)
- manifest 是编排器状态,跨机 resume 靠它 → tick 后回写(commit_manifest)
"""
import os
import subprocess

import pytest

from omac.core import gitsync
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

    def test_remote_advance_rebases_manifest_only_commit_and_pushes(
            self, tmp_path, monkeypatch):
        """PR merge 推进远程 main 后,manifest-only 提交自动 rebase 并补推。"""
        monkeypatch.delenv("OMAC_GIT_SYNC", raising=False)
        work = _make_repo(tmp_path)
        manifest = work / ".omac" / "m.yaml"
        manifest.parent.mkdir()
        manifest.write_text("nodes:\n  a: todo\n")
        assert commit_manifest(
            ".omac/m.yaml", "initial manifest", repo_root=str(work),
            engine_type="multica") is True

        other = tmp_path / "other"
        subprocess.run(["git", "clone", str(tmp_path / "remote.git"), str(other)],
                       check=True, capture_output=True, text=True)
        _git(other, "config", "user.email", "o@o")
        _git(other, "config", "user.name", "o")
        _git(other, "checkout", "main")
        (other / "README").write_text("merged code")
        _git(other, "add", "README")
        _git(other, "commit", "-m", "merge worker pr")
        _git(other, "push", "origin", "main")

        manifest.write_text("nodes:\n  a: done\n  b: in_progress\n")
        assert commit_manifest(
            ".omac/m.yaml", "manifest sync", repo_root=str(work),
            engine_type="multica") is True

        _git(work, "fetch", "origin", "main")
        local_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(work),
            capture_output=True, text=True, check=True).stdout.strip()
        remote_head = subprocess.run(
            ["git", "rev-parse", "origin/main"], cwd=str(work),
            capture_output=True, text=True, check=True).stdout.strip()
        assert local_head == remote_head
        remote_manifest = subprocess.run(
            ["git", "show", "origin/main:.omac/m.yaml"], cwd=str(work),
            capture_output=True, text=True, check=True).stdout
        assert remote_manifest == "nodes:\n  a: done\n  b: in_progress\n"
        assert (work / "README").read_text() == "merged code"

    def test_committed_unpushed_manifest_recovers_without_new_change(
            self, tmp_path, monkeypatch):
        """上轮 push 失败后即使 manifest 未再变化,下一 tick 也会自动补推。"""
        monkeypatch.delenv("OMAC_GIT_SYNC", raising=False)
        work = _make_repo(tmp_path)
        manifest = work / ".omac" / "m.yaml"
        manifest.parent.mkdir()
        manifest.write_text("state: initial\n")
        assert commit_manifest(
            ".omac/m.yaml", "initial manifest", repo_root=str(work),
            engine_type="multica") is True

        other = tmp_path / "other"
        subprocess.run(["git", "clone", str(tmp_path / "remote.git"), str(other)],
                       check=True, capture_output=True, text=True)
        _git(other, "config", "user.email", "o@o")
        _git(other, "config", "user.name", "o")
        _git(other, "checkout", "main")
        (other / "README").write_text("merged")
        _git(other, "add", "README")
        _git(other, "commit", "-m", "remote advance")
        _git(other, "push", "origin", "main")

        manifest.write_text("state: running\n")
        _git(work, "add", ".omac/m.yaml")
        _git(work, "commit", "-m", "manifest sync")

        assert commit_manifest(
            ".omac/m.yaml", "manifest sync", repo_root=str(work),
            engine_type="multica") is True
        _git(work, "fetch", "origin", "main")
        assert subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(work),
            capture_output=True, text=True, check=True).stdout == subprocess.run(
            ["git", "rev-parse", "origin/main"], cwd=str(work),
            capture_output=True, text=True, check=True).stdout

    def test_unicode_manifest_path_is_compared_without_git_quoting(
            self, tmp_path, monkeypatch):
        """中文 manifest 路径不能因 core.quotePath 转义而触发 safety 误报。"""
        monkeypatch.delenv("OMAC_GIT_SYNC", raising=False)
        work = _make_repo(tmp_path)
        manifest_path = ".omac/贪吃蛇手游.yaml"
        manifest = work / manifest_path
        manifest.parent.mkdir()
        manifest.write_text("state: initial\n")
        assert commit_manifest(
            manifest_path, "initial manifest", repo_root=str(work),
            engine_type="multica") is True

        other = tmp_path / "other"
        subprocess.run(["git", "clone", str(tmp_path / "remote.git"), str(other)],
                       check=True, capture_output=True, text=True)
        _git(other, "config", "user.email", "o@o")
        _git(other, "config", "user.name", "o")
        _git(other, "checkout", "main")
        (other / "README").write_text("merged")
        _git(other, "add", "README")
        _git(other, "commit", "-m", "remote advance")
        _git(other, "push", "origin", "main")

        manifest.write_text("state: running\n")
        _git(work, "add", manifest_path)
        _git(work, "commit", "-m", "manifest sync")

        assert commit_manifest(
            manifest_path, "manifest sync", repo_root=str(work),
            engine_type="multica") is True
        _git(work, "fetch", "origin", "main")
        assert subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(work),
            capture_output=True, text=True, check=True).stdout == subprocess.run(
            ["git", "rev-parse", "origin/main"], cwd=str(work),
            capture_output=True, text=True, check=True).stdout

    def test_remote_advance_does_not_rebase_unrelated_local_commit(
            self, tmp_path, monkeypatch):
        """本地含业务提交时不自动 rebase,避免 OMAC 改写用户历史。"""
        monkeypatch.delenv("OMAC_GIT_SYNC", raising=False)
        work = _make_repo(tmp_path)
        manifest = work / ".omac" / "m.yaml"
        manifest.parent.mkdir()
        manifest.write_text("nodes: {}\n")
        assert commit_manifest(
            ".omac/m.yaml", "initial manifest", repo_root=str(work),
            engine_type="multica") is True

        other = tmp_path / "other"
        subprocess.run(["git", "clone", str(tmp_path / "remote.git"), str(other)],
                       check=True, capture_output=True, text=True)
        _git(other, "config", "user.email", "o@o")
        _git(other, "config", "user.name", "o")
        _git(other, "checkout", "main")
        (other / "README").write_text("remote")
        _git(other, "add", "README")
        _git(other, "commit", "-m", "remote advance")
        _git(other, "push", "origin", "main")

        (work / "LOCAL").write_text("user work")
        _git(work, "add", "LOCAL")
        _git(work, "commit", "-m", "user commit")
        manifest.write_text("nodes:\n  a: done\n")

        warnings = []
        monkeypatch.setattr(
            "omac.core.gitsync.log.warning",
            lambda event, **kwargs: warnings.append((event, kwargs)),
        )
        assert commit_manifest(
            ".omac/m.yaml", "manifest sync", repo_root=str(work),
            engine_type="multica") is True

        assert warnings[-1][0] == "manifest_sync_failed"
        assert warnings[-1][1]["step"] == "safety"
        assert "LOCAL" in warnings[-1][1]["error"]

    def test_remote_advance_rejects_local_merge_commit(
            self, tmp_path, monkeypatch):
        """merge commit 可能携带父提交没有的业务改动,不得自动线性 rebase。"""
        monkeypatch.delenv("OMAC_GIT_SYNC", raising=False)
        work = _make_repo(tmp_path)
        manifest_path = ".omac/m.yaml"
        manifest = work / manifest_path
        manifest.parent.mkdir()
        manifest.write_text("a: 0\nb: 0\nc: 0\nd: 0\ne: 0\n")
        assert commit_manifest(
            manifest_path, "initial manifest", repo_root=str(work),
            engine_type="multica") is True

        other = tmp_path / "other"
        subprocess.run(["git", "clone", str(tmp_path / "remote.git"), str(other)],
                       check=True, capture_output=True, text=True)
        _git(other, "config", "user.email", "o@o")
        _git(other, "config", "user.name", "o")
        _git(other, "checkout", "main")
        (other / "README").write_text("remote")
        _git(other, "add", "README")
        _git(other, "commit", "-m", "remote advance")
        _git(other, "push", "origin", "main")

        _git(work, "checkout", "-b", "side")
        manifest.write_text("a: 1\nb: 0\nc: 0\nd: 0\ne: 0\n")
        _git(work, "add", manifest_path)
        _git(work, "commit", "-m", "side manifest")
        _git(work, "checkout", "main")
        manifest.write_text("a: 0\nb: 0\nc: 0\nd: 0\ne: 1\n")
        _git(work, "add", manifest_path)
        _git(work, "commit", "-m", "main manifest")
        _git(work, "merge", "--no-ff", "--no-commit", "side")
        (work / "BUSINESS").write_text("must survive")
        _git(work, "add", manifest_path, "BUSINESS")
        _git(work, "commit", "-m", "local merge resolution")

        warnings = []
        monkeypatch.setattr(
            "omac.core.gitsync.log.warning",
            lambda event, **kwargs: warnings.append((event, kwargs)),
        )
        assert commit_manifest(
            manifest_path, "manifest sync", repo_root=str(work),
            engine_type="multica") is True

        assert warnings[-1][1]["step"] == "safety"
        assert "merge commit" in warnings[-1][1]["error"]
        assert (work / "BUSINESS").read_text() == "must survive"

    def test_head_change_after_safety_check_stops_rebase(
            self, tmp_path, monkeypatch):
        """安全检查后 HEAD 被并发推进时必须停止,不能带入未经校验的提交。"""
        monkeypatch.delenv("OMAC_GIT_SYNC", raising=False)
        work = _make_repo(tmp_path)
        manifest_path = ".omac/m.yaml"
        manifest = work / manifest_path
        manifest.parent.mkdir()
        manifest.write_text("state: initial\n")
        assert commit_manifest(
            manifest_path, "initial manifest", repo_root=str(work),
            engine_type="multica") is True

        other = tmp_path / "other"
        subprocess.run(["git", "clone", str(tmp_path / "remote.git"), str(other)],
                       check=True, capture_output=True, text=True)
        _git(other, "config", "user.email", "o@o")
        _git(other, "config", "user.name", "o")
        _git(other, "checkout", "main")
        (other / "README").write_text("remote")
        _git(other, "add", "README")
        _git(other, "commit", "-m", "remote advance")
        _git(other, "push", "origin", "main")

        manifest.write_text("state: running\n")
        _git(work, "add", manifest_path)
        _git(work, "commit", "-m", "manifest sync")

        original_check = gitsync._manifest_only_local_commits

        def advance_head(repo_root, upstream, path):
            result = original_check(repo_root, upstream, path)
            (work / "BUSINESS").write_text("concurrent")
            _git(work, "add", "BUSINESS")
            _git(work, "commit", "-m", "concurrent business commit")
            return result

        warnings = []
        monkeypatch.setattr(gitsync, "_manifest_only_local_commits", advance_head)
        monkeypatch.setattr(
            "omac.core.gitsync.log.warning",
            lambda event, **kwargs: warnings.append((event, kwargs)),
        )
        assert commit_manifest(
            manifest_path, "manifest sync", repo_root=str(work),
            engine_type="multica") is True

        assert warnings[-1][1]["step"] == "safety"
        assert "HEAD" in warnings[-1][1]["error"]

    def test_remote_manifest_conflict_aborts_rebase_without_overwrite(
            self, tmp_path, monkeypatch):
        """两端同时改 manifest 时中止 rebase,保留远程与本地各自状态。"""
        monkeypatch.delenv("OMAC_GIT_SYNC", raising=False)
        work = _make_repo(tmp_path)
        manifest = work / ".omac" / "m.yaml"
        manifest.parent.mkdir()
        manifest.write_text("state: initial\n")
        assert commit_manifest(
            ".omac/m.yaml", "initial manifest", repo_root=str(work),
            engine_type="multica") is True

        other = tmp_path / "other"
        subprocess.run(["git", "clone", str(tmp_path / "remote.git"), str(other)],
                       check=True, capture_output=True, text=True)
        _git(other, "config", "user.email", "o@o")
        _git(other, "config", "user.name", "o")
        _git(other, "checkout", "main")
        (other / ".omac" / "m.yaml").write_text("state: remote\n")
        _git(other, "add", ".omac/m.yaml")
        _git(other, "commit", "-m", "remote manifest")
        _git(other, "push", "origin", "main")

        manifest.write_text("state: local\n")
        warnings = []
        monkeypatch.setattr(
            "omac.core.gitsync.log.warning",
            lambda event, **kwargs: warnings.append((event, kwargs)),
        )
        assert commit_manifest(
            ".omac/m.yaml", "local manifest", repo_root=str(work),
            engine_type="multica") is True

        assert warnings[-1][1]["step"] == "rebase"
        assert not (work / ".git" / "rebase-merge").exists()
        assert not (work / ".git" / "rebase-apply").exists()
        assert manifest.read_text() == "state: local\n"
        _git(work, "fetch", "origin", "main")
        remote_manifest = subprocess.run(
            ["git", "show", "origin/main:.omac/m.yaml"], cwd=str(work),
            capture_output=True, text=True, check=True).stdout
        assert remote_manifest == "state: remote\n"

    def test_rebase_abort_failure_is_reported(self, monkeypatch):
        """abort 失败时不能误报已恢复,必须暴露人工修复信号。"""
        responses = {
            ("fetch", "--quiet"): subprocess.CompletedProcess([], 0, "", ""),
            ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"):
                subprocess.CompletedProcess([], 0, "origin/main\n", ""),
            ("rev-parse", "HEAD"):
                subprocess.CompletedProcess([], 0, "abc123\n", ""),
            ("rebase", "origin/main"):
                subprocess.CompletedProcess([], 1, "", "conflict"),
            ("rebase", "--abort"):
                subprocess.CompletedProcess([], 1, "", "abort failed"),
        }

        monkeypatch.setattr(
            gitsync, "_run", lambda _root, *args: responses[args])
        monkeypatch.setattr(
            gitsync, "_manifest_only_local_commits",
            lambda _root, _upstream, _path: (True, {".omac/m.yaml"}),
        )
        warnings = []
        monkeypatch.setattr(
            "omac.core.gitsync.log.warning",
            lambda event, **kwargs: warnings.append((event, kwargs)),
        )

        gitsync._retry_manifest_push(".omac/m.yaml", ".")

        assert warnings == [(
            "manifest_sync_failed",
            {
                "step": "rebase_abort",
                "error": "abort failed",
                "hint": "rebase 中止失败,仓库需要人工检查",
            },
        )]
