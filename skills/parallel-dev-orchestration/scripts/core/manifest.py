# manifest.py
from dataclasses import dataclass, field
import os
import re
import yaml

_UNSET = object()  # sentinel: 参数未传（区别于 None=显式清空）

# 仅匹配 ${VAR} 与 ${VAR:-default}，不碰裸 $VAR（避免误伤 description 里的 $ 文本）
_ENV_PAT = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def _expand_env(value):
    """递归把 manifest 里的 ${VAR} / ${VAR:-默认值} 用环境变量展开。

    让 manifest 不必把 squad/workspace 等 id 硬写进文件——CI/他人克隆后
    设环境变量即可，未设则用默认值。VAR 未设且无默认值时保留原样（显式可见）。
    """
    if isinstance(value, str):
        def sub(m):
            name, default = m.group(1), m.group(2)
            env = os.environ.get(name)
            if env is not None:
                return env
            return default if default is not None else m.group(0)
        return _ENV_PAT.sub(sub, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value

@dataclass
class Contract:
    objective: str | None = None
    source_of_truth: list = field(default_factory=list)
    required_contracts: list = field(default_factory=list)
    acceptance: list = field(default_factory=list)
    non_goals: list = field(default_factory=list)
    verification_commands: list = field(default_factory=list)
    pr_base: str | None = None
    coverage_gate: int | float = 90


def _load_contract(raw):
    if raw is None:
        return None
    return Contract(
        objective=raw.get("objective"),
        source_of_truth=list(raw.get("source_of_truth", [])),
        required_contracts=list(raw.get("required_contracts", [])),
        acceptance=list(raw.get("acceptance", [])),
        non_goals=list(raw.get("non_goals", [])),
        verification_commands=list(raw.get("verification_commands", [])),
        pr_base=raw.get("pr_base"),
        coverage_gate=raw.get("coverage_gate", 90),
    )


def _dump_contract(contract):
    if contract is None:
        return None
    data = {
        "objective": contract.objective,
        "acceptance": list(contract.acceptance),
        "non_goals": list(contract.non_goals),
        "verification_commands": list(contract.verification_commands),
        "pr_base": contract.pr_base,
    }
    if contract.source_of_truth:
        data["source_of_truth"] = list(contract.source_of_truth)
    if contract.required_contracts:
        data["required_contracts"] = list(contract.required_contracts)
    if contract.coverage_gate != 90:
        data["coverage_gate"] = contract.coverage_gate
    return data


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
    contract: Contract | None = None
    work_item_id: str | None = None   # 平台返回的 work item id（Phase 2 回填）
    status: str = "todo"           # manifest 携带的节点状态

    def __post_init__(self):
        if isinstance(self.contract, dict):
            self.contract = _load_contract(self.contract)

@dataclass
class Manifest:
    meta: dict
    nodes: dict  # id -> Node

def load_manifest(path: str) -> Manifest:
    with open(path) as f:
        raw = _expand_env(yaml.safe_load(f))
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
            contract=_load_contract(n.get("contract")),
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
        if n.contract is not None:
            node["contract"] = _dump_contract(n.contract)
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
