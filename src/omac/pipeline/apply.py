"""The only place where an ``Effect`` becomes a port call.

``apply`` validates the whole plan first (``validate_plan``), then runs the effects strictly in order, one at a time,
and returns each effect's result in the same order. It never retries and never swallows an exception: when a port call
raises or its outcome is unknown, the effects after it are not run, and the caller (the next tick) observes instead of
repeating. The durable intent that makes that safe is itself an effect (``SetNode`` + ``SaveState``) placed before the
risky one, so the order in the plan *is* the crash-consistency contract.

tests/test_architecture.py forbids port writes anywhere else in ``pipeline`` and ``cli``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from ..core.manifest import Manifest, save_manifest
from .decide_effects import (
    AddComment, Assign, CancelRun, ClearAssignment, CreateWorkItem, DispatchReviewer, Effect,
    NormalizeConfirmedMerge, Plan, PrepareReviewCycle, RequestMerge, ResetReview, SaveState,
    SetAuthoringIdentity, SetNode, SetNodeContract, UpdateMetadata, UpdateStatus, Wake, validate_plan,
)


@dataclass
class Ports:
    """The ports an effect may touch. Unused ones stay ``None``; using one that is missing is a programming error."""
    store: Any = None
    runtime: Any = None
    code_host: Any = None


@dataclass
class ManifestState:
    """Manifest side of ``SetNode`` / ``SaveState``."""
    manifest: Manifest
    path: str | None

    def set_node(self, node_key: str, fields: dict) -> None:
        node = self.manifest.nodes[node_key]
        for name, value in fields.items():
            setattr(node, name, value)

    def save(self) -> None:
        save_manifest(self.manifest, self.path)


_RUNNERS: dict[type, Callable[[Effect, Ports, ManifestState | None], Any]] = {
    UpdateStatus: lambda e, p, s: p.store.update_status(e.item_id, e.status),
    UpdateMetadata: lambda e, p, s: p.store.update_work_item_metadata(e.item_id, **e.fields),
    AddComment: lambda e, p, s: p.store.add_comment(e.item_id, e.text),
    Assign: lambda e, p, s: p.store.assign_work_item(
        e.item_id, e.assignee, e.role, **({} if e.start_run else {"start_run": False})),
    ClearAssignment: lambda e, p, s: p.store.clear_assignment(e.item_id),
    ResetReview: lambda e, p, s: p.store.reset_review(e.item_id),
    PrepareReviewCycle: lambda e, p, s: p.store.prepare_review_cycle(e.item_id, e.subject_digest),
    CreateWorkItem: lambda e, p, s: p.store.create_work_item(**e.fields),
    SetNodeContract: lambda e, p, s: p.store.set_node_contract(e.item_id, e.contract),
    SetAuthoringIdentity: lambda e, p, s: p.store.set_authoring_identity(
        e.item_id, dag_key=e.dag_key, kind=e.kind),
    NormalizeConfirmedMerge: lambda e, p, s: p.store.normalize_confirmed_merge(e.item_id),
    Wake: lambda e, p, s: p.runtime.wake(e.item_id, e.agent, e.role),
    DispatchReviewer: lambda e, p, s: p.runtime.dispatch_reviewer(
        p.store, e.item_id, e.agent, **({} if e.admission is None else {"admission": e.admission})),
    CancelRun: lambda e, p, s: p.runtime.cancel(e.item_id),
    RequestMerge: lambda e, p, s: p.code_host.request_pull_request_merge(
        e.pr_url, e.command, e.timeout_seconds),
    SetNode: lambda e, p, s: s.set_node(e.node_key, dict(e.fields)),
    SaveState: lambda e, p, s: s.save(),
}


def apply(effects: Plan | Iterable[Effect], ports: Ports, state: ManifestState | None = None) -> list[Any]:
    """Run ``effects`` in order against ``ports``; return one result per effect (``None`` when it has none)."""
    steps = tuple(effects.effects if isinstance(effects, Plan) else effects)
    validate_plan(steps)
    return [_RUNNERS[type(effect)](effect, ports, state) for effect in steps]


def apply_one(effect: Effect, ports: Ports, state: ManifestState | None = None) -> Any:
    """Run a single effect and return its result (for the few that produce one: created item, review cycle...)."""
    return apply([effect], ports, state)[0]
