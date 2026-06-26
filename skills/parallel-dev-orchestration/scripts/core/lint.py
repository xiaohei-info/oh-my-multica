# lint.py
from .manifest import Manifest

def _has_cycle(nodes):
    WHITE, GREY, BLACK = 0, 1, 2
    color = {k: WHITE for k in nodes}
    def dfs(u):
        color[u] = GREY
        for v in nodes[u].blocked_by:
            if v not in nodes:        # 未知引用，交给别的规则报
                continue
            if color[v] == GREY:
                return True
            if color[v] == WHITE and dfs(v):
                return True
        color[u] = BLACK
        return False
    return any(color[k] == WHITE and dfs(k) for k in nodes)

def lint(m: Manifest, pool: set) -> list:
    errs = []
    for n in m.nodes.values():
        if n.worker not in pool:
            errs.append(f"node {n.id}: worker '{n.worker}' not in squad pool")
        for b in n.blocked_by:
            if b not in m.nodes:
                errs.append(f"node {n.id}: blocked_by references unknown node '{b}'")
        if n.reviewer is not None:
            if n.reviewer == n.worker:
                errs.append(f"node {n.id}: reviewer must differ from worker")
            if n.reviewer not in pool:
                errs.append(f"node {n.id}: reviewer '{n.reviewer}' not in squad pool")
    if _has_cycle(m.nodes):
        errs.append("manifest DAG has a cycle")
    return errs
