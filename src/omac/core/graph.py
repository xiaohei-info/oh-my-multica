"""DAG 图算法与节点状态分类(唯一定义处)。

术语约定(docs/design/README.md §10.2):就绪节点(ready_nodes)、进行中节点(running_nodes)。
节点状态分三类,其余是 todo:
- RUNNING:进行中节点(ci_check / merging 是 manifest 侧细分态,平台侧映射见 pipeline.delivery);
- NEEDS_DECISION:等调用者显式决策(exit 20);blocked 可经 `omac node retry` 回到 todo,不是终态;
- SATISFIED:满足下游依赖;abandoned 视同满足(§7.5)。DAG 收敛 = 没有 RUNNING 与 NEEDS_DECISION 节点。
"""

RUNNING = frozenset({"in_progress", "ci_check", "in_review", "merging"})
NEEDS_DECISION = frozenset({"blocked", "failed"})
SATISFIED = frozenset({"done", "abandoned"})


def running_nodes(nodes: dict) -> list:
    """进行中节点:status 属于 RUNNING 的节点 key(nodes: key -> 带 .status 的节点)。"""
    return [key for key, node in nodes.items() if node.status in RUNNING]


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
