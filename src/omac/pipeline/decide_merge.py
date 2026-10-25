"""Pure decisions of the merge delivery state machine: facts in, a named action out.

No I/O and no platform access (enforced by tests/test_architecture.py). The shell in
``delivery.run_merge_delivery`` observes the platform, calls these functions, and applies the action.
The durable ``merge_request_state`` marker is what makes a restart safe:

  None --request--> "intent" --command returned--> "requested" --observed MERGED--> done

An "intent" or "requested" marker is never followed by a second merge request.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from enum import Enum

from ..core.workitem import PullRequestState, WorkItemStatus
from ..i18n import ui
from .decide_effects import (
    AddComment, Assign, Observation, Plan, ResetReview, SaveState, SetNode, UpdateMetadata, UpdateStatus, Wake,
)

VALID_MERGE_REQUEST_STATES = {None, "intent", "requested"}
MERGE_BOUNCE_PENDING_PREFIX = "bounce_pending:"


def merge_bounce_attempt(state: str | None) -> int | None:
    """解析 durable merge bounce marker 中的绝对回退次数。"""
    if not isinstance(state, str) or not state.startswith(MERGE_BOUNCE_PENDING_PREFIX):
        return None
    try:
        attempt = int(state[len(MERGE_BOUNCE_PENDING_PREFIX):])
    except ValueError:
        return None
    return attempt if attempt > 0 else None


def merge_request_state_is_valid(state: str | None) -> bool:
    return state in VALID_MERGE_REQUEST_STATES or merge_bounce_attempt(state) is not None


class MergePrecheck(Enum):
    RESUME_BOUNCE = "resume-bounce"          # a durable bounce is half done: finish it first
    BLOCK_NO_PULL_REQUEST = "no-pull-request"
    BLOCK_INVALID_MARKER = "invalid-marker"
    OBSERVE = "observe"                      # read the remote pull request, then decide again


@dataclass(frozen=True)
class MergePrecheckObservation(Observation):
    request_state: str | None
    has_pull_request: bool


def decide_merge_precheck(obs: MergePrecheckObservation) -> MergePrecheck:
    if merge_bounce_attempt(obs.request_state) is not None:
        return MergePrecheck.RESUME_BOUNCE
    if not obs.has_pull_request:
        return MergePrecheck.BLOCK_NO_PULL_REQUEST
    if not merge_request_state_is_valid(obs.request_state):
        return MergePrecheck.BLOCK_INVALID_MARKER
    return MergePrecheck.OBSERVE


class MergeStep(Enum):
    SETTLE = "settle"                  # the observation itself decides: merged, closed, unknown...
    RECORD_PENDING = "record-pending"  # queued remotely (auto-merge): remember "requested", keep waiting
    BLOCK_PRIOR_INTENT = "block-prior-intent"  # a request may already have been sent: never send another
    STAY_PENDING = "stay-pending"      # already requested: only observe
    REQUEST = "request"                # write the intent marker, then send exactly one request


@dataclass(frozen=True)
class MergeObserved(Observation):
    request_state: str | None
    pr_state: PullRequestState


def decide_merge_observed(obs: MergeObserved) -> MergeStep:
    if obs.pr_state == PullRequestState.MERGED:
        return MergeStep.SETTLE
    if obs.pr_state == PullRequestState.PENDING:
        return MergeStep.RECORD_PENDING
    if obs.pr_state != PullRequestState.OPEN:
        return MergeStep.SETTLE
    if obs.request_state == "intent":
        return MergeStep.BLOCK_PRIOR_INTENT
    if obs.request_state == "requested":
        return MergeStep.STAY_PENDING
    return MergeStep.REQUEST


class MergeAfterRequest(Enum):
    SETTLE = "settle"                    # merged / queued / closed / unknown: the observation decides
    BLOCK_TIMED_OUT_OPEN = "block-timed-out-open"  # the command timed out and the PR is still open: outcome unknown
    PENDING = "pending"                  # the command succeeded but the PR is not merged yet: observe next tick
    BOUNCE_OR_BLOCK = "bounce-or-block"  # the command failed: bounded return to the worker


@dataclass(frozen=True)
class AfterMergeRequest(Observation):
    timed_out: bool
    succeeded: bool
    pr_state: PullRequestState


def decide_after_merge_request(obs: AfterMergeRequest) -> MergeAfterRequest:
    if obs.pr_state in {PullRequestState.MERGED, PullRequestState.PENDING}:
        return MergeAfterRequest.SETTLE
    if obs.pr_state == PullRequestState.OPEN:
        if obs.timed_out:
            return MergeAfterRequest.BLOCK_TIMED_OUT_OPEN
        if obs.succeeded:
            return MergeAfterRequest.PENDING
        return MergeAfterRequest.BOUNCE_OR_BLOCK
    return MergeAfterRequest.SETTLE


# ── plans: facts in, ordered effects out (applied by pipeline.apply.apply) ──────────────────────────────────

MERGING = WorkItemStatus.IN_REVIEW  # manifest `merging` is a finer state; the platform keeps it in_review


def tail_of(text: str, n: int = 2000) -> str:
    """Only the end of a command output goes into a comment: enough to locate the problem, not a flood."""
    text = text.rstrip()
    return text if len(text) <= n else "..." + text[-n:]


def retry_command(manifest_path: str | None, node_key: str) -> str:
    if not manifest_path:
        return "omac node retry <manifest> <node>"
    return f"omac node retry {shlex.quote(manifest_path)} {shlex.quote(node_key)}"


def block_node(node_key: str, item_id: str, *, comment: str | None = None, save: bool = True) -> Plan:
    """Fail closed: the manifest says blocked (durably, unless `save` is False), then the platform follows."""
    effects = [SetNode(node_key, {"status": "blocked"})]
    if save:
        effects.append(SaveState())
    if comment is not None:
        effects.append(AddComment(item_id, comment))
    effects.append(UpdateStatus(item_id, WorkItemStatus.BLOCKED))
    return Plan(tuple(effects), "blocked")


def block_unproven(
    node_key: str, item_id: str, manifest_path: str | None, detail: str, *, save: bool = True,
) -> Plan:
    """Keep the uncertain merge marker; a person verifies the PR and runs `node retry`."""
    retry = retry_command(manifest_path, node_key)
    return block_node(node_key, item_id, save=save, comment=ui(
        f"⚠️ {detail} OMAC will not send another merge request. Verify the PR remotely, "
        f"then run:\n\n    {retry}",
        f"⚠️ {detail} OMAC 不会再次请求合入。请先远端核实 PR，再执行：\n\n    {retry}"))


def block_no_pull_request(node_key: str, item_id: str) -> Plan:
    return block_node(node_key, item_id, comment=ui(
        "⚠️ Merge is configured, but the node has no pr_url, so automatic merge cannot run.\n"
        "Confirm the worker submitted a PR URL with `omac work submit <id> --pr-url <url> ...`.",
        "⚠️ merge 已配置(command)但节点无 pr_url,无法执行自动合并。\n"
        "请确认 worker 已用 `omac work submit <id> --pr-url <url> ...` 提交 PR 地址。"))


def auth_error_durable(node_key: str) -> Plan:
    """First half of the auth fail-closed plan: durable before any remote write can fail."""
    return Plan((SetNode(node_key, {"status": "blocked"}), SaveState()), "blocked")


def auth_error_remote(node_key: str, item_id: str, manifest_path: str) -> Plan:
    """Second half: best-effort remote writes; the caller tolerates auth/platform errors here."""
    retry = retry_command(manifest_path, node_key)
    return Plan((AddComment(item_id, ui(
        "⚠️ Merge observation failed because platform authentication/authorization is unavailable. "
        "OMAC did not retry or reissue the merge request. Restore the integration credential "
        "or permission, verify the PR remotely, then inspect the node and explicitly recover with:\n\n"
        f"    {retry}\n\n"
        "After that, rerun `omac dag run <manifest>`.",
        "⚠️ 因平台认证/授权不可用，无法观察 merge 状态。OMAC 未重试、未重新发起合入请求。"
        "请修复集成凭证或权限并在远端核实 PR，然后检查该节点并显式恢复：\n\n"
        f"    {retry}\n\n"
        "之后重新运行 `omac dag run <manifest>`。")), UpdateStatus(item_id, WorkItemStatus.BLOCKED)), "blocked")


def plan_observed_step(step: MergeStep, *, node_key: str, item_id: str, manifest_path: str | None) -> Plan:
    """Effects of the non-settling steps of `decide_merge_observed` (REQUEST's send happens in plan_request)."""
    if step is MergeStep.RECORD_PENDING:
        return Plan((SetNode(node_key, {"status": "merging", "merge_request_state": "requested"}),
                     SaveState(), UpdateStatus(item_id, MERGING)), "pending")
    if step is MergeStep.STAY_PENDING:
        return Plan((SetNode(node_key, {"status": "merging"}), SaveState(), UpdateStatus(item_id, MERGING)),
                    "pending")
    if step is MergeStep.BLOCK_PRIOR_INTENT:
        return block_unproven(node_key, item_id, manifest_path,
                              "A prior merge request may have started before the process stopped.")
    raise ValueError(f"{step} has no standalone plan")


def plan_request_intent(node_key: str, item_id: str) -> Plan:
    """Intent first (durable), then the platform status; the shell appends the single RequestMerge."""
    return Plan((SetNode(node_key, {"status": "merging", "merge_request_state": "intent"}),
                 SaveState(), UpdateStatus(item_id, MERGING)))


def plan_request_sent(node_key: str, *, timed_out: bool) -> Plan:
    """A request whose command returned (even failed) is `requested`; a timeout keeps `intent` (outcome unknown)."""
    if timed_out:
        return Plan()
    return Plan((SetNode(node_key, {"merge_request_state": "requested"}), SaveState()))


def plan_blocked_timed_out_open(node_key: str, item_id: str, manifest_path: str | None) -> Plan:
    return block_unproven(
        node_key, item_id, manifest_path,
        "Merge request timed out while the PR remains OPEN; the external outcome is unknown.")


def plan_settle(
    pr_state: PullRequestState, *, merged_at: str | None, node_key: str, item_id: str,
) -> tuple[Plan, bool]:
    """Plan for an observation that decides by itself. The bool says the closed-unmerged bounce must follow."""
    if pr_state == PullRequestState.MERGED and merged_at:
        return Plan((SetNode(node_key, {"merged": True, "merged_at": merged_at, "merge_request_state": None}),),
                    "pass"), False
    if pr_state == PullRequestState.PENDING:
        return Plan((SetNode(node_key, {"status": "merging", "merge_request_state": "requested"}),),
                    "pending"), False
    if pr_state == PullRequestState.CLOSED_UNMERGED:
        return Plan((SetNode(node_key, {"merge_request_state": None}),)), True
    # UNKNOWN is uncertainty, not a failed merge: keep the persisted marker, observe again next tick.
    return Plan((SetNode(node_key, {"status": "merging"}), SaveState(), UpdateStatus(item_id, MERGING)),
                "pending"), False


def plan_bounce_pending(node_key: str, attempt: int) -> Plan:
    """Record the absolute bounce attempt durably before any remote write of the bounce."""
    return Plan((SetNode(node_key, {"status": "merging",
                                    "merge_request_state": f"{MERGE_BOUNCE_PENDING_PREFIX}{attempt}"}),
                 SaveState()))


def plan_resume_bounce(
    *, node_key: str, item_id: str, worker: str, attempt: int, current: int, label: str | None, output: str,
    merge_limit: int, consumed_attempt: int,
) -> Plan:
    """Idempotent completion of a durable merge bounce; the marker stays until the last step is saved."""
    if current > attempt:
        return block_node(node_key, item_id, comment=ui(
            "⚠️ Persisted merge bounce attempt is older than platform metadata; refusing automatic recovery.",
            "⚠️ manifest 中的 merge 回退轮次早于平台元数据；拒绝自动恢复。"))
    effects = []
    if current < attempt:
        resolved = label or ui("Merge failed; resuming the pending worker handoff",
                               "merge 失败；继续完成尚未结束的 worker 交接")
        tail = tail_of(output) or ui("(no output)", "(无输出)")
        effects += [AddComment(item_id, ui(
            f"⚠️ {resolved}. Returning to the worker to resolve the merge and rerun ci→review→merge.\n\n"
            f"--- Command output tail ---\n{tail}",
            f"⚠️ {resolved} —— 转回 worker 解决后重新走 ci→review→merge。\n\n"
            f"--- 命令输出尾部 ---\n{tail}")),
            UpdateMetadata(item_id, {"merge_bounce": attempt})]
    if merge_limit == 0 or consumed_attempt >= merge_limit:
        effects += [UpdateStatus(item_id, WorkItemStatus.BLOCKED),
                    SetNode(node_key, {"status": "blocked", "merge_request_state": None}), SaveState()]
        return Plan(tuple(effects), "blocked")
    effects += [UpdateStatus(item_id, WorkItemStatus.IN_PROGRESS), ResetReview(item_id),
                Assign(item_id, worker, "worker"), Wake(item_id, worker, "worker"),
                SetNode(node_key, {"status": "in_progress", "merge_request_state": None}), SaveState()]
    return Plan(tuple(effects), "bounce")
