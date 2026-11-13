"""Pure plans of the Loop's result collection and reconcile: observed facts in, ordered effects out.

No I/O and no platform access (enforced by tests/test_architecture.py). The shell in ``loop.py`` reads the platform and
the manifest, builds an ``Observation`` where a decision has many inputs, calls one of these functions, and hands the
returned ``Plan`` to ``pipeline.apply.apply``. Most plans end with ``SetNode(status="blocked")``: the manifest follows
the platform write, as it always has, and the node is saved by the tick.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from ..core.taskmeta import DECISION_REQUIRED_SCHEMA, TaskKind, TaskPhase
from ..core.workitem import PullRequestState, WorkItemStatus
from ..i18n import ui
from .decide_effects import (
    AddComment, Effect, Observation, Plan, ResetReview, SaveState, SetNode, UpdateMetadata, UpdateStatus,
)
from .decide_merge import MERGING, block_unproven


def mark_recovery(node_key: str, *, persist: bool) -> tuple[Effect, ...]:
    """The active-set hint is written *before* a recovery control fact, so a restart observes instead of dropping it."""
    return (SetNode(node_key, {"recovery_marker": True}),) + ((SaveState(),) if persist else ())


def plan_block(
    node_key: str, item_id: str, *, decision: Mapping[str, Any] | None = None, write_decision: bool = True,
    write_status: bool = True, comment: str | None = None, comment_before_status: bool = False,
    mark: bool = False, persist_marker: bool = False, blocked_first: bool = False, phase: Any = None,
) -> Plan:
    """Stop a node: optionally a recovery marker and a decision first, then the platform status, then the manifest.

    `write_decision` / `write_status` are False when the platform already says so (idempotent on restart).
    """
    blocked = (SetNode(node_key, {"status": "blocked"}),)
    effects: list[Effect] = list(blocked) if blocked_first else []
    if mark:
        effects += mark_recovery(node_key, persist=persist_marker)
    if decision is not None and write_decision:
        fields = {"decision_required": decision}
        if phase is not None:
            fields["phase"] = phase
        effects.append(UpdateMetadata(item_id, fields))
    if comment is not None and comment_before_status:
        effects.append(AddComment(item_id, comment))
    if write_status:
        effects.append(UpdateStatus(item_id, WorkItemStatus.BLOCKED))
    if comment is not None and not comment_before_status:
        effects.append(AddComment(item_id, comment))
    if not blocked_first:
        effects += blocked
    return Plan(tuple(effects), "blocked")


def plan_worker_failed(node_key: str, item_id: str) -> Plan:
    reason = ui("Worker execution failed", "worker 执行失败")
    return plan_block(node_key, item_id, comment=reason)


def plan_evidence_gate_failed(node_key: str, item_id: str, reason: str) -> Plan:
    return plan_block(node_key, item_id, comment=ui(f"Evidence gate failed: {reason}", f"证据门未通过: {reason}"))


def plan_return_to_worker_failed(
    node_key: str, item_id: str, worker: str, bounce_rollback: int, error: str,
) -> Plan:
    """The bounce was counted but the handoff failed: roll the count back and stop."""
    effects = (UpdateMetadata(item_id, {"worker_bounce": bounce_rollback}),
               UpdateStatus(item_id, WorkItemStatus.BLOCKED),
               AddComment(item_id, ui(
                   f"Failed to return delivery to worker {worker}; retry count rolled back: {error}",
                   f"回退到 worker {worker} 继续交付失败(已回滚回退计数): {error}")),
               SetNode(node_key, {"status": "blocked"}))
    return Plan(effects, "blocked")


# errors.NodeFailure reason code of a delivery whose PR HEAD is no longer the head the Worker submitted.
DELIVERY_HEAD_MOVED = "delivery-head-moved"
_DETAIL_LIMIT = 500   # the decision lives in 8 KiB issue metadata


class NodeFailureRoute(Enum):
    REWORK = "rework"   # the delivery is void: back to its Worker through a new handoff generation (spends retry.worker)
    DECIDE = "decide"   # stop only this node with a decision; the tick goes on with the others


@dataclass(frozen=True)
class NodeFailureObservation(Observation):
    stale_delivery: bool     # the PR HEAD moved after the Worker's submit
    merge_started: bool      # a merge marker is persisted: its outcome may already include the moved head
    worker_consumed: int
    worker_limit: int


def decide_node_failure(obs: NodeFailureObservation) -> NodeFailureRoute:
    """Only a stale delivery is reworked, and only while no merge is under way and retry.worker has budget left."""
    if obs.stale_delivery and not obs.merge_started and obs.worker_consumed < obs.worker_limit:
        return NodeFailureRoute.REWORK
    return NodeFailureRoute.DECIDE


def node_failure_decision(
    node_key: str, item_id: str, phase: TaskPhase, reason_code: str, detail: str, next_action: str,
) -> dict:
    """The standing decision of a node-level failure (errors.NodeFailure), in the shape every other decision has."""
    return {
        "schema": DECISION_REQUIRED_SCHEMA, "reason_code": reason_code, "kind": TaskKind.DEVELOP.value,
        "phase": phase.value, "gate": "delivery", "resume_issue_id": item_id, "node_id": node_key,
        "detail": detail[:_DETAIL_LIMIT], "next_action": next_action,
    }


def plan_node_failure(
    node_key: str, item_id: str, decision: Mapping[str, Any], *, has_decision: bool, item_status: WorkItemStatus,
    reason: str,
) -> Plan:
    """Stop one node: an earlier standing decision is kept (it is the older, still unanswered question)."""
    return plan_block(
        node_key, item_id, decision=decision, write_decision=not has_decision, mark=True, persist_marker=True,
        comment=reason, write_status=item_status != WorkItemStatus.BLOCKED)


def node_failure_reason(reason_code: str, detail: str, next_action: str) -> str:
    return ui(
        f"Node stopped ({reason_code}): {detail}. The rest of the DAG keeps running; after checking, run `{next_action}`.",
        f"节点已停下({reason_code}):{detail}。DAG 其余节点继续推进;确认后运行 `{next_action}`。")


def stale_delivery_note(submitted: str, current: str) -> str:
    """What the Worker reads on its issue when its delivery went stale."""
    return ui(
        f"⚠️ The PR HEAD changed after your submit ({submitted} → {current}), so this delivery is void. Confirm the "
        "verification on the current HEAD, then run `omac work submit` again. Do not push to the PR branch after "
        "submitting.",
        f"⚠️ PR HEAD 在提交后变化({submitted} → {current}),本次交付作废。请在当前 HEAD 上确认验证并重新执行 "
        "`omac work submit`;submit 之后不要再往 PR 分支推提交。")


@dataclass(frozen=True)
class DoneDevelopObservation(Observation):
    """What a historical `done` develop node must still prove: a confirmed remote merge."""
    node_key: str
    item_id: str
    item_status: WorkItemStatus
    has_pull_request: bool
    request_state: str | None
    request_state_valid: bool
    merge_closed: bool
    pr_state: PullRequestState | None   # None when no remote observation was needed
    merged_at: str | None = None
    detail: str = ""


def decide_done_develop(obs: DoneDevelopObservation, manifest_path: str | None) -> Plan:
    """Outcome `changed` when the manifest or platform was corrected, `unchanged` when `done` stands."""
    done = (UpdateStatus(obs.item_id, WorkItemStatus.DONE),) if obs.item_status != WorkItemStatus.DONE else ()
    if not obs.has_pull_request:
        blocked = plan_block(obs.node_key, obs.item_id, comment=ui(
            "⚠️ Develop done requires a PR with confirmed remote merge facts.",
            "⚠️ develop done 必须有带远端合入确认事实的 PR。"))
        return Plan(blocked.effects, "changed")
    if not obs.request_state_valid:
        plan = block_unproven(obs.node_key, obs.item_id, manifest_path,
                              f"Historical merge request state {obs.request_state!r} is invalid.", save=False)
        return Plan(plan.effects, "changed")
    if obs.merge_closed:
        return Plan(done, "unchanged")
    if obs.pr_state == PullRequestState.MERGED and obs.merged_at:
        merged = SetNode(obs.node_key, {"merged": True, "merged_at": obs.merged_at, "merge_request_state": None})
        return Plan((merged,) + done, "changed")
    if obs.pr_state in {PullRequestState.OPEN, PullRequestState.PENDING, PullRequestState.UNKNOWN}:
        fields: dict[str, Any] = {"status": "merging"}
        if obs.pr_state == PullRequestState.PENDING:
            fields["merge_request_state"] = "requested"
        return Plan((UpdateStatus(obs.item_id, MERGING), SetNode(obs.node_key, fields)), "changed")
    detail = obs.detail or ui("PR closed without merge or merge facts are unavailable",
                              "PR 未合入即关闭，或无法获得合入事实")
    blocked = plan_block(obs.node_key, obs.item_id, comment=ui(
        f"⚠️ Historical done state lacks confirmed merge; refusing closure. {detail}",
        f"⚠️ 历史 done 状态缺少已确认的合入事实；拒绝收口。{detail}"))
    return Plan(blocked.effects, "changed")


def plan_done_non_develop(item_id: str, item_status: WorkItemStatus, verdict: str | None) -> Plan:
    """A closed non-develop node: drop a stale pass-with-nits verdict and repair a regressed platform projection."""
    effects: list[Effect] = []
    if verdict == "pass-with-nits":
        effects.append(ResetReview(item_id))
    if item_status != WorkItemStatus.DONE:
        effects.append(UpdateStatus(item_id, WorkItemStatus.DONE))
    return Plan(tuple(effects), "unchanged")


def decide_tick_state(
    *, running: bool, failed: bool, full_scan: bool, audit_complete: bool, held: bool = False,
) -> str:
    """Running work wins; an unfinished full audit is neither a failure nor convergence, so the runner keeps advancing.

    With nothing running or failed, a held node (and the nodes behind it) is waiting for `omac node release`: the DAG is
    neither converged nor advancing, so the tick reports ``held`` and the caller stops with the release commands.
    """
    if running or (full_scan and not audit_complete):
        return "running"
    if failed:
        return "needs_decision"
    return "held" if held else "converged"
