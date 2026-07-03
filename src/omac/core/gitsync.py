"""manifest 的 git 回写(可选,默认关闭)。现有资产平移,环境变量更名 OMAC_GIT_SYNC。"""
import os
import subprocess

_TRUTHY = {"1", "true", "yes", "on"}


def git_sync_enabled() -> bool:
    """manifest 是否回写 git(add+commit+push)。默认关闭,避免污染业务项目仓库。"""
    return os.environ.get("OMAC_GIT_SYNC", "").strip().lower() in _TRUTHY


def commit_manifest(path: str, message: str, repo_root: str = ".") -> bool:
    """git add <path> + commit + push。默认关闭时直接跳过;幂等:无变更跳过。

    push 失败醒目告警但不中断编排;不自动 merge(PR 评审是外部门控)。
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
        print("  manifest 已本地 commit 但未 push——跨机器口径可能滞后!")
    return True
