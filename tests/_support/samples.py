"""Small synthetic samples shared by tests (replaces captured fixture files)."""
from __future__ import annotations

import copy


def _cycle(round_index: int, prior: list[str], *, new: int, unchanged: int) -> dict:
    return {
        "round": round_index, "subject_digest": f"subject-round-{round_index}", "verdict": "reject",
        "new_count": new, "fixed_count": 0, "regressed_count": 0, "unchanged_count": unchanged,
        "open_count": 1, "prior_open_blocker_ids": prior,
        "open_blocker_ids": ["BLK-legacy"], "reported_blocker_ids": ["BLK-legacy"],
    }


def legacy_convergence_snapshot() -> dict:
    """A develop work item that bounced three reviews with one unchanged blocker, mid-authoring."""
    return copy.deepcopy({
        "work_item": {
            "identifier": "PROJ-849", "dag_key": "contracts-schema", "status": "in_progress",
            "kind": "develop", "phase": "authoring",
            "bounces": {"worker": 14, "ci": 0, "review": 3, "merge": 0},
            "decision_required": {},
            "review_ledger": {
                "schema": "omac.review-ledger/v1",
                "cycles": [_cycle(1, [], new=1, unchanged=0),
                           _cycle(2, ["BLK-legacy"], new=0, unchanged=1),
                           _cycle(3, ["BLK-legacy"], new=0, unchanged=1)],
                "blockers": [{
                    "blocker_id": "BLK-legacy", "root_cause_key": "legacy-root-cause",
                    "obligation_id": "dimension:structure", "status": "open", "classification": "unchanged",
                    "first_seen_round": 1, "last_seen_round": 3, "seen_count": 3}],
            },
            "worker_handoff": {
                "schema": "omac.worker-handoff/v1", "state": "pending", "target_worker": "worker-a",
                "gate": "review", "source_review_subject_digest": "subject-round-3",
                "source_review_round": 3, "source_review_verdict": "reject",
                "source_review_feedback": None, "target_review_bounce": 3, "generation": "handoff-1",
                "target_agent_id": "agent-a", "baseline_direct_run_ids": ["run-1", "run-2"],
                "baseline_verification_attachment_id": "attachment-1", "target_run_id": None,
                "target_worker_bounce": 14, "terminal_observed_at": None,
            },
        }
    })


# --- control-metadata samples sized like a real node near the 8 KiB metadata limit -------------------

import hashlib
import json


def _hex(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _uuid(seed: str) -> str:
    h = _hex(seed)
    return f"{h[:8]}-{h[8:12]}-7{h[13:16]}-8{h[17:20]}-{h[20:32]}"


def _ref(name: str, filename: str, size: int) -> dict:
    return {"comment_id": _uuid(name + "-comment"), "attachment_id": _uuid(name + "-attachment"),
            "sha256": _hex(name), "bytes": size, "filename": filename}


def control_metadata_before_handoff() -> dict:
    """Serialized metadata of a review-phase node whose next handoff intent overflows 8 KiB."""
    generation = "authoring-" + _hex("generation")[:24]
    baseline = {
        "schema": "omac.reviewer-run-baseline/v1", "subject_digest": _hex("subject"),
        "target_reviewer": "reviewer-a", "target_agent_id": _uuid("reviewer-agent"),
        "cutoff_created_at": "2026-01-01T00:00:00Z", "generation": "review-" + _hex("rv")[:16],
        "baseline_direct_run_ids": [_uuid(f"run-{i}") for i in range(20)],
        "target_run_id": None, "attempt": 0,
    }
    refs = {name: _ref(name, f"omac-{name}-{_hex(name)[:12]}.yaml", 5000)
            for name in ("contract", "review-ledger", "review-obligations", "review-report", "verification")}
    return {
        "artifacts": json.dumps({"pr_url": "https://example.test/acme/demo/pull/85", "head_sha": _hex("head")[:40]}),
        "blocked_by": json.dumps(["bootstrap", "tooling", "schema-core", "schema-extras", "docs", "release-notes"]),
        "bounce_baseline": "{}",
        "contract_ref": json.dumps(refs["contract"]),
        "dag_key": "schema-convergence", "decision_required": "{}", "delivery_identity": "{}",
        "kind": "develop", "machine_feedback_ref": "{}", "phase": "review", "review_bounce": 1,
        "review_comment": "", "review_continuation": "{}",
        "review_generation": generation, "review_ledger_generation": generation,
        "review_ledger_ref": json.dumps(refs["review-ledger"]), "review_obligations": "[]",
        "review_obligations_ref": json.dumps(refs["review-obligations"]),
        "review_report_ref": json.dumps(refs["review-report"]),
        "review_subject_digest": _hex("subject"), "review_verdict": "reject", "reviewer": "reviewer-a",
        "reviewer_run_baseline": json.dumps(baseline),
        "source_refs": json.dumps([{"issue_id": _uuid(f"source-{i}"), "label": f"design doc {i}",
                                    "kind": "design", "relation": "source"} for i in range(8)]),
        "verification_ref": json.dumps(refs["verification"]),
        "worker": "worker-a", "worker_bounce": 20, "worker_handoff": "{}",
    }


def failed_handoff_intent() -> dict:
    """A complete pending worker handoff after a rejected review: 20 baseline runs, 3 blockers."""
    blockers = [{"root_cause_key": f"root-cause-{i}", "summary": f"Blocker {i}: " + "invariant violated " * 4,
                 "required_fix": f"Fix {i}: " + "bind the fact to its exact subject and add a regression case " * 3}
                for i in range(3)]
    return {
        "schema": "omac.worker-handoff/v1", "state": "pending", "target_worker": "worker-a", "gate": "review",
        "source_review_subject_digest": _hex("subject"), "source_review_round": 2,
        "source_review_verdict": "reject",
        "source_review_feedback": {
            "schema": "omac.worker-rework-feedback/v1", "verdict": "reject",
            "report_ref": _ref("review-report", "omac-review-report-aaaaaaaaaaaa.yaml", 9062),
            "ledger_ref": _ref("review-ledger", "omac-review-ledger-bbbbbbbbbbbb.yaml", 7038),
            "blockers": blockers},
        "review_context_binding": {"generation": "authoring-" + _hex("generation")[:24],
                                   "contract_sha256": _hex("contract")},
        "target_review_bounce": 2, "generation": "handoff-" + _hex("handoff")[:16],
        "target_agent_id": _uuid("worker-agent"),
        "baseline_direct_run_ids": [_uuid(f"run-{i}") for i in range(20)],
        "baseline_cutoff_created_at": "2026-01-01T00:00:00Z",
        "baseline_verification_attachment_id": _uuid("verification-attachment"),
        "baseline_pr_head_sha": _hex("head")[:40], "target_run_id": None,
        "target_worker_bounce": 20, "terminal_observed_at": None,
    }


def work_item(**fields):
    """A WorkItem as the Store would return it, with only the fields a test cares about set."""
    from omac.core.workitem import WorkItem, WorkItemStatus

    base = dict(id="item-1", workspace_id="ws", title="t", description="d", status=WorkItemStatus.TODO, dag_key="dag-1")
    return WorkItem(**{**base, **fields})
