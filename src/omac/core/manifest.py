# manifest.py
from dataclasses import dataclass, field
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import yaml

from ..errors import ValidationError
from ..i18n import ui

_UNSET = object()  # sentinel: 参数未传（区别于 None=显式清空）

# 仅匹配 ${VAR} 与 ${VAR:-default}，不碰裸 $VAR（避免误伤 description 里的 $ 文本）
_ENV_PAT = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def _expand_env(value):
    """递归把 manifest 里的 ${VAR} / ${VAR:-默认值} 用环境变量展开。

    让 manifest 不必把 workspace 等 id 硬写进文件——CI/他人克隆后
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
class QualityContract:
    required_outcomes: list = field(default_factory=list)
    business_tests: list = field(default_factory=list)
    runtime_data_policy: str | None = None


def _load_quality(raw):
    if raw is None:
        return None
    if isinstance(raw, QualityContract):
        return raw
    if not isinstance(raw, dict):
        raise ValueError("contract.quality must be an object")
    required_outcomes = raw.get("required_outcomes", [])
    if not isinstance(required_outcomes, list):
        raise ValueError("quality.required_outcomes must be a list")
    business_tests = raw.get("business_tests", [])
    if not isinstance(business_tests, list):
        raise ValueError("quality.business_tests must be a list")
    return QualityContract(
        required_outcomes=list(required_outcomes),
        business_tests=list(business_tests),
        runtime_data_policy=raw.get("runtime_data_policy"),
    )


def _dump_quality(quality):
    if quality is None:
        return None
    return {
        "required_outcomes": list(quality.required_outcomes),
        "business_tests": list(quality.business_tests),
        "runtime_data_policy": quality.runtime_data_policy,
    }


_CONTRACT_LIST_FIELDS = (
    "source_of_truth",
    "required_contracts",
    "acceptance",
    "non_goals",
    "verification_commands",
    "integration_gates",
    "scope_paths",
)


def _load_contract_list(raw: dict, field_name: str) -> list:
    value = raw.get(field_name, [])
    if not isinstance(value, list):
        raise ValueError(f"contract.{field_name} must be a list")
    return list(value)


def _load_optional_contract_string(raw: dict, field_name: str):
    if field_name not in raw:
        return None
    value = raw[field_name]
    if not isinstance(value, str):
        raise ValueError(f"contract.{field_name} must be a string")
    return value


@dataclass
class Contract:
    objective: str | None = None
    source_of_truth: list = field(default_factory=list)
    required_contracts: list = field(default_factory=list)
    acceptance: list = field(default_factory=list)
    non_goals: list = field(default_factory=list)
    verification_commands: list = field(default_factory=list)
    integration_gates: list = field(default_factory=list)
    pr_base: str | None = None
    coverage_gate: int | float = 90
    acceptance_doc: dict | list | None = None
    # 主要代码归属范围(可选、非穷举白名单):用于表达节点稳定的模块边界、降低
    # 并行冲突。完成 contract 必需的配套文件可扩展,但需在 PR/verification 说明。
    scope_paths: list = field(default_factory=list)
    quality: QualityContract | None = None

    def __post_init__(self):
        if isinstance(self.quality, dict):
            self.quality = _load_quality(self.quality)


def _load_contract(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("contract must be an object")
    list_fields = {
        field_name: _load_contract_list(raw, field_name)
        for field_name in _CONTRACT_LIST_FIELDS
    }
    return Contract(
        objective=_load_optional_contract_string(raw, "objective"),
        source_of_truth=list_fields["source_of_truth"],
        required_contracts=list_fields["required_contracts"],
        acceptance=list_fields["acceptance"],
        non_goals=list_fields["non_goals"],
        verification_commands=list_fields["verification_commands"],
        integration_gates=list_fields["integration_gates"],
        pr_base=_load_optional_contract_string(raw, "pr_base"),
        coverage_gate=raw.get("coverage_gate", 90),
        acceptance_doc=raw.get("acceptance_doc"),
        scope_paths=list_fields["scope_paths"],
        quality=_load_quality(raw.get("quality")),
    )


def _dump_contract(contract):
    if contract is None:
        return None
    data = {
        "acceptance": list(contract.acceptance),
        "non_goals": list(contract.non_goals),
        "verification_commands": list(contract.verification_commands),
        "integration_gates": list(contract.integration_gates),
    }
    if contract.objective is not None:
        data["objective"] = contract.objective
    if contract.pr_base is not None:
        data["pr_base"] = contract.pr_base
    if contract.source_of_truth:
        data["source_of_truth"] = list(contract.source_of_truth)
    if contract.required_contracts:
        data["required_contracts"] = list(contract.required_contracts)
    if contract.scope_paths:
        data["scope_paths"] = list(contract.scope_paths)
    if contract.coverage_gate != 90:
        data["coverage_gate"] = contract.coverage_gate
    if contract.quality is not None:
        data["quality"] = _dump_quality(contract.quality)
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
    # P4.2:done = 已合入集成分支;记录合入信息(merged: true / 时间)
    merged: bool = False
    merged_at: str | None = None

    def __post_init__(self):
        if isinstance(self.contract, dict):
            self.contract = _load_contract(self.contract)

@dataclass
class Manifest:
    meta: dict
    nodes: dict  # id -> Node
    project_root: str | None = None


def project_root_from_manifest_path(manifest_path: str) -> str:
    parent = Path(manifest_path).resolve().parent
    return str(parent.parent if parent.name == ".omac" else parent)


def _require_manifest_mapping(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("manifest must be an object")
    return raw


def _require_manifest_shape(raw) -> dict:
    raw = _require_manifest_mapping(raw)
    if not isinstance(raw.get("meta", {}), dict):
        raise ValueError("manifest.meta must be an object")

    nodes = raw.get("nodes", [])
    if not isinstance(nodes, list):
        raise ValueError("manifest.nodes must be a list")
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ValueError(f"manifest.nodes[{index}] must be an object")
    return raw


def _build_nodes(raw) -> dict:
    """从已展开的 raw dict 构造 {id: Node}(共享于文件加载与不落盘文本解析)。"""
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
            merged=bool(n.get("merged", False)),
            merged_at=n.get("merged_at"),
        )
    return nodes


def load_manifest(path: str) -> Manifest:
    """从文件路径加载 manifest(环境变量展开 + schema 校验)。"""
    with open(path) as f:
        raw = _require_manifest_shape(_expand_env(yaml.safe_load(f)))
    return Manifest(
        meta=raw.get("meta", {}),
        nodes=_build_nodes(raw),
        project_root=project_root_from_manifest_path(path),
    )


def loads_manifest(text: str, *, project_root: str | None = None) -> Manifest:
    """从 YAML 文本解析 manifest(不落盘,供 pipeline 直接消费 LLM 产出的 manifest)。"""
    raw = _require_manifest_shape(_expand_env(yaml.safe_load(text)))
    return Manifest(
        meta=raw.get("meta", {}),
        nodes=_build_nodes(raw),
        project_root=(
            str(Path(project_root).resolve()) if project_root is not None else None
        ),
    )

def save_manifest(manifest: Manifest, path: str):
    """把 manifest 原子序列化回 YAML。

    临时文件与目标文件放在同一目录,完整 flush+fsync 后用 os.replace 替换。
    进程被终止或 dump 失败时,旧 manifest 保持完整,不会留下可被 YAML 当成
    合法短文件读取的半截状态。
    """
    node_list = []
    for key in manifest.nodes:  # 保留 YAML 里的声明顺序
        n = manifest.nodes[key]
        node = {
            "id": n.id,
            "worker": n.worker,
            "blocked_by": list(n.blocked_by),
        }
        if n.work_item_id is not None:
            node["work_item_id"] = n.work_item_id
        if n.status != "todo":
            node["status"] = n.status
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
        if n.merged:
            node["merged"] = True
            node["merged_at"] = n.merged_at
        node_list.append(node)

    data = {"meta": manifest.meta, "nodes": node_list}
    target = os.path.abspath(path)
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{os.path.basename(target)}.", suffix=".tmp", dir=directory)
    try:
        if os.path.exists(target):
            os.chmod(temporary, stat.S_IMODE(os.stat(target).st_mode))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(
                data, f, default_flow_style=False,
                allow_unicode=True, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


@contextmanager
def manifest_write_lock(path: str):
    """同一台机器只允许一个 dag run/tick 修改指定 manifest。"""
    key = hashlib.sha256(os.path.abspath(path).encode("utf-8")).hexdigest()[:24]
    lock_path = os.path.join(tempfile.gettempdir(), f"omac-manifest-{key}.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValidationError(ui(
                f"Another `omac dag run` or `tick` is already modifying manifest: {path}\n"
                "Wait for it to exit, or confirm the stale process has stopped before retrying.",
                f"已有另一个 omac dag run/tick 正在修改 manifest: {path}\n"
                "提示:等待现有进程退出,或确认旧进程已停止后再重试。")) from exc
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

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

def merge_increment(manifest: Manifest, increment: Manifest) -> None:
    """并入增量 fix 节点到原 manifest(原地修改,§7.6)。

    - id 冲突 -> raise ValueError(「并入原 manifest,id 冲突报错」)
    - 已存在(含已 done)节点不动(「已 done 节点不动」)
    - 新增节点按 manifest.nodes 写入顺序追加(保留原 DAG 顺序,可续跑)
    """
    for node_id, node in increment.nodes.items():
        if node_id in manifest.nodes:
            raise ValueError(f"node id conflict: {node_id!r} already in manifest")
        manifest.nodes[node_id] = node
