"""`.omac` 状态回写 git —— 隔离区 agent 与跨机编排的同步地基。

架构:agent 在隔离工作区只能 clone main,信息来源只有远程仓库。于是:
- config.yaml 必须已 push 到 main,否则 agent 读不到 → 派单前硬门(assert_config_pushed)
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


def assert_config_pushed(config_path: str, branch: str = "main",
                         repo_root: str = ".") -> None:
    """派单前门:config 必须 tracked、无未提交改动、且已 push 到 origin/<branch>。

    隔离区 agent clone main 后靠这份 config 连平台;本地未推送即 agent 读不到,
    与其让它在隔离区里神秘失败,不如派单前当场硬报错 + 给补救命令。
    """
    abs_path = config_path if config_path.startswith("/") else f"{repo_root}/{config_path}"
    if not os.path.exists(abs_path):
        raise ValidationError(
            f"config 不存在: {config_path} —— 先运行 `omac init` 生成配置")

    # 未提交/未跟踪:git status --porcelain 有输出即脏
    st = _run(repo_root, "status", "--porcelain", "--", config_path)
    if st.stdout.strip():
        raise ValidationError(
            f"{config_path} 有未提交改动 —— 隔离区 agent clone {branch} 读到的会是旧版。\n"
            f"  先提交并推送:git add {config_path} && git commit -m 'chore: omac config' "
            f"&& git push origin {branch}")

    # 已提交但未推送:@{upstream}..HEAD 里有触及 config 的提交
    rev = _run(repo_root, "rev-list", "@{upstream}..HEAD", "--", config_path)
    if rev.returncode != 0:
        raise ValidationError(
            f"当前分支无 upstream(无法确认 {config_path} 是否已推送)。\n"
            f"  先推送并设 upstream:git push -u origin {branch}")
    if rev.stdout.strip():
        raise ValidationError(
            f"{config_path} 已提交但未推送到 origin/{branch} —— 隔离区 agent 读不到最新版。\n"
            f"  推送:git push origin {branch}")


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
