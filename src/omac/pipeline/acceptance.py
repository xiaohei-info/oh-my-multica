"""总控验收外层循环 + DAG 增量扩展(不起新 DAG,设计文档 §7.6)。

内层 tick-loop 收敛(全部节点 done)后,若 ``.orchestrator/<name>.acceptance.yaml`` 存在,
进入外层验收循环(``<= acceptance.max_rounds``,缺省 3;无验收文档跳过,收敛即 exit 0):

    loop 外层验收循环(≤ max_rounds):
        1. 派发 final-acceptance 任务(P3.1 原语,assignee=roles.acceptor 缺省
           reviewers 轮转)给 acceptor;payload = 验收文档 + 集成分支 + 各节点
           env_setup 汇总;
        2. acceptor 端到端走查后回 acceptance_results;左移校验(P2.2):结果须逐项
           对齐验收文档条目;
        3. alt 全部条目 pass -> exit 0(真正可交付)
           alt 存在 fail -> 派发 decompose 增量任务(orchestrator,payload=失败项清单+
           现 manifest);orchestrator 交付仅含新增 fix 节点;lint 增量节点(含与既有
           节点的依赖引用校验)-> 并入原 manifest(id 冲突报错;已 done 节点不动)->
           回到内层 tick-loop 继续推进
    耗尽仍 fail -> exit 20,报告=未通过验收项清单 + 历轮 results

纪律(§12.4 红线):本层只调 engines 的 Store/Runtime 接口,绝不直接 shell out
平台 CLI。
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import yaml

from ..core.acceptance import AcceptanceDoc, load_acceptance_doc_file
from ..core.config import (
    DEFAULT_MAX_ROUNDS, get_value, resolve_retry,
)
from ..core.evidence import validate_acceptance_results
from ..core.lint import lint_increment
from ..core.manifest import Manifest, Node, merge_increment, save_manifest
from ..core.taskmeta import TaskKind
from ..engines.models import WorkItemStatus
from ..engines.store import WorkItemStore
from ..errors import NeedsDecision
from ..pipeline import loop as loop_mod


CONFIG_DIR = ".orchestrator"


@dataclass
class AcceptanceConfig:
    """总控验收外层循环配置(从 config.yaml 解析)。"""
    max_rounds: int = DEFAULT_MAX_ROUNDS
    acceptor: Optional[str] = None       # 缺省复用 reviewers 池轮转
    no_acceptance: bool = False         # --no-acceptance:跳过验收环节(收敛即 0)


@dataclass
class AcceptanceOutcome:
    """外层循环结果。"""
    exit_code: int                       # 0 = 全 pass; 20 = 耗尽仍 fail
    rounds: int = 0                      # 经历的轮次
    failed_items: List[str] = field(default_factory=list)   # 最终未通过验收项
    reports: List[Dict[str, Any]] = field(default_factory=list)


def acceptance_doc_path(manifest_path: str) -> str:
    """从 manifest 路径派生验收文档路径.

    .orchestrator/feature-x.yaml -> .orchestrator/feature-x.acceptance.yaml
    """
    base, _ = os.path.splitext(manifest_path)
    return base + ".acceptance.yaml"


def resolve_acceptance_config(config: dict) -> AcceptanceConfig:
    """从项目配置解析总控验收配置."""
    acceptance = get_value(config, "acceptance") or {}
    max_rounds = acceptance.get("max_rounds", DEFAULT_MAX_ROUNDS)
    if not isinstance(max_rounds, int) or isinstance(max_rounds, bool):
        max_rounds = DEFAULT_MAX_ROUNDS
    max_rounds = max(1, max(max_rounds, 1))
    acceptor = get_value(config, "roles.acceptor")
    return AcceptanceConfig(max_rounds=max_rounds, acceptor=acceptor)


def _reviewers(config: dict) -> List[str]:
    roles = get_value(config, "roles") or {}
    reviewers = roles.get("reviewers") or []
    if isinstance(reviewers, str):
        reviewers = [r.strip() for r in reviewers.split(",") if r.strip()]
    return [r for r in reviewers]


def _poll_no_op() -> None:
    """测试用 poll:不 sleep(配合 mock delay=0)."""
    pass


def run_acceptance_loop(
    engine,
    manifest: Manifest,
    manifest_path: str,
    acceptance_doc: AcceptanceDoc,
    config: dict,
    *,
    no_acceptance: bool = False,
    poll: Callable[[], None] = _poll_no_op,
) -> AcceptanceOutcome:
    """总控验收外层循环(§7.6)。

    前置:内层 tick-loop 已全部节点 done。
    返回 AcceptanceOutcome(exit_code=0 全 pass / =20 耗尽仍 fail)。
    """
    acceptance_cfg = resolve_acceptance_config(config)
    if no_acceptance or acceptance_cfg.no_acceptance:
        return AcceptanceOutcome(exit_code=0)
    reviewers = _reviewers(config)
    if not reviewers:
        reviewers = _collect_workers(manifest)
    acceptor_cfg = acceptance_cfg.acceptor

    operation_branch = manifest.meta.get("pr_base")
    resolve_retry(config)  # 校验 retry 配置合法性(副作用)

    workspace_id = engine.store.config.workspace_id
    orchestrator = get_value(config, "roles.orchestrator") or _first_worker(manifest)

    reports: List[Dict[str, Any]] = []
    failed_items: List[str] = []

    for round_num in range(1, acceptance_cfg.max_rounds + 1):
        # ── 步骤 1:派发 final-acceptance 任务 ──
        if acceptor_cfg:
            acceptor = acceptor_cfg
        else:
            acceptor = reviewers[(round_num - 1) % len(reviewers)] if reviewers else None
        if not acceptor:
            raise NeedsDecision(
                "无法确定验收人——请配置 roles.acceptors 或 roles.reviewers")

        acceptance_item_id = _dispatch_and_wait(
            engine, workspace_id, TaskKind.FINAL_ACCEPTANCE, acceptor,
            f"final-acceptance-r{round_num}",
            {
                "acceptance_doc": _acceptance_doc_raw(acceptance_doc),
                "pr_base": operation_branch,
                "flows": acceptance_doc.flow_ids,
            },
            poll=poll,
        )

        results = _read_acceptance_results(engine.store, acceptance_item_id)

        # ── 步骤 2:左移校验 P2.2 ──
        validation_errors = validate_acceptance_results(acceptance_doc, results)
        if validation_errors:
            raise NeedsDecision(
                "acceptance_results 校验失败:\\n  - " + "\\n  - ".join(validation_errors),
                report={"round": round_num, "errors": validation_errors})

        failed_items = [r["id"] for r in results if r.get("status") == "fail"]
        reports.append({
            "round": round_num,
            "acceptor": acceptor,
            "results": results,
            "failed_items": failed_items,
        })

        # ── 步骤 3:全 pass -> exit 0 ──
        if not failed_items:
            return AcceptanceOutcome(exit_code=0, rounds=round_num, reports=reports)

        # ── 步骤 4:有 fail -> 派发 decompose 增量任务 ──
        if not orchestrator:
            raise NeedsDecision(
                "无法派发增量拆解:配置/角色缺 orchestrator",
                report={"round": round_num})

        decompose_item_id = _dispatch_and_wait(
            engine, workspace_id, TaskKind.DECOMPOSE, orchestrator,
            f"decompose-r{round_num}",
            {
                "manifest": _dump_manifest(manifest),
                "failed_items": failed_items,
                "pr_base": operation_branch,
                "mode": "incremental",
            },
            poll=poll,
        )

        increment = _read_increment(engine.store, decompose_item_id)
        if not increment or not increment.nodes:
            continue

        # ── 步骤 5:lint 增量节点(含与既有节点的依赖引用校验) ──
        pool = set(_collect_workers(manifest))
        pool.update(reviewers)
        if acceptor_cfg:
            pool.add(acceptor_cfg)
        lint_errors = lint_increment(increment, manifest, pool)
        if lint_errors:
            raise NeedsDecision(
                "incremental manifest lint 失败:\\n  - " + "\\n  - ".join(lint_errors),
                report={"round": round_num, "lint_errors": lint_errors,
                        "failed_items": failed_items})

        # ── 步骤 6:并入原 manifest(id 冲突报错;已 done 节点不动) ──
        merge_increment(manifest, increment)
        save_manifest(manifest, manifest_path)

        # ── 步骤 7:回到内层 tick-loop 继续推进(收敛后才有下一轮验收) ──
        _run_inner_loop(
            engine, manifest, manifest_path, config, poll=poll)

    # ── 耗尽仍 fail -> exit 20 ──
    return AcceptanceOutcome(
        exit_code=20,
        rounds=acceptance_cfg.max_rounds,
        failed_items=failed_items,
        reports=reports)


def _dispatch_and_wait(
    engine,
    workspace_id: str,
    kind: TaskKind,
    assignee: str,
    dag_key: str,
    payload: Dict[str, Any],
    *,
    poll: Callable[[], None],
    max_ticks: int = 10000,
) -> str:
    """派发一个非 develop 任务(kind×authoring),等终态,返回 item_id。

    final-acceptance:创建后把验收文档挂到 contract.acceptance_doc 上,供
    work submit 路径的左移校验(_validate_final_acceptance_authoring)读取。
    decompose:作者提交后状态落在 IN_REVIEW(待审),一并视为终态。
    """
    store = engine.store
    runtime = engine.runtime
    description = yaml.dump(payload, allow_unicode=True, sort_keys=False)
    item = store.create_work_item(
        workspace_id=workspace_id,
        title=f"{kind.value} {dag_key}",
        description=description,
        dag_key=dag_key,
        worker=assignee,
        kind=kind,
    )
    item_id = item.id
    acceptance_doc_raw = payload.get("acceptance_doc")
    if acceptance_doc_raw is not None:
        store.set_node_contract(item_id, {"acceptance_doc": acceptance_doc_raw})
    store.mark_in_progress(item_id)
    store.assign_work_item(item_id, assignee, "worker")
    runtime.wake(item_id, assignee, "worker")

    # decompose 作者提交后落在 IN_REVIEW(待审),视为终态一并退出
    terminal = (WorkItemStatus.DONE, WorkItemStatus.FAILED,
                WorkItemStatus.BLOCKED, WorkItemStatus.IN_REVIEW)
    for _ in range(max_ticks):
        cur = store.get_work_item(item_id)
        if cur.status in terminal:
            break
        poll()
    return item_id


def _run_inner_loop(
    engine,
    manifest: Manifest,
    manifest_path: str,
    config: dict,
    *,
    poll: Callable[[], None] = _poll_no_op,
    max_ticks: int = 1000,
) -> None:
    """把内层 tick-loop 跑到收敛(全部 done)或需决策(阻塞)。

    幂等:done 节点不动;只推进新并入的 todo 节点。

    语义保障:只在 tick 返回 converged 时静默返回;needs_decision 或达到安全
    上限仍未收敛 → 抛 NeedsDecision(让外层以 exit 20 结束),绝不在节点仍
    in_progress 时悄悄返回,避免外层误判 exit 0。
    """
    retry_limits = resolve_retry(config)
    max_parallel = config.get("defaults", {}).get("max_parallel", 4)

    for _ in range(max_ticks):
        result = loop_mod.tick(
            engine.store, engine.runtime, manifest, manifest_path,
            max_parallel=max_parallel, retry_limits=retry_limits, config=config)
        if result.state == "converged":
            return
        if result.state == "needs_decision":
            raise NeedsDecision(
                f"内层 loop 需决策:节点 {result.failed} 失败/受阻,无法继续推进",
                report=result.report)
        poll()

    # 安全上限耗尽仍不收敛 → 需决策而非静默放行
    snapshot = {k: n.status for k, n in manifest.nodes.items()}
    raise NeedsDecision(
        f"内层 loop {max_ticks} tick 仍未收敛,节点状态={snapshot}",
        report={"state": "running", "nodes": snapshot})


def _collect_workers(manifest: Manifest) -> List[str]:
    return list({n.worker for n in manifest.nodes.values() if n.worker})


def _first_worker(manifest: Manifest) -> Optional[str]:
    for n in manifest.nodes.values():
        if n.worker:
            return n.worker
    return None


def _acceptance_doc_raw(doc: AcceptanceDoc) -> Dict[str, Any]:
    """把 AcceptanceDoc 序列化为可嵌入 payload 的 dict。"""
    return {
        "flows": [
            {
                "id": flow.id,
                "name": flow.name,
                "actions": [
                    {"step": a.step, "how": a.how, "expected": a.expected}
                    for a in flow.actions
                ],
            }
            for flow in doc.flows
        ]
    }


def _read_acceptance_results(store: WorkItemStore, item_id: str) -> List[Dict[str, Any]]:
    """读回 final-acceptance 任务的 acceptance_results。

    真实 work submit 路径把 acceptance-results 文件文本写到 item.deliverable,
    这里解析 JSON/YAML 文本还原为结果列表(兼容旧 mock 直接挂 list/dict)。
    """
    item = store.get_work_item(item_id)
    deliverable = getattr(item, "deliverable", None)
    if isinstance(deliverable, list):
        return deliverable
    if isinstance(deliverable, dict):
        results = deliverable.get("results")
        if isinstance(results, list):
            return results
        if "id" in deliverable and "status" in deliverable:
            return [deliverable]
        return []
    if isinstance(deliverable, str):
        try:
            data = yaml.safe_load(deliverable)
        except yaml.YAMLError:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            results = data.get("results")
            if isinstance(results, list):
                return results
            if "id" in data and "status" in data:
                return [data]
    return []


def _read_increment(store: WorkItemStore, item_id: str) -> Optional[Manifest]:
    """读回 decompose 增量任务的 Manifest。

    真实 work submit 路径对 DECOMPOSE authoring 把 manifest 文本写到
    item.deliverable(状态进 IN_REVIEW),这里解析该文本为 Manifest(YAML)。
    """
    item = store.get_work_item(item_id)
    deliverable = getattr(item, "deliverable", None)
    if not deliverable or not isinstance(deliverable, str):
        return None
    try:
        raw = yaml.safe_load(deliverable)
    except yaml.YAMLError:
        return None
    if not isinstance(raw, dict):
        return None
    nodes_raw = raw.get("nodes")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        return None
    nodes = {}
    for n in nodes_raw:
        if not isinstance(n, dict):
            continue
        n_id = n.get("id")
        n_worker = n.get("worker")
        if not isinstance(n_id, str) or not n_id:
            continue
        if not isinstance(n_worker, str) or not n_worker:
            continue
        nodes[n_id] = Node(
            id=n_id, worker=n_worker,
            blocked_by=list(n.get("blocked_by", []) or []),
            status=n.get("status", "todo"),
            work_item_id=n.get("work_item_id"),
        )
    if not nodes:
        return None
    return Manifest(meta=raw.get("meta") or {}, nodes=nodes)


def _dump_manifest(manifest: Manifest) -> str:
    """把 manifest 序列化为 YAML 文本(作 decompose 任务的 payload)。"""
    buf = io.StringIO()
    data = {
        "meta": manifest.meta,
        "nodes": [
            {
                "id": n.id,
                "worker": n.worker,
                "blocked_by": list(n.blocked_by),
                "status": n.status,
                "work_item_id": n.work_item_id,
                "reviewer": n.reviewer,
            }
            for n in manifest.nodes.values()
        ],
    }
    yaml.dump(data, buf, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return buf.getvalue()
