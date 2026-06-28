"""
工具函数模块
"""
import os
import subprocess

# git 回写开关：默认关闭，避免装完跑测试/demo 时污染业务项目仓库。
# 真实跨机器协作时显式打开（README「git 同步开关」一节）。
_TRUTHY = {"1", "true", "yes", "on"}


def git_sync_enabled() -> bool:
    """manifest 是否回写 git（add+commit+push）。默认关闭。

    打开方式（任一）：环境变量 ORCH_GIT_SYNC=1|true|yes|on。
    """
    return os.environ.get("ORCH_GIT_SYNC", "").strip().lower() in _TRUTHY


def commit_manifest(path: str, message: str, repo_root: str = ".") -> bool:
    """git add <path> + git commit + git push。

    默认关闭（ORCH_GIT_SYNC 未开）：直接跳过，manifest 仍以本地文件为口径，
    不碰 git——避免污染业务仓。打开后才回写。
    幂等：无变更时跳过。push 失败醒目告警但不中断编排。
    不自动 merge（PR 评审是外部门控）。
    """
    if not git_sync_enabled():
        return False
    abs_path = path if path.startswith("/") else f"{repo_root}/{path}"
    r = subprocess.run(["git", "add", abs_path], cwd=repo_root,
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"git add 失败: {r.stderr.strip()}")
        return False
    r = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo_root,
                       capture_output=True, text=True)
    if r.returncode == 0:
        return False
    r = subprocess.run(["git", "commit", "-m", message], cwd=repo_root,
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"git commit 失败: {r.stderr.strip()}")
        return False
    r = subprocess.run(["git", "push"], cwd=repo_root,
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"git push 失败: {r.stderr.strip()}")
        print(f"  manifest 已本地 commit 但未 push——跨机器口径可能滞后！")
    return True
