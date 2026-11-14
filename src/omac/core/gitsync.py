"""`.omac` 状态回写 git —— 隔离区 agent 与跨机编排的同步地基。

架构:agent 在隔离工作区只能 clone main,信息来源只有远程仓库。于是:
- config.yaml 必须已 push 到 main,否则 agent 读不到 → 派单前自动同步(ensure_config_synced)
- manifest 是编排器状态,跨机 resume 靠它 → tick 后回写(commit_manifest)

开关:真实引擎(multica)默认开(架构要求);非 multica 引擎默认关(不碰业务仓库);
OMAC_GIT_SYNC 显式覆盖(truthy 强开 / falsy 强关)。
"""
import os
import subprocess
import time
from typing import NamedTuple

from . import logsetup
from ..errors import NeedsDecision, ValidationError
from ..i18n import ui
from .manifest import manifest_files
from .pathrefs import relative_to_root

log = logsetup.get_logger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}
_PUSH_RETRY_BASE_SECONDS = 10.0
_PUSH_RETRY_MAX_SECONDS = 300.0
# Process-local: manifest commits stay on disk; only the next network attempt is
# delayed. A Runner process therefore cannot turn one outage into a tight loop.
_PUSH_RETRY_STATE: dict[tuple[str, tuple[str, ...]], dict[str, float | int]] = {}


def sync_enabled(engine_type=None) -> bool:
    """是否把 .omac 状态回写 git。

    OMAC_GIT_SYNC 显式覆盖优先;未设时默认按引擎:multica 需要(隔离区 agent +
    跨机编排都靠 main 上的 .omac),其他引擎不需要。
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
    # A directory in ``allowed`` (the per-node state directory) covers every file beneath it.
    inside = all(any(item == a or item.startswith(a + os.sep) for a in allowed) for item in touched)
    return bool(touched) and inside, touched


def _has_unpushed_path(repo_root: str, path: str) -> bool:
    return _has_unpushed_files(repo_root, [path])


def _has_unpushed_files(repo_root: str, paths) -> bool:
    result = _run(
        repo_root, "rev-list", "@{upstream}..HEAD", "--",
        *_repo_relative_paths(repo_root, paths))
    return result.returncode == 0 and bool(result.stdout.strip())


def _path_matches_remote_tracking(
    repo_root: str, path: str, branch: str,
) -> bool:
    """只读比较 HEAD 与本地 origin/<branch> 快照中的同一文件 blob。"""
    relative = _repo_relative_path(repo_root, path)
    local = _run(repo_root, "rev-parse", f"HEAD:{relative}")
    remote = _run(repo_root, "rev-parse", f"origin/{branch}:{relative}")
    return (
        local.returncode == 0
        and remote.returncode == 0
        and local.stdout.strip() == remote.stdout.strip()
    )


class SyncFailure(NamedTuple):
    """One failed sync attempt: which git step failed, git's own words, and how to repair it."""
    step: str
    error: str
    hint: str = ""


def _fail(step: str, error: str, hint: str = "") -> SyncFailure:
    log.warning("manifest_sync_failed", step=step, error=error, **({"hint": hint} if hint else {}))
    return SyncFailure(step, error, hint)


def _rebase_in_progress(repo_root: str) -> bool:
    for name in ("rebase-merge", "rebase-apply"):
        found = _run(repo_root, "rev-parse", "--git-path", name)
        if found.returncode == 0 and os.path.isdir(os.path.join(repo_root, found.stdout.strip())):
            return True
    return False


def _push_retry_key(repo_root: str, paths) -> tuple[str, tuple[str, ...]]:
    return (
        os.path.abspath(repo_root),
        tuple(sorted(_repo_relative_paths(repo_root, paths))),
    )


def _push_retry_deferred(key: tuple[str, tuple[str, ...]]) -> bool:
    state = _PUSH_RETRY_STATE.get(key)
    return bool(state and time.monotonic() < state["next_retry_at"])


def _record_push_failure(key: tuple[str, tuple[str, ...]], step: str = "push") -> float:
    """Count consecutive failures of the same step (a different step starts over) and return the backoff delay."""
    previous = _PUSH_RETRY_STATE.get(key, {})
    failures = int(previous.get("failures", 0)) + 1 if previous.get("step", step) == step else 1
    delay = min(
        _PUSH_RETRY_BASE_SECONDS * (2 ** (failures - 1)),
        _PUSH_RETRY_MAX_SECONDS,
    )
    _PUSH_RETRY_STATE[key] = {
        "step": step,
        "failures": failures,
        "next_retry_at": time.monotonic() + delay,
    }
    return delay


def _clear_push_retry(key: tuple[str, tuple[str, ...]]) -> None:
    _PUSH_RETRY_STATE.pop(key, None)


def _retry_manifest_push(path: str, repo_root: str) -> SyncFailure | None:
    return _retry_files_push([path], repo_root)


def _retry_files_push(paths, repo_root: str) -> SyncFailure | None:
    """Rebase OMAC-only local commits onto the moved upstream and push again. Returns None once pushed."""
    fetched = _run(repo_root, "fetch", "--quiet")
    if fetched.returncode != 0:
        return _fail("fetch", fetched.stderr.strip())

    upstream_result = _run(
        repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name",
        "@{upstream}")
    if upstream_result.returncode != 0:
        return _fail("upstream", upstream_result.stderr.strip())
    upstream = upstream_result.stdout.strip()

    head_result = _run(repo_root, "rev-parse", "HEAD")
    if head_result.returncode != 0:
        return _fail("safety", head_result.stderr.strip())
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
        return _fail(
            "safety",
            ui(f"Unpushed local commits modify more than the manifest: {paths}",
               f"本地未推送提交不只修改 manifest: {paths}"),
            ui("OMAC will not rebase user business commits automatically.",
               "OMAC 不会自动 rebase 用户业务提交"))

    current_head = _run(repo_root, "rev-parse", "HEAD")
    if current_head.returncode != 0 or current_head.stdout.strip() != validated_head:
        return _fail(
            "safety",
            ui("HEAD changed after the safety check; automatic rebase stopped.",
               "安全检查后 HEAD 已变化,停止自动 rebase"),
            ui("Wait for the current git operation; the next tick will retry.",
               "等待当前 git 操作完成后由下一轮 tick 重试"))

    rebased = _run(repo_root, "rebase", upstream)
    if rebased.returncode != 0:
        error = (rebased.stderr or rebased.stdout).strip()
        if not _rebase_in_progress(repo_root):
            # git refused before starting (e.g. uncommitted changes): there is nothing to abort.
            repo = os.path.abspath(repo_root)
            return _fail(
                "rebase_not_started", error,
                ui(f"git refused to start the rebase, usually because of uncommitted changes in {repo}. "
                   f"Run `git -C {repo} status`, then commit or stash those changes (`git -C {repo} stash`).",
                   f"git 拒绝开始 rebase,通常是 {repo} 有未提交改动。"
                   f"运行 `git -C {repo} status`,提交或暂存这些改动(`git -C {repo} stash`)。"))
        aborted = _run(repo_root, "rebase", "--abort")
        if aborted.returncode != 0:
            return _fail(
                "rebase_abort", aborted.stderr.strip(),
                ui("Could not abort rebase; inspect the repository manually.",
                   "rebase 中止失败,仓库需要人工检查"))
        return _fail(
            "rebase_conflict", error,
            ui("The manifest conflicts with remote state. Rebase was aborted and remote state was not overwritten.",
               "manifest 与远程状态冲突,已中止 rebase,未覆盖远程"))

    retried = _run(repo_root, "push")
    if retried.returncode != 0:
        return _fail(
            "push_retry", retried.stderr.strip(),
            ui("Manifest rebased, but the retry push failed.", "manifest 已 rebase 但重试 push 失败"))
    return None


def ensure_config_synced(config_path: str, branch: str = "main",
                         repo_root: str = ".", engine_type=None) -> None:
    """派单前把 config 同步到 origin/<branch>:脏就自动 commit+push。

    config 是 omac 自有状态,不该让用户手动 commit+push——脏就当场提交,已提交没推就
    补推,幂等静默。只在两种「omac 无法自动修复」时才硬报错:
    - config 不存在 → 引导 `omac init`
    - push 被远程拒(分叉/无 upstream)→ 引导用户手动 pull/rebase 后重试

    sync 关闭时完全 no-op,不碰业务仓库 git。
    """
    if not sync_enabled(engine_type):
        return

    abs_path = config_path if config_path.startswith("/") else f"{repo_root}/{config_path}"
    shown = relative_to_root(config_path, repo_root)    # commit messages and logs are shared: never a machine path
    if not os.path.exists(abs_path):
        raise ValidationError(ui(
            f"Configuration not found: {config_path}. Run `omac init` first.",
            f"config 不存在: {config_path} —— 先运行 `omac init` 生成配置"))

    # 有未提交改动就自动提交(隔离区 agent clone 到的必须是最新 config)
    if _run(repo_root, "status", "--porcelain", "--", config_path).stdout.strip():
        _run(repo_root, "add", config_path)
        r = _run(repo_root, "commit", "-m", f"chore(omac): sync {shown}")
        if r.returncode != 0:
            raise ValidationError(ui(
                f"Automatic configuration commit failed: {r.stderr.strip()}",
                f"config 自动提交失败: {r.stderr.strip()}"))

    # 已知 origin/<branch> 快照包含相同 config blob 时无需联网重复 push。
    # resume 只消费既有配置；网络离线不应阻断一个已同步的幂等续跑。
    if _path_matches_remote_tracking(repo_root, config_path, branch):
        log.info(logsetup.EVT_CONFIG_SYNCED, path=shown, branch=branch)
        return

    # 本地 config 与远端跟踪快照不同，补推已提交但未推送的配置。
    r = _run(repo_root, "push", "origin", branch)
    if r.returncode != 0:
        raise ValidationError(ui(
            f"Could not push configuration to origin/{branch}; isolated agents would clone stale state.\n"
            f"  {r.stderr.strip()}\n"
            f"  The remote may have diverged. Run `git pull --rebase origin {branch}` and retry.",
            f"config push 到 origin/{branch} 失败 —— 隔离区 agent 会 clone 到旧版。\n"
            f"  {r.stderr.strip()}\n"
            f"  远程可能已分叉,先 `git pull --rebase origin {branch}` 再重试"))
    log.info(logsetup.EVT_CONFIG_SYNCED, path=shown, branch=branch)


def ensure_files_clean(paths, repo_root: str = ".", engine_type=None) -> None:
    """自动提交业务文件前确保没有用户未提交改动。"""
    if not sync_enabled(engine_type):
        return
    relative_paths = _repo_relative_paths(repo_root, paths)
    result = _run(repo_root, "status", "--porcelain", "--", *relative_paths)
    if result.returncode != 0:
        raise ValidationError(ui(
            f"Could not inspect Git status for: {', '.join(relative_paths)}\n"
            f"  {result.stderr.strip()}",
            f"无法检查这些文件的 Git 状态:{', '.join(relative_paths)}\n"
            f"  {result.stderr.strip()}"))
    if not result.stdout.strip():
        return
    joined = " ".join(relative_paths)
    raise NeedsDecision(ui(
        f"OMAC will not mix existing user changes into its automatic plan-output commit: {joined}\n"
        f"Commit them first with `git add {joined} && git commit`, or temporarily "
        f"stash them with `git stash push -- {joined}`, then rerun the plan.",
        f"OMAC 不会把用户已有改动混入自动 plan 产物提交:{joined}\n"
        f"请先执行 `git add {joined} && git commit` 提交，或执行 "
        f"`git stash push -- {joined}` 暂存后重新运行 plan。"),
        report={"paths": relative_paths, "reason": "user_changes_before_plan"})


def repository_revision(repo_root: str = ".") -> str:
    result = _run(repo_root, "rev-parse", "HEAD")
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "unknown"


def ensure_sources_published(paths, repo_root: str = ".", engine_type=None) -> None:
    """Agents clone the remote, so a design source must be committed, unmodified, and on a pushed revision."""
    if not sync_enabled(engine_type):
        return
    relative_paths = _repo_relative_paths(repo_root, paths)
    joined = " ".join(relative_paths)
    status = _run(repo_root, "status", "--porcelain", "--", *relative_paths)
    if status.returncode != 0:
        raise ValidationError(ui(
            f"Could not inspect Git status for the design source: {joined}\n  {status.stderr.strip()}",
            f"无法检查设计来源的 Git 状态:{joined}\n  {status.stderr.strip()}"))
    if status.stdout.strip():
        raise ValidationError(ui(
            f"The design source has uncommitted changes: {joined}\n"
            f"Agents read the pushed revision. Run `git add {joined} && git commit -m docs && git push`, then rerun.",
            f"设计来源有未提交改动:{joined}\n"
            f"Agent 读取的是已推送的版本。请执行 `git add {joined} && git commit -m docs && git push` 后重试。"))
    if not _run(repo_root, "branch", "-r", "--contains", "HEAD").stdout.strip():
        raise ValidationError(ui(
            "The current revision is not on the remote, so agents cannot read the design source. "
            "Run `git push` and rerun.",
            "当前 revision 尚未推送到远端,Agent 读不到设计来源。请执行 `git push` 后重试。"))


def commit_files(paths, message: str, repo_root: str = ".",
                 engine_type=None, *, stop_when_stuck: bool = False) -> bool:
    """把一组 OMAC 状态文件作为一个提交同步到远程。

    ``stop_when_stuck``: once the same step has failed so often that the push backoff sits at its cap, raise
    ``NeedsDecision`` instead of warning again, so a Loop never runs on for hours with state that never reaches git.
    """
    if not sync_enabled(engine_type):
        return False
    relative_paths = _repo_relative_paths(repo_root, paths)
    if not relative_paths:
        return False
    retry_key = _push_retry_key(repo_root, relative_paths)
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
        _clear_push_retry(retry_key)
        return False
    if _push_retry_deferred(retry_key):
        return True

    r = _run(repo_root, "push")
    if r.returncode == 0:
        _clear_push_retry(retry_key)
        return True
    if _is_non_fast_forward(r.stderr):
        failure = _retry_files_push(relative_paths, repo_root)
        if failure is None:
            _clear_push_retry(retry_key)
            return True
        delay = _record_push_failure(retry_key, failure.step)
    else:
        failure = SyncFailure("push", r.stderr.strip(), ui(
            "Check the network and git credentials: `git push` must succeed from this checkout.",
            "检查网络与 git 凭据:在该仓库里 `git push` 必须能成功。"))
        delay = _record_push_failure(retry_key, failure.step)
        log.warning("manifest_sync_failed", step=failure.step, error=failure.error,
                    retry_after_seconds=delay, hint=ui(
                        "OMAC kept the local commit and will retry with exponential backoff; "
                        "cross-machine state may lag",
                        "OMAC 已保留本地 commit，将以指数退避重试；跨机状态可能暂时滞后",
                    ))
    if stop_when_stuck and delay >= _PUSH_RETRY_MAX_SECONDS:
        _raise_sync_stuck(repo_root, failure, _PUSH_RETRY_STATE[retry_key]["failures"])
    return True


def _repo_next_actions(repo: str) -> list:
    return [f"git -C {repo} status", f"git -C {repo} branch -a", f"git -C {repo} switch <branch>"]


def ensure_on_branch(repo_root: str = ".", engine_type=None) -> str:
    """Fail closed unless the checkout is on a branch that tracks an upstream; returns the branch name.

    A detached HEAD (or a branch without upstream) can commit state but never push it, so a Loop that carries on
    would run for hours with state that other machines and agents never see. Sync disabled: no-op, returns "".
    """
    if not sync_enabled(engine_type):
        return ""
    repo = os.path.abspath(repo_root)
    branch = _run(repo_root, "symbolic-ref", "-q", "--short", "HEAD")
    name = branch.stdout.strip() if branch.returncode == 0 else ""
    upstream = _run(repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}") if name else None
    if name and upstream.returncode == 0:
        return name
    why = (ui("HEAD is detached", "HEAD 处于 detached 状态") if not name else
           ui(f"branch '{name}' has no upstream", f"分支 '{name}' 没有 upstream"))
    actions = _repo_next_actions(repo)
    if name:
        actions = [actions[0], f"git -C {repo} branch --set-upstream-to=origin/{name} {name}"]
    raise NeedsDecision(ui(
        f"OMAC state cannot be synced to git: {why} in {repo}. Nothing was dispatched.\n"
        f"  Inspect with `{actions[0]}`, put the checkout back on its branch (`{actions[-1]}`), "
        f"then rerun the same omac command.",
        f"OMAC 状态无法同步到 git:{repo} 的 {why}。本次未派发任何节点。\n"
        f"  用 `{actions[0]}` 检查,把工作区切回分支(`{actions[-1]}`)后重跑同一条 omac 命令。"),
        report={"reason": "repository_not_on_branch", "repository": repo,
                "branch": name or None, "next_actions": actions})


def ensure_state_pushed(path: str, repo_root: str = ".", engine_type=None) -> None:
    """Exit gate of a converged run: the manifest state must have reached the remote, else NeedsDecision (exit 20)."""
    if not sync_enabled(engine_type):
        return
    ensure_on_branch(repo_root, engine_type)
    relative = _repo_relative_paths(repo_root, [path])
    dirty = bool(_run(repo_root, "status", "--porcelain", "--", *relative).stdout.strip())
    if not dirty and not _has_unpushed_files(repo_root, relative):
        return
    if not dirty and _run(repo_root, "push").returncode == 0:
        return
    failure = SyncFailure("push", ui("state is not pushed", "状态尚未推送"), ui(
        "Converged, but the final state never reached the remote.", "DAG 已收敛,但最终状态没有推送到远程。"))
    _raise_sync_stuck(repo_root, failure, 1)


def _raise_sync_stuck(repo_root: str, failure: SyncFailure, failures) -> None:
    repo = os.path.abspath(repo_root)
    actions = [f"git -C {repo} status", f"git -C {repo} log --oneline @{{upstream}}..HEAD", f"git -C {repo} push"]
    raise NeedsDecision(ui(
        f"OMAC state could not be synced to git: step '{failure.step}' failed {failures} times in a row: "
        f"{failure.error}\n  {failure.hint or 'Inspect the repository.'}\n"
        f"  Local commits are kept; other machines and agents see stale state until this is fixed. "
        f"Inspect with `{actions[0]}`, repair, push with `{actions[2]}`, then rerun the same omac command.",
        f"OMAC 状态无法同步到 git:步骤 '{failure.step}' 已连续失败 {failures} 次:{failure.error}\n"
        f"  {failure.hint or '请检查仓库。'}\n"
        f"  本地提交已保留;修复前其他机器与 Agent 看到的是旧状态。用 `{actions[0]}` 检查,修复后 "
        f"`{actions[2]}`,再重跑同一条 omac 命令。"),
        report={"reason": "manifest_sync_failed", "step": failure.step, "error": failure.error,
                "consecutive_failures": failures, "repository": repo, "next_actions": actions})


def commit_manifest(path: str, message: str, repo_root: str = ".",
                    engine_type=None) -> bool:
    """git add manifest + commit + push。sync 关闭或无变更时返回 False。

    push 失败保留本地 commit，并以指数退避抑制重复网络请求/告警；同一步骤连续失败到退避封顶时
    raise NeedsDecision(exit 20),不再静默推进。不自动 merge —— PR 评审是外部门控。
    """
    return commit_files(
        manifest_files(path), message, repo_root=repo_root, engine_type=engine_type,
        stop_when_stuck=True)
