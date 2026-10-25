# manifest.py
from dataclasses import dataclass, field
from contextlib import contextmanager
import copy
from enum import Enum
import fcntl
import hashlib
import os
import re
import stat
import tempfile
import yaml

from ..errors import ValidationError
from ..i18n import ui

_UNSET = object()  # sentinel: 参数未传（区别于 None=显式清空）

# 仅匹配 ${VAR} 与 ${VAR:-default}，不碰裸 $VAR（避免误伤 description 里的 $ 文本）
_ENV_PAT = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")
_SHELL_COMMAND_KEYS = frozenset({"commands", "verification_commands"})


def _expand_env(value, *, preserve_shell=False):
    """Expand manifest values without consuming shell command placeholders.

    Manifest/config values may use ``${VAR}`` or ``${VAR:-default}``, but
    verification and integration command strings must reach the execution
    environment unchanged so the shell can resolve their variables at runtime.
    """
    if isinstance(value, str):
        if preserve_shell:
            return value

        def sub(m):
            name, default = m.group(1), m.group(2)
            env = os.environ.get(name)
            if env is not None:
                return env
            return default if default is not None else m.group(0)
        return _ENV_PAT.sub(sub, value)
    if isinstance(value, dict):
        return {
            k: _expand_env(v, preserve_shell=(preserve_shell or k in _SHELL_COMMAND_KEYS))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_expand_env(v, preserve_shell=preserve_shell) for v in value]
    return value

class EvidenceMode(str, Enum):
    """Authoritative evidence class for one independently reviewable node."""

    FIXTURE = "fixture"
    ARTIFACT = "artifact"
    LIVE = "live"


class _MissingContractField(Enum):
    VALUE = "missing"


MISSING_CONSUMES = _MissingContractField.VALUE


@dataclass(frozen=True)
class ProducedArtifact:
    """Stable artifact identity produced by one node."""

    artifact_id: str


@dataclass(frozen=True)
class ConsumedArtifact:
    """One exact artifact input and the upstream node that owns it."""

    artifact_id: str
    producer: str
    evidence_mode: EvidenceMode


def _load_evidence_mode(value):
    if isinstance(value, EvidenceMode) or value is None:
        return value
    try:
        return EvidenceMode(value)
    except (TypeError, ValueError):
        # Preserve invalid input so lint can return a repairable schema error.
        return value


def _load_produced_artifact(value):
    if isinstance(value, ProducedArtifact):
        return value
    if isinstance(value, dict):
        return ProducedArtifact(artifact_id=value.get("artifact_id"))
    return value


def _load_consumed_artifact(value):
    if isinstance(value, ConsumedArtifact):
        return value
    if isinstance(value, dict):
        return ConsumedArtifact(
            artifact_id=value.get("artifact_id"),
            producer=value.get("producer"),
            evidence_mode=_load_evidence_mode(value.get("evidence_mode")),
        )
    return value


def _dump_evidence_mode(value):
    return value.value if isinstance(value, EvidenceMode) else value


def _dump_produced_artifact(value):
    if isinstance(value, ProducedArtifact):
        return {"artifact_id": value.artifact_id}
    return value


def _dump_consumed_artifact(value):
    if isinstance(value, ConsumedArtifact):
        return {
            "artifact_id": value.artifact_id,
            "producer": value.producer,
            "evidence_mode": _dump_evidence_mode(value.evidence_mode),
        }
    return value


@dataclass
class Contract:
    objective: str | None = None
    source_of_truth: list = field(default_factory=list)
    required_contracts: list = field(default_factory=list)
    acceptance: list = field(default_factory=list)
    # New manifests use explicit responsibility semantics. ``acceptance`` is
    # retained as the published legacy full-flow claim field and is never
    # reinterpreted as a trace-only reference.
    acceptance_claims: list = field(default_factory=list)
    acceptance_contributions: list = field(default_factory=list)
    acceptance_refs: list = field(default_factory=list)
    non_goals: list = field(default_factory=list)
    verification_commands: list = field(default_factory=list)
    integration_gates: list = field(default_factory=list)
    pr_base: str | None = None
    coverage_gate: int | float = 90
    acceptance_doc: dict | list | None = None
    # 主要代码归属范围(可选、非穷举白名单):用于表达节点稳定的模块边界、降低
    # 并行冲突。完成 contract 必需的配套文件可扩展,但需在 PR/verification 说明。
    scope_paths: list = field(default_factory=list)
    # 可选的 typed artifact boundary。consumes 使用 presence-aware 值：sentinel 表示
    # 旧运行 manifest 未声明，[] 表示明确无外部输入，非空列表表示严格 allowlist；
    # None 只保留显式 YAML null，供 lint 失败关闭。
    evidence_mode: EvidenceMode | None = None
    produces: list[ProducedArtifact] = field(default_factory=list)
    consumes: list[ConsumedArtifact] | None | _MissingContractField = MISSING_CONSUMES


def _load_contract(raw):
    if raw is None:
        return None
    raw_produces = raw.get("produces", [])
    raw_consumes = raw["consumes"] if "consumes" in raw else MISSING_CONSUMES
    return Contract(
        objective=raw.get("objective"),
        source_of_truth=list(raw.get("source_of_truth", [])),
        required_contracts=list(raw.get("required_contracts", [])),
        acceptance=list(raw.get("acceptance", [])),
        acceptance_claims=list(raw.get("acceptance_claims", [])),
        acceptance_contributions=list(raw.get("acceptance_contributions", [])),
        acceptance_refs=list(raw.get("acceptance_refs", [])),
        non_goals=list(raw.get("non_goals", [])),
        verification_commands=list(raw.get("verification_commands", [])),
        integration_gates=list(raw.get("integration_gates", [])),
        pr_base=raw.get("pr_base"),
        coverage_gate=raw.get("coverage_gate", 90),
        acceptance_doc=raw.get("acceptance_doc"),
        scope_paths=list(raw.get("scope_paths", [])),
        evidence_mode=_load_evidence_mode(raw.get("evidence_mode")),
        produces=(
            [_load_produced_artifact(value) for value in raw_produces]
            if isinstance(raw_produces, list) else raw_produces
        ),
        consumes=(
            [_load_consumed_artifact(value) for value in raw_consumes]
            if isinstance(raw_consumes, list) else raw_consumes
        ),
    )


def _dump_contract(contract):
    """Serialize the canonical executable contract, omitting semantic defaults.

    ``coverage_gate=90`` and omitted optional collections are restored by
    ``_load_contract``. Explicit empty ``consumes`` remains serialized because
    it is a policy, not a default. Plan-owned acceptance content is persisted once in the
    sibling acceptance artifact referenced by ``meta.acceptance_file`` rather
    than copied into every node's ``acceptance_doc``.
    """
    if contract is None:
        return None
    data = {
        "objective": contract.objective,
        "non_goals": list(contract.non_goals),
        "verification_commands": list(contract.verification_commands),
        "integration_gates": list(contract.integration_gates),
        "pr_base": contract.pr_base,
    }
    if contract.acceptance:
        data["acceptance"] = list(contract.acceptance)
    if contract.acceptance_claims:
        data["acceptance_claims"] = list(contract.acceptance_claims)
    if contract.acceptance_contributions:
        data["acceptance_contributions"] = list(
            contract.acceptance_contributions)
    if contract.acceptance_refs:
        data["acceptance_refs"] = list(contract.acceptance_refs)
    if contract.source_of_truth:
        data["source_of_truth"] = list(contract.source_of_truth)
    if contract.required_contracts:
        data["required_contracts"] = list(contract.required_contracts)
    if contract.scope_paths:
        data["scope_paths"] = list(contract.scope_paths)
    if contract.evidence_mode is not None:
        data["evidence_mode"] = _dump_evidence_mode(contract.evidence_mode)
    if contract.produces:
        data["produces"] = [
            _dump_produced_artifact(value) for value in contract.produces
        ]
    if contract.consumes is not MISSING_CONSUMES:
        data["consumes"] = (
            [_dump_consumed_artifact(value) for value in contract.consumes]
            if isinstance(contract.consumes, list) else contract.consumes
        )
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
    # P4.2:done = 已合入集成分支;记录合入信息(merged: true / 时间)
    merged: bool = False
    merged_at: str | None = None
    merge_request_state: str | None = None
    # 轻量恢复标记:平台上存在 OMAC 写入的待消费恢复事实
    # (worker_handoff / reviewer_run_baseline / decision_required)时为 True。
    # reconcile 按需读取据此把 blocked/failed 节点留在活跃集;标记在事实被
    # 消费/清除时移除,观察不到标记的存量事实由全量审计轮兜底。
    recovery_marker: bool = False

    def __post_init__(self):
        if isinstance(self.contract, dict):
            self.contract = _load_contract(self.contract)


def confirmed_merge_is_closed(node: Node) -> bool:
    """Return whether the node carries an active confirmed-merge closure."""
    return bool(
        node.merged
        and node.merged_at
        and node.merge_request_state is None
    )


def clear_confirmed_merge(node: Node) -> None:
    """Explicitly retire merge closure before an operator-authorized reopen."""
    node.merged = False
    node.merged_at = None
    node.merge_request_state = None

@dataclass
class Manifest:
    meta: dict
    nodes: dict  # id -> Node
    # What the last load/save left on disk, so a save rewrites only files whose content changed.
    _disk: dict = field(default_factory=dict, repr=False, compare=False)

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
            merge_request_state=n.get("merge_request_state"),
            recovery_marker=bool(n.get("recovery_marker", False)),
        )
    return nodes


_RUNTIME_KEYS = ("work_item_id", "status", "merged", "merged_at", "merge_request_state", "recovery_marker")
_NODE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_STATE_HEAD = re.compile(rb"# sha256: ([0-9a-f]{64})")
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def state_dir(path: str) -> str:
    """Directory holding one small runtime-state file per node: ``.omac/<name>.yaml`` -> ``.omac/<name>.state``."""
    root, ext = os.path.splitext(path)
    return (root if ext in (".yaml", ".yml") else path) + ".state"


def manifest_files(path: str) -> list[str]:
    """Every file git must carry for this manifest: the definition and, once it exists, the state directory."""
    return [path, *([state_dir(path)] if os.path.isdir(state_dir(path)) else [])]


def _definition_of(n: Node) -> dict:
    node = {"id": n.id, "worker": n.worker, "blocked_by": list(n.blocked_by)}
    for name in ("title", "description", "reviewer", "risk", "gate"):
        if getattr(n, name) is not None:
            node[name] = getattr(n, name)
    if n.contract is not None:
        node["contract"] = _dump_contract(n.contract)
    return node


def _runtime_of(n: Node) -> dict:
    state = {"work_item_id": n.work_item_id, "status": n.status}
    if n.merged:
        state["merged"] = True
        state["merged_at"] = n.merged_at
    if n.merge_request_state is not None:
        state["merge_request_state"] = n.merge_request_state
    if n.recovery_marker:
        state["recovery_marker"] = True
    return state


def _apply_runtime(n: Node, state: dict) -> None:
    n.work_item_id = state.get("work_item_id")
    n.status = state.get("status", "todo")
    n.merged = bool(state.get("merged", False))
    n.merged_at = state.get("merged_at")
    n.merge_request_state = state.get("merge_request_state")
    n.recovery_marker = bool(state.get("recovery_marker", False))


def _read_state_file(path: str) -> dict:
    """Read one node state file and verify the sha256 header written with it."""
    with open(path, "rb") as f:
        head, _, body = f.read().partition(b"\n")
    found = _STATE_HEAD.fullmatch(head)
    if not found or hashlib.sha256(body).hexdigest().encode() != found.group(1):
        raise ValidationError(ui(
            f"State file {path} is corrupt or was edited by hand (its sha256 header does not match).\n"
            f"Restore it from git: `git checkout -- {path}` (`git log -- {path}` lists earlier versions), then rerun.",
            f"状态文件 {path} 已损坏或被手工修改(sha256 头不匹配)。\n"
            f"请从 git 恢复: `git checkout -- {path}`(`git log -- {path}` 可查看历史版本),然后重试。"))
    state = yaml.load(body, Loader=_YAML_LOADER)
    if not isinstance(state, dict):
        raise ValidationError(ui(
            f"State file {path} is not a mapping; restore it with `git checkout -- {path}`.",
            f"状态文件 {path} 不是映射;请用 `git checkout -- {path}` 恢复。"))
    return state


STATE_FILE_MAX = 64 * 1024  # a node's state is facts and references; large payloads belong on the platform


def _state_bytes(state: dict, node_id: str = "") -> bytes:
    body = yaml.dump(state, Dumper=getattr(yaml, "CDumper", yaml.Dumper),
                     default_flow_style=False, allow_unicode=True, sort_keys=False).encode("utf-8")
    if len(body) > STATE_FILE_MAX:
        raise ValidationError(ui(
            f"State of node {node_id!r} is {len(body)} bytes; the limit is {STATE_FILE_MAX}. Store large payloads "
            "(transcripts, captured sources) as platform attachments and keep only a reference and digest here.",
            f"节点 {node_id!r} 的状态为 {len(body)} 字节,上限 {STATE_FILE_MAX}。大载荷(转录、捕获的源)请作为平台附件保存,"
            "状态里只留引用和摘要。"))
    return b"# sha256: " + hashlib.sha256(body).hexdigest().encode() + b"\n" + body


def _reject_1x_state(raw, path: str) -> None:
    transport = isinstance(raw, dict) and {"payload", "decoded_sha256"} <= set(raw)
    inline = isinstance(raw, dict) and any(
        isinstance(n, dict) and n.get("work_item_id") for n in raw.get("nodes") or [])
    if transport or inline:
        raise ValidationError(ui(
            f"{path} holds 1.x run state (single-file manifest); 2.0 does not read or modify it.\n"
            "Finish that run with omac 1.x or abandon it, then start a new run with 2.0: "
            "`omac plan create ...`.",
            f"{path} 是 1.x 的运行状态(单文件 manifest),2.0 不读取也不修改。\n"
            "请用 omac 1.x 跑完或放弃该运行,再用 2.0 重新开始: `omac plan create ...`。"))


def load_manifest(path: str) -> Manifest:
    """Load the DAG definition plus each node's state file (环境变量展开 + schema 校验)."""
    target = _canonical_manifest_target(path)
    with open(target, "rb") as f:
        raw = yaml.load(f.read().decode("utf-8"), Loader=_YAML_LOADER)
    _reject_1x_state(raw, path)
    raw = _expand_env(raw)
    manifest = Manifest(meta=raw.get("meta", {}), nodes=_build_nodes(raw))
    directory, states = state_dir(target), {}
    for key, node in manifest.nodes.items():
        state_path = os.path.join(directory, f"{key}.yaml")
        if os.path.exists(state_path):
            states[key] = _read_state_file(state_path)
            _apply_runtime(node, states[key])
    plain = {"meta": copy.deepcopy(manifest.meta),
             "nodes": [_definition_of(n) for n in manifest.nodes.values()]}
    inline = any(any(k in n for k in _RUNTIME_KEYS) for n in raw.get("nodes", []))
    manifest._disk = {"path": target, "def": None if inline else plain, "state": states}
    return manifest


def loads_manifest(text: str) -> Manifest:
    """从 YAML 文本解析 manifest(不落盘,供 pipeline 直接消费 LLM 产出的 manifest)。"""
    raw = _expand_env(yaml.load(text, Loader=_YAML_LOADER))
    return Manifest(meta=raw.get("meta", {}), nodes=_build_nodes(raw))


def _canonical_manifest_target(path: str) -> str:
    """Return the one filesystem identity used for manifest locking and writes."""
    return os.path.realpath(os.path.abspath(path))


def _atomic_write(target: str, data: bytes, mode: int | None = None) -> None:
    """临时文件与目标同目录,flush+fsync 后 os.replace:进程被杀只会留下旧的或新的完整文件。"""
    directory = os.path.dirname(target)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{os.path.basename(target)}.", suffix=".tmp", dir=directory)
    try:
        if mode is not None:
            os.chmod(temporary, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _fsync_dir(directory: str) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save_manifest(manifest: Manifest, path: str):
    """Persist only what changed: node state files first, the definition file last.

    Each file is replaced atomically. A save that touches one node rewrites one small file; the
    definition file (meta + node definitions) is rewritten only by plan or amendment.
    """
    target = _canonical_manifest_target(path)
    directory = state_dir(target)
    for key in manifest.nodes:
        if not _NODE_ID.fullmatch(key):
            raise ValidationError(ui(
                f"Node id {key!r} cannot be used as a state file name; use letters, digits, '.', '_' or '-'.",
                f"节点 id {key!r} 不能作为状态文件名;请只用字母、数字、'.'、'_'、'-'。"))
    disk = manifest._disk if manifest._disk.get("path") == target else {}
    states = {key: _runtime_of(n) for key, n in manifest.nodes.items()}
    definition = {"meta": manifest.meta, "nodes": [_definition_of(n) for n in manifest.nodes.values()]}
    old_states = disk.get("state", {})

    touched = set()
    for key, state in states.items():
        if old_states.get(key) != state:
            _atomic_write(os.path.join(directory, f"{key}.yaml"), _state_bytes(state, key))
            touched.add(directory)
    for key in old_states.keys() - states.keys():
        try:
            os.unlink(os.path.join(directory, f"{key}.yaml"))
        except FileNotFoundError:
            pass
        touched.add(directory)
    if disk.get("def") != definition:
        mode = stat.S_IMODE(os.stat(target).st_mode) if os.path.exists(target) else None
        body = yaml.dump(definition, Dumper=getattr(yaml, "CDumper", yaml.Dumper),
                         default_flow_style=False, allow_unicode=True, sort_keys=False)
        _atomic_write(target, body.encode("utf-8"), mode)
        touched.add(os.path.dirname(target))
    for changed in touched:
        _fsync_dir(changed)
    manifest._disk = {"path": target, "def": copy.deepcopy(definition), "state": copy.deepcopy(states)}


@contextmanager
def manifest_write_lock(path: str):
    """同一台机器只允许一个受管命令操作同一真实 manifest。"""
    canonical_path = _canonical_manifest_target(path)
    key = hashlib.sha256(canonical_path.encode("utf-8")).hexdigest()[:24]
    lock_path = os.path.join(tempfile.gettempdir(), f"omac-manifest-{key}.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValidationError(ui(
                "Another `omac dag run`, `tick`, `dag amend propose`, or "
                f"`dag amend accept` is already operating on manifest: {path}\n"
                "Wait for it to exit, or confirm the stale process has stopped before retrying.",
                f"已有另一个 omac dag run/tick/amend propose/accept 正在操作 manifest: {path}\n"
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
