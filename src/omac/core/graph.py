"""DAG 图算法。

术语约定(设计文档 §10.2):就绪节点(ready_nodes)、进行中节点(running)。
"""

DONE = "done"
# 依赖「已满足」的上游状态:done 正常满足,abandoned 视同满足(§2.4 P1.4 / §7.5)
SATISFIED = {DONE, "abandoned"}
TERMINAL = {"done", "cancelled", "abandoned"}
RUNNING = {"in_progress", "in_review"}  # 进行中节点的状态集合
# 细分态 ci_check / merging 是 manifest 中间切片、不是终态,故意不进
# SATISFIED / TERMINAL / RUNNING —— 它们的终态由平台侧 IN_REVIEW / BLOCKED 接管,
# 因此也不应被视为「已满足」上游依赖。进行中节点的显式枚举在
# loop.py 的 RUNNING_STATUSES(已含 ci_check)中维护,本处不必重复。


def is_done(issue) -> bool:
    return issue["status"] == DONE


def ready_nodes(issues: dict) -> list:
    """就绪节点:status==todo 且所有 blocked_by 节点的依赖已满足。

    依赖「已满足」= 上游 done 或 abandoned(§7.5:abandoned 上游视同依赖已满足,
    下游可继续推进;报告中会对经过 abandoned 上游的节点加注记)。
    """
    out = []
    for key, it in issues.items():
        if it["status"] != "todo":
            continue
        if all(issues.get(b, {}).get("status") in SATISFIED for b in it["blocked_by"]):
            out.append(key)
    return out


def all_terminal(issues: dict) -> bool:
    return all(it["status"] in TERMINAL for it in issues.values())


def node_waves(nodes: dict) -> dict:
    """每个节点的拓扑层级(wave):根节点(无 blocked_by)wave=0,其余 = max(前置 wave)+1。

    用于 dag show 的按 wave 拓扑摘要。含环保护(环上节点回落到 0)。
    """
    waves, visiting = {}, set()

    def compute(k):
        if k in waves:
            return waves[k]
        if k in visiting:
            return 0
        visiting.add(k)
        deps = [d for d in nodes[k].blocked_by if d in nodes]
        waves[k] = max((compute(d) for d in deps), default=-1) + 1
        visiting.discard(k)
        return waves[k]

    for k in nodes:
        compute(k)
    return waves


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
