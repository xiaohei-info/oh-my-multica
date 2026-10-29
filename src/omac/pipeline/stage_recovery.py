"""Explicit stage recovery writes for `omac node retry` and amendment apply (docs/design/README.md §7.3.1).

The checks and digests are pure functions in ``core.stage_recovery``; every write here is an Effect run by ``apply``.
A read between two writes splits the recovery into separate ``apply`` calls. This module does not dispatch an Agent
and does not request or observe a merge: the caller persists its own intent first, and the next `dag run` advances.
"""
from __future__ import annotations

from dataclasses import replace

from ..core.stage_recovery import (
    authoring_generation, authoring_recovery_intent, authoring_recovery_target, stage_recovery_subject,
    validate_stage_recovery,
)
from ..core.taskmeta import WorkerHandoffIntent, review_context_binding
from ..errors import PlatformError
from .apply import Ports, apply, apply_one
from .decide_effects import RestoreAuthoringGeneration, UpdateMetadata
from .decide_recovery import plan_enter_review, plan_stage_reset


def _observe_write(store, item_id, effect, expected):
    """Apply one write, then read it back; an error is not permission to replay, so the durable result decides."""
    try:
        apply_one(effect, Ports(store=store))
    except PlatformError:
        observed = store.get_work_item(item_id)
        if not expected(observed):
            raise
        return observed
    observed = store.get_work_item(item_id)
    if not expected(observed):
        raise PlatformError("Authoring recovery write did not converge; repeat node retry after inspecting work show")
    return observed


def _prepare_authoring_retry(node, store, item, intent):
    """Persist a recovery intent before the first destructive stage write."""
    item_id = node.work_item_id
    if intent.authoring_recovery is None:
        intent = authoring_recovery_intent(node, item, intent)
        if intent is None:
            return "todo"
        item = _observe_write(store, item_id, UpdateMetadata(item_id, {"worker_handoff": intent}),
                              lambda current: current.worker_handoff == intent)
    generation, reached = authoring_recovery_target(node, item, intent)
    if not reached(item):
        item = _observe_write(store, item_id, RestoreAuthoringGeneration(
            item_id, node.contract, generation, worker_handoff=intent), reached)
    completed = replace(intent, state="pending", authoring_recovery=None,
                        review_context_binding=review_context_binding(item))
    _observe_write(store, item_id, UpdateMetadata(item_id, {"worker_handoff": completed}),
                   lambda current: current.worker_handoff == completed and current.review_generation == generation)
    return "todo"


def prepare_stage_recovery(
    node,
    store,
    stage: str,
    *,
    expected_review_subject: str | None = None,
    expected_review_generation: str | None = None,
    expected_bounce_baseline: dict[str, int] | None = None,
    sync_contract: bool = False,
    worker_handoff: WorkerHandoffIntent | None = None,
) -> str:
    """Prepare the review/authoring stage on the work item; merging only validates and delegates."""
    if not node.work_item_id:
        return "no-work-item"
    item_id = node.work_item_id
    item = store.get_work_item(item_id)
    validate_stage_recovery(item, stage)
    if stage == "authoring":
        if worker_handoff is not None:
            return _prepare_authoring_retry(node, store, item, worker_handoff)
        generation = expected_review_generation or authoring_generation(node, item)
        apply_one(RestoreAuthoringGeneration(item_id, node.contract, generation, expected_bounce_baseline),
                  Ports(store=store))
        return "todo"
    # A replay after an unknown response is safe: retiring the handoff is an idempotent metadata write.
    reset = plan_stage_reset(item_id, stage, retire_handoff=item.worker_handoff is not None,
                             contract=node.contract if sync_contract else None)
    apply(reset, Ports(store=store))
    if reset.outcome:
        return reset.outcome
    subject = expected_review_subject or stage_recovery_subject(node, store.get_work_item(item_id))
    entered = plan_enter_review(item_id, subject)
    apply(entered, Ports(store=store))
    return entered.outcome
