"""Multica 桥接层 —— 最薄组合:人工计划门、PlanReturn 摄入、外部 merge 证据
摄入、五阶段父工单投影、机器隔离校验。

设计约束(§12.4):本模块只组合 WorkItemStore/AgentRuntime 与 core 纯校验器,
绝不 shell out 平台 CLI。它不重建调度器:loop.tick 仍是唯一推进引擎,桥接只
提供「门」与「摄入」原语:

  - 人工计划门:node.gate.human_plan 标记的硬/歧义工作,在校验器把不可变
    PlanReturn 快照记入 manifest meta.plan_snapshot 之前,绝不派发
    (partition_ready_by_plan_gate);被挡节点以 needs_decision(exit 20)
    结构化报告呈现,附可复制的修复命令。
  - PlanReturn 摄入(submit_plan_return):严格解析 + 不可变 SHA-256 快照,
    只有校验器能写入 plan_snapshot(validator-only plan completion);
    artifact/host 形式仅经注入的窄 fetch 接口,CLI 不内嵌任何 fetch/shell。
  - 外部 merge 证据摄入(submit_external_merge_evidence):只接受绑定已批准
    pr_url + tip 的证据,stale/wrong/畸形一律 exit 5 拒绝且不落盘。
  - 五阶段投影(project_parent):Intake/Plan/Build/Verify/Done 的确定性父
    工单投影,不需要任何可见工作流 label;blocked/failed 是异常态,不是阶段。
  - 机器隔离(validate_machine_isolation):machine 配置开启时,manifest 必须
    声明 meta.source(人工看板指针)与匹配的 meta.namespace。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from ..core import graph
from ..core.config import resolve_machine, resolve_plan_gate
from ..core.evidence import validate_external_merge_evidence
from ..core.linkage import project_stage, validate_source_linkage
from ..core.manifest import Manifest, save_manifest
from ..core.planreturn import (
    PlanSnapshot, parse_plan_return, resolve_plan_return)
from ..engines.store import WorkItemStore
from ..errors import ValidationError
from ..i18n import ui

# 人工计划门标记:node.gate = {"human_plan": true}(机器专用,不进人工看板词表)
HUMAN_PLAN_GATE_KEY = "human_plan"
# manifest meta 键:校验器写入的不可变计划快照(validator-only plan completion)
PLAN_SNAPSHOT_META_KEY = "plan_snapshot"

# 异常态(不是生命周期阶段);abandoned 视为已满足(与 graph.SATISFIED 对齐)
_EXCEPTION_STATUSES = {"blocked", "failed", "cancelled"}
_DONE_STATUSES = {"done", "abandoned"}

# manifest 细分态 → 平台状态(投影用);与 delivery.MANIFEST_TO_PLATFORM_STATUS
# 保持一致,但只取投影需要的子集并落到平台状态字符串。
_MANIFEST_TO_PLATFORM = {
    "todo": "todo",
    "in_progress": "in_progress",
    "ci_check": "in_progress",
    "in_review": "in_review",
    "merging": "in_review",
    "done": "done",
}

# 父工单阶段聚合优先级(取活跃节点到达的最远阶段)
_STAGE_RANK = {"intake": 0, "plan": 1, "build": 2, "verify": 3, "done": 4}


# ==================== 人工计划门 ====================

def is_human_plan_node(node) -> bool:
    """节点是否带人工计划门标记(硬/歧义工作,须先过 PlanReturn 校验)。"""
    gate = getattr(node, "gate", None)
    return isinstance(gate, dict) and gate.get(HUMAN_PLAN_GATE_KEY) is True


def plan_snapshot_of(manifest: Manifest) -> Optional[dict]:
    """读取校验器记录的不可变计划快照;未解锁返回 None。"""
    snapshot = manifest.meta.get(PLAN_SNAPSHOT_META_KEY)
    if not isinstance(snapshot, dict) or not snapshot.get("sha256"):
        return None
    return snapshot


def partition_ready_by_plan_gate(
    manifest: Manifest, ready: List[str]
) -> Tuple[List[str], List[str]]:
    """把就绪节点分成 (可派发, 被人工计划门挡住)。

    未带 gate 标记的 manifest 行为与上游完全一致(全部分到可派发)。
    被挡节点保持 todo —— 不是失败,也不是 blocked;解锁(校验器写入
    meta.plan_snapshot)后下一个 tick 自然进入派发。
    """
    if plan_snapshot_of(manifest) is not None:
        return list(ready), []
    dispatchable = []
    gated = []
    for key in ready:
        if is_human_plan_node(manifest.nodes[key]):
            gated.append(key)
        else:
            dispatchable.append(key)
    return dispatchable, gated


def build_plan_gate_report(
    manifest: Manifest, manifest_path: str, gated: List[str]
) -> dict:
    """人工计划门的 needs-decision 结构化报告(schema 与 report 模块锁定一致)。

    next_actions 给出可复制的修复命令:先经 bridge submit-plan-return 提交
    PlanReturn 解锁;放弃该节点仍是显式决策(omac node abandon)。
    """
    repair = (
        f"omac bridge submit-plan-return {manifest_path} "
        '--text "PlanReturn path=/absolute/path/to/plan.md"')
    failed_nodes = []
    for key in sorted(gated):
        node = manifest.nodes[key]
        failed_nodes.append({
            "key": key,
            "status": node.status,
            "reason": ui(
                "Human plan gate: implementation is blocked until a validated "
                "PlanReturn records an immutable plan snapshot.",
                "人工计划门:经校验的 PlanReturn 记录不可变计划快照之前,"
                "实现保持阻断"),
            "work_item_id": node.work_item_id,
            "pr_url": None,
            "evidence_summary": None,
        })
    next_actions: List[str] = [repair]
    for key in sorted(gated):
        next_actions.append(f"omac node abandon {manifest_path} {key}")
    snapshot = {
        key: {"status": node.status, "blocked_by": list(node.blocked_by)}
        for key, node in manifest.nodes.items()
    }
    downstream = graph.downstream_of(snapshot, set(gated))
    return {
        "failed_nodes": failed_nodes,
        "blocked_downstream": sorted(downstream),
        "next_actions": next_actions,
    }


def _project_root_from_manifest_path(manifest_path: str) -> str:
    parent = os.path.dirname(os.path.abspath(manifest_path))
    if os.path.basename(parent) == ".omac":
        return os.path.dirname(parent)
    return parent


def submit_plan_return(
    manifest: Manifest,
    manifest_path: str,
    text: str,
    *,
    config: dict,
    fetch=None,
) -> PlanSnapshot:
    """PlanReturn 桥接摄入:严格解析 → 不可变快照 → 写入 manifest meta。

    只有本校验路径能写入 meta.plan_snapshot(validator-only plan completion)。
    解析失败 → ValidationError(exit 5,附批准形式);无法安全解析(缺失/
    不可读/变动/hash 不匹配/未配置安全 fetch/host 未授权)→ NeedsDecision
    (exit 20,report 含可复制的 repair 行)。
    store_dir 相对路径按 manifest 所在项目根解析;artifact/host 形式只走注入的
    窄 fetch(source) -> bytes 接口。
    """
    plan_gate = resolve_plan_gate(config)
    store_dir = plan_gate["store_dir"]
    if not os.path.isabs(store_dir):
        store_dir = os.path.join(
            _project_root_from_manifest_path(manifest_path), store_dir)
    ret = parse_plan_return(text)
    snapshot = resolve_plan_return(
        ret,
        plan_store_dir=store_dir,
        fetch=fetch,
        allowed_hosts=plan_gate["allowed_hosts"],
    )
    manifest.meta[PLAN_SNAPSHOT_META_KEY] = {
        "sha256": snapshot.sha256,
        "size": snapshot.size,
        "snapshot_path": snapshot.snapshot_path,
        "source": snapshot.source,
    }
    save_manifest(manifest, manifest_path)
    return snapshot


def revoke_plan_snapshot(manifest: Manifest, manifest_path: str) -> None:
    """回滚:移除已记录的计划快照,人工计划门重新锁定(显式操作)。"""
    manifest.meta.pop(PLAN_SNAPSHOT_META_KEY, None)
    save_manifest(manifest, manifest_path)


# ==================== 外部 merge 证据摄入 ====================

def submit_external_merge_evidence(
    store: WorkItemStore, item_id: str, evidence: Dict[str, Any]
) -> None:
    """外部 merge 权威证据摄入:校验绑定已批准 pr_url + tip 后才落盘。

    证据写入 artifacts.external_merge,loop 的 merging 回收路径在下一 tick
    校验并推进。stale/wrong/畸形证据 → ValidationError(exit 5),且不做任何
    写入(原子性)。
    """
    item = store.get_work_item(item_id)
    artifacts = item.artifacts if isinstance(item.artifacts, dict) else {}
    pr_url = artifacts.get("pr_url") or artifacts.get("pr") or ""
    if not pr_url:
        raise ValidationError(ui(
            f"Work item {item_id} has no approved pr_url, so external merge "
            "evidence cannot be bound. Publish the PR first (review-before-PR "
            "publishes it after a green review).",
            f"工作单元 {item_id} 没有已批准的 pr_url,无法绑定外部 merge 证据。"
            "请先发布 PR(review-before-PR 会在评审通过后发布)。"))
    approved_tip = artifacts.get("pr_tip_sha") or artifacts.get("tip_sha") or ""
    errors = validate_external_merge_evidence(
        evidence, pr_url=pr_url, tip_sha=approved_tip)
    if errors:
        raise ValidationError(ui(
            "External merge evidence rejected:\n  - " + "\n  - ".join(errors)
            + "\nOnly evidence bound to the approved pr_url + tip_sha advances; "
            "redeliver for the exact approved PR/tip.",
            "外部 merge 证据被拒绝:\n  - " + "\n  - ".join(errors)
            + "\n只有绑定已批准 pr_url + tip_sha 的证据才能推进;"
            "请按精确的已批准 PR/tip 重新投递。"))
    merged = dict(artifacts)
    merged["external_merge"] = dict(evidence)
    store.update_work_item_metadata(item_id, artifacts=merged)


# ==================== 五阶段父工单投影 ====================

def project_parent(manifest: Manifest) -> dict:
    """确定性父工单投影:Intake/Plan/Build/Verify/Done,不需要可见工作流 label。

    blocked/failed/cancelled 是异常态,不是阶段 —— 单列在 blocked 清单里,
    节点阶段为 None;父阶段取其余节点到达的最远阶段。abandoned 视为已满足
    (与 graph.SATISFIED 对齐)。未知状态 → ValidationError(exit 5)。
    """
    node_stages: Dict[str, Optional[str]] = {}
    exception: List[str] = []
    for key, node in manifest.nodes.items():
        status = node.status
        if status in _EXCEPTION_STATUSES:
            node_stages[key] = None
            exception.append(key)
            continue
        if status in _DONE_STATUSES:
            stage = "done"
        else:
            platform = _MANIFEST_TO_PLATFORM.get(status)
            if platform is None:
                # 交给 linkage 投影抛出权威的教学错误(未知状态)
                project_stage(status)
            stage = project_stage(platform)
        node_stages[key] = stage
    # 父阶段:全部完成 → done;否则取活跃(非 done)节点到达的最远阶段。
    # done 节点不会把仍在推进的工作抬成 done。
    stages = [s for s in node_stages.values() if s is not None]
    if stages and all(s == "done" for s in stages):
        best: Optional[str] = "done"
    else:
        active = [s for s in stages if s != "done"]
        best = max(active, key=lambda s: _STAGE_RANK[s], default=None)
    gated = sorted(
        key for key, node in manifest.nodes.items() if is_human_plan_node(node))
    return {
        "stage": best if best is not None else "intake",
        "blocked": sorted(exception),
        "node_stages": node_stages,
        "plan_gate": {
            "gated": gated,
            "unlocked": plan_snapshot_of(manifest) is not None,
        },
        "source": manifest.meta.get("source"),
    }


# ==================== 机器隔离校验 ====================

def validate_machine_isolation(config: dict, manifest: Manifest) -> None:
    """机器隔离:machine 配置开启时,manifest 必须声明人工看板指针与命名空间。

    缺省(machine 块不存在)→ no-op(向后兼容)。开启时:
      - meta.source 必须过 validate_source_linkage(机器 DAG 必须指回源
        project/issue,机器工作不得与人工看板脱钩);
      - meta.namespace 必须等于配置的 machine.namespace(fail closed:
        防止机器条目静默落入人工项目命名空间)。
    违规 → ValidationError(exit 5,报错即教学)。
    """
    machine = resolve_machine(config)
    if machine["project"] is None:
        return
    if manifest.meta.get("source") is None:
        raise ValidationError(ui(
            "Machine isolation requires meta.source on the manifest: machine DAG "
            "work must point back to its human-board origin. Add meta.source: "
            "{project: <human-board-project>, issue: <id>}.",
            "机器隔离要求 manifest 声明 meta.source:机器 DAG 工作必须指回人工"
            "看板来源。请添加 meta.source: {project: <人工看板 project>, "
            "issue: <id>}。"))
    errors = validate_source_linkage(manifest.meta)
    if errors:
        raise ValidationError(ui(
            "Machine isolation requires a source pointer on the manifest:\n  - "
            + "\n  - ".join(errors)
            + "\nAdd meta.source: {project: <human-board-project>, issue: <id>} "
            "so machine DAG work stays linked to its human-board origin.",
            "机器隔离要求 manifest 声明 source 指针:\n  - "
            + "\n  - ".join(errors)
            + "\n请添加 meta.source: {project: <人工看板 project>, issue: <id>},"
            "让机器 DAG 工作始终指回人工看板来源。"))
    namespace = manifest.meta.get("namespace")
    if namespace != machine["namespace"]:
        raise ValidationError(ui(
            f"Machine isolation requires meta.namespace to equal the configured "
            f"machine namespace {machine['namespace']!r}; got {namespace!r}. "
            "Machine work must declare its machine namespace so it never lands "
            "in a human project board.",
            f"机器隔离要求 meta.namespace 等于配置的机器命名空间 "
            f"{machine['namespace']!r};当前为 {namespace!r}。"
            "机器工作必须声明机器命名空间,绝不落入人工项目看板。"))
