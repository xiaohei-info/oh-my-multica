"""`.omac` 状态回写 git —— 隔离区 agent 与跨机编排的同步地基。

架构:agent 在隔离工作区只能 clone main,信息来源只有远程仓库。于是:
- config.yaml 必须已 push 到 main,否则 agent 读不到 → 派单前自动同步(ensure_config_synced)
- manifest 是编排器状态,跨机 resume 靠它 → tick 后回写(commit_manifest)

开关:真实引擎(multica)默认开(架构要求);mock 本地跑默认关(不碰业务仓库);
OMAC_GIT_SYNC 显式覆盖(truthy 强开 / falsy 强关)。
"""
import os
import subprocess

from ..errors import ValidationError

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
        raise ValidationError(
            f"config 不存在: {config_path} —— 先运行 `omac init` 生成配置")

    # 有未提交改动就自动提交(隔离区 agent clone 到的必须是最新 config)
    if _run(repo_root, "status", "--porcelain", "--", config_path).stdout.strip():
        _run(repo_root, "add", config_path)
        r = _run(repo_root, "commit", "-m", f"chore(omac): sync {config_path}")
        if r.returncode != 0:
            raise ValidationError(f"config 自动提交失败: {r.stderr.strip()}")

    # 补推(覆盖「已提交未推送」;已同步时 Everything up-to-date 幂等)
    r = _run(repo_root, "push", "origin", branch)
    if r.returncode != 0:
        raise ValidationError(
            f"config push 到 origin/{branch} 失败 —— 隔离区 agent 会 clone 到旧版。\n"
            f"  {r.stderr.strip()}\n"
            f"  远程可能已分叉,先 `git pull --rebase origin {branch}` 再重试")
    print(f"已同步 {config_path} → origin/{branch}")


def commit_manifest(path: str, message: str, repo_root: str = ".",
                    engine_type=None) -> bool:
    """git add <path> + commit + push。sync 关闭(gating)或无变更时跳过,返回 False。

    push 失败醒目告警但不中断编排(跨机口径可能滞后,但不阻塞本机推进);
    不自动 merge —— PR 评审是外部门控。
    """
    if not sync_enabled(engine_type):
        return False
    r = _run(repo_root, "add", path)
    if r.returncode != 0:
        print(f"git add 失败: {r.stderr.strip()}")
        return False
    if _run(repo_root, "diff", "--cached", "--quiet", "--", path).returncode == 0:
        return False  # 无变更,幂等跳过
    r = _run(repo_root, "commit", "-m", message)
    if r.returncode != 0:
        print(f"git commit 失败: {r.stderr.strip()}")
        return False
    r = _run(repo_root, "push")
    if r.returncode != 0:
        print(f"git push 失败: {r.stderr.strip()}")
        print("  manifest 已本地 commit 但未 push——跨机器口径可能滞后!")
    return True
