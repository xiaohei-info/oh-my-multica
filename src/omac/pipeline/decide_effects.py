"""The vocabulary of the Loop's pure core: ``Observation`` in, a ``Plan`` of ``Effect`` values out.

No I/O and no platform access (enforced by tests/test_architecture.py). A decision function reads only an
``Observation`` (facts already read from the platform or the manifest) and returns a ``Plan``: an ordered tuple of
effects plus the outcome name the caller reports. ``pipeline.apply.apply`` is the only place that turns an effect
into a port call, in order, one at a time.

Crash consistency is part of the plan, not of the executor's goodwill: an effect whose outcome can be unknown and
must never be replayed (``RequestMerge``) is only valid when the plan first records the intent durably
(``SetNode(merge_request_state="intent")`` then ``SaveState``). ``validate_plan`` rejects a plan that does not.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..core.workitem import WorkItemStatus


class PlanError(ValueError):
    """A decision produced a plan that would break the intent-before-effect rule."""


@dataclass(frozen=True)
class Observation:
    """Base of the facts a decision reads. Subclasses are plain frozen data: no methods that read a platform."""


@dataclass(frozen=True)
class Effect:
    """Base of everything a plan may ask the shell to do. Each subclass maps to exactly one port call."""


# --- manifest side -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SetNode(Effect):
    """Set fields on a manifest node in memory (``status``, ``merge_request_state``, ``merged``...)."""
    node_key: str
    fields: Mapping[str, Any]


@dataclass(frozen=True)
class SaveState(Effect):
    """Persist the manifest (atomic, incremental). Everything before it in the plan is durable after it."""


# --- WorkItemStore -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class UpdateStatus(Effect):
    item_id: str
    status: WorkItemStatus


@dataclass(frozen=True)
class UpdateMetadata(Effect):
    item_id: str
    fields: Mapping[str, Any]


@dataclass(frozen=True)
class AddComment(Effect):
    item_id: str
    text: str


@dataclass(frozen=True)
class Assign(Effect):
    item_id: str
    assignee: str
    role: str
    start_run: bool = True


@dataclass(frozen=True)
class ClearAssignment(Effect):
    item_id: str


@dataclass(frozen=True)
class ResetReview(Effect):
    item_id: str


@dataclass(frozen=True)
class PrepareReviewCycle(Effect):
    item_id: str
    subject_digest: str


@dataclass(frozen=True)
class CreateWorkItem(Effect):
    fields: Mapping[str, Any]


@dataclass(frozen=True)
class SetNodeContract(Effect):
    item_id: str
    contract: Any


@dataclass(frozen=True)
class SetAuthoringIdentity(Effect):
    item_id: str
    dag_key: str
    kind: Any


@dataclass(frozen=True)
class NormalizeConfirmedMerge(Effect):
    item_id: str


# --- AgentRuntime ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Wake(Effect):
    item_id: str
    agent: str
    role: str


@dataclass(frozen=True)
class AcknowledgeAssignment(Effect):
    item_id: str


@dataclass(frozen=True)
class DispatchReviewer(Effect):
    item_id: str
    agent: str
    admission: Any = None


@dataclass(frozen=True)
class CancelRun(Effect):
    item_id: str


# --- CodeHost ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RequestMerge(Effect):
    """Ask the code host to merge. The outcome may be unknown: it is observed afterwards, never replayed."""
    pr_url: str
    command: str
    timeout_seconds: int


@dataclass(frozen=True)
class Plan:
    """What a decision returns: ordered effects, and the outcome name for the caller (``"pass"``, ``"blocked"``...)."""
    effects: tuple[Effect, ...] = ()
    outcome: str | None = field(default=None)


def validate_plan(effects: tuple[Effect, ...] | list[Effect]) -> None:
    """Reject a plan that sends a non-replayable request without a durable intent recorded before it."""
    intent_pending = False
    durable = False
    for effect in effects:
        if isinstance(effect, SetNode) and effect.fields.get("merge_request_state") == "intent":
            intent_pending = True
        elif isinstance(effect, SaveState) and intent_pending:
            durable = True
        elif isinstance(effect, RequestMerge) and not durable:
            raise PlanError(
                "RequestMerge needs SetNode(merge_request_state='intent') and SaveState earlier in the same plan")
