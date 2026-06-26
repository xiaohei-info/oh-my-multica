"""
工具函数模块
"""
import subprocess


def commit_manifest(path: str, message: str, repo_root: str = ".") -> bool:
    """git add <path> + git commit + git push。

    幂等：无变更时跳过。push 失败醒目告警但不中断编排。
    不自动 merge（PR 评审是外部门控）。
    """
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

