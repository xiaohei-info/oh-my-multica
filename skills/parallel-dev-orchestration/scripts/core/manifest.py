# manifest.py
from dataclasses import dataclass, field
import yaml

_UNSET = object()  # sentinel: 参数未传（区别于 None=显式清空）

@dataclass
class Node:
    id: str
    worker: str
    blocked_by: list = field(default_factory=list)
    title: str | None = None
    description: str | None = None
    reviewer: str | None = None
    risk: str | None = None
    gate: dict | None = None
    work_item_id: str | None = None   # 平台返回的 work item id（Phase 2 回填）
    status: str = "todo"           # manifest 携带的节点状态

@dataclass
class Manifest:
    meta: dict
    nodes: dict  # id -> Node

def load_manifest(path: str) -> Manifest:
    with open(path) as f:
        raw = yaml.safe_load(f)
    nodes = {}
    for n in raw.get("nodes", []):
        if "id" not in n:
            raise ValueError("node missing 'id'")
        if not n.get("worker"):
            raise ValueError(f"node {n['id']} missing required 'worker'")
        nodes[n["id"]] = Node(
            id=n["id"],
            worker=n["worker"],
            blocked_by=list(n.get("blocked_by", [])),
            title=n.get("title"),
            description=n.get("description"),
            reviewer=n.get("reviewer"),
            risk=n.get("risk"),
            gate=n.get("gate"),
            work_item_id=n.get("work_item_id"),
            status=n.get("status", "todo"),
        )
    return Manifest(meta=raw.get("meta", {}), nodes=nodes)

def save_manifest(manifest: Manifest, path: str):
    """把 manifest 序列化回 YAML，原地覆盖。

    用显式 schema dump，保证 work_item_id/status 等字段齐全、可读。
    """
    node_list = []
    for key in manifest.nodes:  # 保留 YAML 里的声明顺序
        n = manifest.nodes[key]
        node = {
            "id": n.id,
            "worker": n.worker,
            "blocked_by": list(n.blocked_by),
            "work_item_id": n.work_item_id,
            "status": n.status,
        }
        if n.title is not None:
            node["title"] = n.title
        if n.description is not None:
            node["description"] = n.description
        if n.reviewer is not None:
            node["reviewer"] = n.reviewer
        if n.risk is not None:
            node["risk"] = n.risk
        if n.gate is not None:
            node["gate"] = n.gate
        node_list.append(node)

    data = {"meta": manifest.meta, "nodes": node_list}
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

def set_node(manifest: Manifest, key: str, *, work_item_id=_UNSET, status: str | None = None):
    """仅改传入字段，其余不动。

    work_item_id 用 _UNSET 哨兵区分「不传」与「显式传 None=清空」。
    status 用 None 表示不传（None 不是合法状态值）。
    """
    if key not in manifest.nodes:
        raise KeyError(f"node {key} not in manifest")
    n = manifest.nodes[key]
    if work_item_id is not _UNSET:
        n.work_item_id = work_item_id
    if status is not None:
        n.status = status
