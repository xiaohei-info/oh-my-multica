"""`.omac` 状态回写 git —— 隔离区 agent 与跨机编排的同步地基。

架构:agent 在隔离工作区只能 clone main,信息来源只有远程仓库。于是:
- config.yaml 必须已 push 到 main,否则 agent 读不到 → 派单前自动同步(ensure_config_synced)
- manifest 是编排器状态,跨机 resume 靠它 → tick 后回写(commit_manifest)

开关:真实引擎(multica)默认开(架构要求);mock 本地跑默认关(不碰业务仓库);
OMAC_GIT_SYNC 显式覆盖(truthy 强开 / falsy 强关)。
"""
import os
import subprocess

from . import logsetup
from ..errors import ValidationError
from ..i18n import ui

log = logsetup.get_logger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def sync_enabled(engine_type=None) -> bool:
    """是否把 .omac 状态回写 git。

    OMAC_GIT_SYNC 显式覆盖优先;未设时默认按引擎:multica 需要(隔离区 agent +
    跨机编排都靠 main 上的 .omac),mock 不需要。
    """
    env = os.environ.get("OMAC_GIT_SYNC", "").strip().lower()
    if env in _TRUTHY:
        return True
    if env in _FALSY:
        return False
    return engine_type == "multica"


def _run(repo_root, *args):
    return subprocess.run(["git", *args], cwd=repo_root,
                          capture_output=True, text=True)


def _is_non_fast_forward(stderr: str) -> bool:
    text = stderr.lower()
    return "non-fast-forward" in text or "fetch first" in text


def _repo_relative_path(repo_root: str, path: str) -> str:
    return os.path.normpath(
        os.path.relpath(path, repo_root) if os.path.isabs(path) else path)


def _repo_relative_paths(repo_root: str, paths) -> list[str]:
    return list(dict.fromkeys(_repo_relative_path(repo_root, path) for path in paths))


def _manifest_only_local_commits(repo_root: str, upstream: str,
                                 path: str) -> tuple[bool, set[str]]:
    return _files_only_local_commits(repo_root, upstream, [path])


def _files_only_local_commits(repo_root: str, upstream: str,
                              paths) -> tuple[bool, set[str]]:
    allowed = set(_repo_relative_paths(repo_root, paths))
    commits = _run(repo_root, "rev-list", "--parents", f"{upstream}..HEAD")
    if commits.returncode != 0:
        return False, set()

    touched: set[str] = set()
    for line in commits.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        commit = parts[0]
        if len(parts) > 2:
            return False, {f"merge commit {commit}"}
        changed = _run(
            repo_root, "-c", "core.quotepath=false", "diff-tree",
            "--no-commit-id", "--name-only", "-r", commit)
        if changed.returncode != 0:
            return False, touched
        touched.update(
            os.path.normpath(item) for item in changed.stdout.splitlines() if item)
    return bool(touched) and touched == allowed, touched


def _has_unpushed_path(repo_root: str, path: str) -> bool:
    return _has_unpushed_files(repo_root, [path])


def _has_unpushed_files(repo_root: str, paths) -> bool:
    result = _run(
        repo_root, "rev-list", "@{upstream}..HEAD", "--",
        *_repo_relative_paths(repo_root, paths))
    return result.returncode == 0 and bool(result.stdout.strip())


def _retry_manifest_push(path: str, repo_root: str) -> None:
    _retry_files_push([path], repo_root)


def _retry_files_push(paths, repo_root: str) -> None:
    fetched = _run(repo_root, "fetch", "--quiet")
    if fetched.returncode != 0:
        log.warning("manifest_sync_failed", step="fetch",
                    error=fetched.stderr.strip())
        return

    upstream_result = _run(
        repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name",
        "@{upstream}")
    if upstream_result.returncode != 0:
        log.warning("manifest_sync_failed", step="upstream",
                    error=upstream_result.stderr.strip())
        return
    upstream = upstream_result.stdout.strip()

    head_result = _run(repo_root, "rev-parse", "HEAD")
    if head_result.returncode != 0:
        log.warning("manifest_sync_failed", step="safety",
                    error=head_result.stderr.strip())
        return
    validated_head = head_result.stdout.strip()

    relative_paths = _repo_relative_paths(repo_root, paths)
    if len(relative_paths) == 1:
        safe, touched = _manifest_only_local_commits(
            repo_root, upstream, relative_paths[0])
    else:
        safe, touched = _files_only_local_commits(
            repo_root, upstream, relative_paths)
    if not safe:
        paths = ", ".join(sorted(touched)) or ui(
            "unable to determine local commit scope", "无法确定本地提交范围")
        log.warning(
            "manifest_sync_failed", step="safety",
            error=ui(
                f"Unpushed local commits modify more than the manifest: {paths}",
                f"本地未推送提交不只修改 manifest: {paths}"),
            hint=ui(
                "OMAC will not rebase user business commits automatically.",
                "OMAC 不会自动 rebase 用户业务提交"))
        return

    current_head = _run(repo_root, "rev-parse", "HEAD")
    if current_head.returncode != 0 or current_head.stdout.strip() != validated_head:
        log.warning(
            "manifest_sync_failed", step="safety",
            error=ui(
                "HEAD changed after the safety check; automatic rebase stopped.",
                "安全检查后 HEAD 已变化,停止自动 rebase"),
            hint=ui(
                "Wait for the current git operation; the next tick will retry.",
                "等待当前 git 操作完成后由下一轮 tick 重试"))
        return

    rebased = _run(repo_root, "rebase", upstream)
    if rebased.returncode != 0:
        aborted = _run(repo_root, "rebase", "--abort")
        if aborted.returncode != 0:
            log.warning(
                "manifest_sync_failed", step="rebase_abort",
                error=aborted.stderr.strip(),
                hint=ui(
                    "Could not abort rebase; inspect the repository manually.",
                    "rebase 中止失败,仓库需要人工检查"))
        else:
            log.warning(
                "manifest_sync_failed", step="rebase",
                error=(rebased.stderr or rebased.stdout).strip(),
                hint=ui(
                    "The manifest conflicts with remote state. Rebase was aborted and remote state was not overwritten.",
                    "manifest 与远程状态冲突,已中止 rebase,未覆盖远程"))
        return

    retried = _run(repo_root, "push")
    if retried.returncode != 0:
        log.warning("manifest_sync_failed", step="push_retry",
                    error=retried.stderr.strip(),
                    hint=ui(
                        "Manifest rebased, but the retry push failed.",
                        "manifest 已 rebase 但重试 push 失败"))


def ensure_config_synced(config_path: str, branch: str = "main",
                         repo_root: str = ".", engine_type=None) -> None:
    """派单前把 config 同步到 origin/<branch>:脏就自动 commit+push。

    config 是 omac 自有状态,不该让用户手动 commit+push——脏就当场提交,已提交没推就
    补推,幂等静默。只在两种「omac 无法自动修复」时才硬报错:
    - config 不存在 → 引导 `omac init`
    - push 被远程拒(分叉/无 upstream)→ 引导用户手动 pull/rebase 后重试

    sync 关闭(mock 本地跑)时完全 no-op,不碰业务仓库 git。
    """
    if not sync_enabled(engine_type):
        return

    abs_path = config_path if config_path.startswith("/") else f"{repo_root}/{config_path}"
    if not os.path.exists(abs_path):
        raise ValidationError(ui(
            f"Configuration not found: {config_path}. Run `omac init` first.",
            f"config 不存在: {config_path} —— 先运行 `omac init` 生成配置"))

    # 有未提交改动就自动提交(隔离区 agent clone 到的必须是最新 config)
    if _run(repo_root, "status", "--porcelain", "--", config_path).stdout.strip():
        _run(repo_root, "add", config_path)
        r = _run(repo_root, "commit", "-m", f"chore(omac): sync {config_path}")
        if r.returncode != 0:
            raise ValidationError(ui(
                f"Automatic configuration commit failed: {r.stderr.strip()}",
                f"config 自动提交失败: {r.stderr.strip()}"))

    # 补推(覆盖「已提交未推送」;已同步时 Everything up-to-date 幂等)
    r = _run(repo_root, "push", "origin", branch)
    if r.returncode != 0:
        raise ValidationError(ui(
            f"Could not push configuration to origin/{branch}; isolated agents would clone stale state.\n"
            f"  {r.stderr.strip()}\n"
            f"  The remote may have diverged. Run `git pull --rebase origin {branch}` and retry.",
            f"config push 到 origin/{branch} 失败 —— 隔离区 agent 会 clone 到旧版。\n"
            f"  {r.stderr.strip()}\n"
            f"  远程可能已分叉,先 `git pull --rebase origin {branch}` 再重试"))
    log.info(logsetup.EVT_CONFIG_SYNCED, path=config_path, branch=branch)


def commit_files(paths, message: str, repo_root: str = ".",
                 engine_type=None) -> bool:
    """把一组 OMAC 状态文件作为一个提交同步到远程。"""
    if not sync_enabled(engine_type):
        return False
    relative_paths = _repo_relative_paths(repo_root, paths)
    if not relative_paths:
        return False
    r = _run(repo_root, "add", "--", *relative_paths)
    if r.returncode != 0:
        log.warning("manifest_sync_failed", step="add", error=r.stderr.strip())
        return False
    has_staged_change = (
        _run(repo_root, "diff", "--cached", "--quiet", "--", *relative_paths).returncode != 0)
    if has_staged_change:
        r = _run(repo_root, "commit", "-m", message, "--", *relative_paths)
        if r.returncode != 0:
            log.warning("manifest_sync_failed", step="commit", error=r.stderr.strip())
            return False
    elif not _has_unpushed_files(repo_root, relative_paths):
        return False
    r = _run(repo_root, "push")
    if r.returncode != 0:
        if _is_non_fast_forward(r.stderr):
            _retry_files_push(relative_paths, repo_root)
        else:
            log.warning("manifest_sync_failed", step="push", error=r.stderr.strip(),
                        hint="OMAC 状态已本地 commit 但未 push,跨机口径可能滞后")
    return True


def commit_manifest(path: str, message: str, repo_root: str = ".",
                    engine_type=None) -> bool:
    """git add manifest + commit + push。sync 关闭或无变更时返回 False。

    push 失败醒目告警但不中断编排(跨机口径可能滞后,但不阻塞本机推进);
    不自动 merge —— PR 评审是外部门控。
    """
    return commit_files(
        [path], message, repo_root=repo_root, engine_type=engine_type)
