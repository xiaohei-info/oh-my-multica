"""DAG 图算法。

术语约定(设计文档 §10.2):就绪节点(ready_nodes)、进行中节点(running)。
"""

DONE = "done"
# 依赖满足:done 或 abandoned(abandoned 上游视同依赖已满足,§2.4 P1.4)
SATISFIED = {"done", "abandoned"}
TERMINAL = {"done", "cancelled", "abandoned"}
RUNNING = {"in_progress", "in_review"}  # 进行中节点的状态集合


def is_done(issue) -> bool:
    return issue["status"] == DONE


def ready_nodes(issues: dict) -> list:
    """就绪节点:status==todo 且所有 blocked_by 节点已 done(连续推进,不分层)。"""
    out = []
    for key, it in issues.items():
        if it["status"] != "todo":
            continue
        if all(issues.get(b, {}).get("status") in SATISFIED for b in it["blocked_by"]):
            out.append(key)
    return out


def all_terminal(issues: dict) -> bool:
    return all(it["status"] in TERMINAL for it in issues.values())


def downstream_of(issues: dict, failed_keys: set) -> set:
    """所有(传递)依赖了 failed_keys 的节点 key。"""
    rev = {k: set() for k in issues}          # blocker -> dependents
    for k, it in issues.items():
        for b in it["blocked_by"]:
            if b in rev:
                rev[b].add(k)
    out, stack = set(), list(failed_keys)
    while stack:
        cur = stack.pop()
        for dep in rev.get(cur, ()):
            if dep not in out:
                out.add(dep)
                stack.append(dep)
    return out
