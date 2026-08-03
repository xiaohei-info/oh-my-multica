"""Review convergence primitives.

The reviewer must disposition a finite obligation set and every open blocker.
The ledger keeps blocker identity across revisions so a workflow can distinguish
fixed work, deeper findings, regressions, and genuinely new problems.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .contract_boundaries import review_boundary_report_errors

from .acceptance_responsibility import (
    contributions,
    full_claims,
    responsibility_matrix,
)
from .manifest import loads_manifest
from .taskmeta import DECISION_REQUIRED_SCHEMA, TaskKind, TaskPhase


REVIEW_PROTOCOL_VERSION = "omac.review/v2"
REVIEW_LEDGER_SCHEMA = "omac.review-ledger/v1"
REVIEW_CONVERGENCE_DECISION_SCHEMA = "omac.review-convergence-decision/v1"
REVIEW_CONVERGENCE_EARLIEST_CYCLE = 3

_BASE_OBLIGATIONS = (
    ("dimension:authority", "Authoritative inputs and source references"),
    ("dimension:structure", "Structure, schema, dependency, and completeness"),
    ("dimension:execution", "Executable commands and fail-closed verification"),
    ("dimension:ownership", "Ownership, boundaries, and unique write paths"),
    ("dimension:evidence", "Artifact lineage and evidence traceability"),
    ("dimension:regression", "Prior blocker regression and changed-scope review"),
)

_RESULT_STATUSES = {"pass", "fail"}
_PRIOR_STATUSES = {"fixed", "unchanged", "deeper", "regressed"}
_BLOCKER_CLASSIFICATIONS = {"new", "unchanged", "deeper", "regressed"}
_LEDGER_BLOCKER_CLASSIFICATIONS = _BLOCKER_CLASSIFICATIONS | {"fixed"}
_LEDGER_BLOCKER_STATUSES = {"open", "fixed"}


def _canonical_cycle_projection(
    cycles: list[Any],
) -> tuple[list[set[str]], set[str]] | None:
    """Validate canonical cycle facts and return blocker sightings/current open IDs."""
    cycle_blocker_ids: list[set[str]] = []
    cycle_open_blocker_ids: list[set[str] | None] = []
    has_canonical_cycle_ids = False
    for index, cycle in enumerate(cycles):
        path = f"cycles[{index}]"
        if not isinstance(cycle, dict):
            raise ValueError(f"review ledger {path} must be an object")
        for field in ("round", "open_count"):
            value = cycle.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"review ledger {path}.{field} must be a non-negative integer")
        expected_cycle_round = index + 1
        if cycle["round"] != expected_cycle_round:
            raise ValueError(
                f"review ledger {path}.round must be {expected_cycle_round}")

        ids_by_field: dict[str, set[str]] = {}
        for field in (
            "prior_open_blocker_ids",
            "open_blocker_ids",
            "reported_blocker_ids",
        ):
            values = cycle.get(field)
            if values is None:
                continue
            if not isinstance(values, list):
                raise ValueError(f"review ledger {path}.{field} must be a list")
            ids: set[str] = set()
            for value in values:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"review ledger {path}.{field} must contain "
                        "non-empty strings")
                if value in ids:
                    raise ValueError(
                        f"review ledger {path}.{field} must contain unique IDs")
                ids.add(value)
            ids_by_field[field] = ids

        open_ids = ids_by_field.get("open_blocker_ids")
        reported_ids = ids_by_field.get("reported_blocker_ids")
        if open_ids is not None:
            has_canonical_cycle_ids = True
            if len(open_ids) != cycle["open_count"]:
                raise ValueError(
                    f"review ledger {path}.open_count must exactly match "
                    "open_blocker_ids")
        if reported_ids is not None:
            has_canonical_cycle_ids = True
            if open_ids is not None and not reported_ids <= open_ids:
                raise ValueError(
                    f"review ledger {path}.reported_blocker_ids must be a "
                    "subset of open_blocker_ids")

        cycle_open_blocker_ids.append(open_ids)
        cycle_blocker_ids.append((open_ids or set()) | (reported_ids or set()))

    if not has_canonical_cycle_ids:
        return None
    for index, open_ids in enumerate(cycle_open_blocker_ids):
        if open_ids is None:
            raise ValueError(
                f"review ledger cycles[{index}].open_blocker_ids is required "
                "for canonical cycle history")
    return cycle_blocker_ids, cycle_open_blocker_ids[-1] if cycles else set()


def _canonical_blocker_projection(
    cycle_blocker_ids: list[set[str]],
    latest_open_blocker_ids: set[str],
) -> list[dict]:
    sighting_rounds_by_blocker_id: dict[str, list[int]] = {}
    for round_index, blocker_ids in enumerate(cycle_blocker_ids, start=1):
        for blocker_id in blocker_ids:
            sighting_rounds_by_blocker_id.setdefault(blocker_id, []).append(
                round_index)
    return sorted((
        {
            "blocker_id": blocker_id,
            "seen_count": len(sighting_rounds),
            "first_seen_round": sighting_rounds[0],
            "status": (
                "open" if blocker_id in latest_open_blocker_ids else "fixed"
            ),
        }
        for blocker_id, sighting_rounds
        in sighting_rounds_by_blocker_id.items()
    ), key=lambda record: record["blocker_id"])


def _persisted_blocker_projection(blockers: list[dict]) -> list[dict]:
    return sorted((
        {
            "blocker_id": blocker["blocker_id"],
            "seen_count": blocker["seen_count"],
            "first_seen_round": blocker["first_seen_round"],
            "status": blocker["status"],
        }
        for blocker in blockers
    ), key=lambda record: record["blocker_id"])


def validate_review_ledger(
    ledger: Any,
    *,
    expected_round: int | None = None,
) -> dict:
    """Validate only facts consumed by the convergence decision boundary."""
    if not isinstance(ledger, dict):
        raise ValueError("review ledger must be an object")
    if ledger.get("schema") != REVIEW_LEDGER_SCHEMA:
        raise ValueError(f"review ledger schema must be {REVIEW_LEDGER_SCHEMA}")
    cycles = ledger.get("cycles")
    blockers = ledger.get("blockers")
    if not isinstance(cycles, list):
        raise ValueError("review ledger cycles must be a list")
    if not isinstance(blockers, list):
        raise ValueError("review ledger blockers must be a list")
    canonical_cycles = _canonical_cycle_projection(cycles)
    if expected_round is not None and (
        not cycles or cycles[-1]["round"] != expected_round
    ):
        raise ValueError(f"review ledger latest round must be {expected_round}")
    for index, blocker in enumerate(blockers):
        path = f"blockers[{index}]"
        if not isinstance(blocker, dict):
            raise ValueError(f"review ledger {path} must be an object")
        for field in ("blocker_id", "root_cause_key", "obligation_id"):
            value = blocker.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"review ledger {path}.{field} must be a non-empty string")
        status = blocker.get("status")
        classification = blocker.get("classification")
        if status not in _LEDGER_BLOCKER_STATUSES:
            raise ValueError(f"review ledger {path}.status is invalid")
        if classification not in _LEDGER_BLOCKER_CLASSIFICATIONS:
            raise ValueError(f"review ledger {path}.classification is invalid")
        first_seen_round = blocker.get("first_seen_round")
        seen_count = blocker.get("seen_count")
        for field, value in (
            ("first_seen_round", first_seen_round),
            ("seen_count", seen_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(
                    f"review ledger {path}.{field} must be a positive integer")
        if canonical_cycles is None:
            if first_seen_round > len(cycles):
                raise ValueError(
                    f"review ledger {path}.first_seen_round exceeds "
                    "persisted cycles")
            if seen_count > len(cycles) - first_seen_round + 1:
                raise ValueError(
                    f"review ledger {path}.seen_count exceeds persisted cycles")
    if canonical_cycles is not None:
        cycle_blocker_ids, latest_open_blocker_ids = canonical_cycles
        canonical_projection = _canonical_blocker_projection(
            cycle_blocker_ids,
            latest_open_blocker_ids,
        )
        persisted_projection = _persisted_blocker_projection(blockers)
        if persisted_projection != canonical_projection:
            raise ValueError(
                "review ledger blocker projection must exactly match canonical "
                "cycles for blocker_id, seen_count, first_seen_round, and status")
    return ledger


def _contract_value(contract: Any, name: str, default):
    if isinstance(contract, dict):
        return contract.get(name, default)
    return getattr(contract, name, default) if contract is not None else default


def _open_blockers(ledger: Any) -> list[dict]:
    if not isinstance(ledger, dict):
        return []
    blockers = ledger.get("blockers")
    if not isinstance(blockers, list):
        return []
    return [
        blocker for blocker in blockers
        if isinstance(blocker, dict) and blocker.get("status") == "open"
    ]


def open_blockers(ledger: Any) -> list[dict]:
    """Return copies of currently open blocker records."""
    return deepcopy(_open_blockers(ledger))


def required_closures(ledger: Any) -> list[dict]:
    """Return the bounded rework contract exposed to the authoring agent."""
    fields = (
        "blocker_id", "obligation_id", "root_cause_key", "summary", "required_fix")
    return [
        {field: blocker.get(field) for field in fields}
        for blocker in _open_blockers(ledger)
    ]


def review_convergence_decision(
    ledger: Any,
    *,
    minimum_cycles: int = 5,
    hard_limit: int = 10,
) -> dict | None:
    """Return a fail-closed task-boundary decision for non-converging review.

    Infrastructure retries are not ledger cycles.  This policy consumes only
    validated semantic Reviewer reports already persisted in the ledger.
    """
    if not isinstance(ledger, dict):
        return None
    cycles = [cycle for cycle in ledger.get("cycles", []) if isinstance(cycle, dict)]
    if not cycles:
        return None

    open_records = _open_blockers(ledger)
    if not open_records:
        return None
    cycle_count = len(cycles)
    open_ids = sorted(
        record.get("blocker_id") for record in open_records
        if isinstance(record.get("blocker_id"), str)
        and record.get("blocker_id")
    )
    open_roots = sorted(
        record.get("root_cause_key") for record in open_records
        if isinstance(record.get("root_cause_key"), str)
        and record.get("root_cause_key")
    )
    dimensions = sorted({
        record.get("obligation_id") for record in open_records
        if isinstance(record.get("obligation_id"), str)
        and record.get("obligation_id", "").startswith("dimension:")
    })
    late_roots = sorted({
        record.get("root_cause_key") for record in open_records
        if isinstance(record.get("root_cause_key"), str)
        and record.get("root_cause_key")
        and isinstance(record.get("first_seen_round"), int)
        and record["first_seen_round"] > minimum_cycles
    })

    unchanged_blocker_ids = sorted({
        record.get("blocker_id") for record in open_records
        if isinstance(record.get("blocker_id"), str)
        and record.get("blocker_id")
        and record.get("classification") == "unchanged"
        and int(record.get("seen_count", 0)) >= 3
    })

    non_reducing_streak = 0
    for previous, current in reversed(list(zip(cycles, cycles[1:]))):
        previous_open = previous.get("open_count")
        current_open = current.get("open_count")
        if not isinstance(previous_open, int) or not isinstance(current_open, int):
            break
        if current_open < previous_open:
            break
        non_reducing_streak += 1

    common = {
        "schema": REVIEW_CONVERGENCE_DECISION_SCHEMA,
        "cycle_count": cycle_count,
        "open_blocker_count": len(open_ids),
        "open_blocker_ids": open_ids,
        "open_root_cause_keys": open_roots,
        "obligation_dimensions": dimensions,
        "late_root_cause_keys": late_roots,
        "unchanged_blocker_ids": unchanged_blocker_ids,
        "non_reducing_streak": non_reducing_streak,
    }
    if cycle_count >= hard_limit:
        return {
            **common,
            "mode": "exhausted",
            "reason_code": "review-convergence-exhausted",
        }
    if (
        cycle_count >= REVIEW_CONVERGENCE_EARLIEST_CYCLE
        and len(dimensions) >= 3
    ) or late_roots:
        return {
            **common,
            "mode": "scope-expanding",
            "reason_code": "review-convergence-scope-expanding",
        }
    if unchanged_blocker_ids:
        return {
            **common,
            "mode": "stalled",
            "reason_code": "review-convergence-stalled",
        }
    return None


def build_review_convergence_decision(
    item: Any,
    convergence: dict,
    *,
    kind: str,
    recommended_action: str,
    node_id: str | None = None,
) -> dict:
    """Build the single persisted decision projection for all review loops."""
    decision = {
        "schema": DECISION_REQUIRED_SCHEMA,
        "reason_code": convergence["reason_code"],
        "kind": kind,
        "phase": TaskPhase.REVIEW.value,
        "gate": "review-convergence",
        "rounds": convergence["cycle_count"],
        "resume_issue_id": item.id,
        "verdict": item.review_verdict,
        "recommended_action": recommended_action,
        "convergence": convergence,
    }
    if node_id is not None:
        decision["node_id"] = node_id
    for field in ("contract_ref", "review_report_ref", "review_ledger_ref"):
        value = getattr(item, field, None)
        if isinstance(value, dict) and value:
            decision[field] = value
    return decision


def build_review_obligations(
    item: Any,
    *,
    acceptance_doc: Any = None,
    amendment_manifest: Any = None,
    amendment_evidence: dict[str, str] | None = None,
) -> list[dict]:
    """Build a stable, finite review coverage set for the current work item."""
    obligations = [
        {"obligation_id": obligation_id, "category": "dimension", "requirement": requirement}
        for obligation_id, requirement in _BASE_OBLIGATIONS
    ]
    contract = getattr(item, "contract", None)
    acceptance = sorted({
        value for value in full_claims(contract)
        if isinstance(value, str) and value.strip()
    })
    for acceptance_id in acceptance:
        obligations.append({
            "obligation_id": f"acceptance:{acceptance_id}",
            "category": "acceptance",
            "requirement": f"Verify acceptance outcome {acceptance_id}",
            "subject": acceptance_id,
        })
    action_responsibilities = sorted({
        (contribution.get("flow_id"), action_id)
        for contribution in contributions(contract)
        for action_id in (contribution.get("action_ids", []) or [])
        if isinstance(contribution.get("flow_id"), str)
        and contribution.get("flow_id").strip()
        and isinstance(action_id, str) and action_id.strip()
    })
    for flow_id, action_id in action_responsibilities:
        obligations.append({
            "obligation_id": f"acceptance-action:{flow_id}:{action_id}",
            "category": "acceptance-contribution",
            "requirement": (
                f"Verify declared contribution {action_id} within {flow_id} "
                "against this node contract"
            ),
            "subject": {"flow_id": flow_id, "action_id": action_id},
        })
    gates = _contract_value(contract, "integration_gates", [])
    gate_names = sorted({
        gate.get("name") for gate in gates
        if isinstance(gate, dict) and isinstance(gate.get("name"), str)
        and gate.get("name").strip()
    })
    for gate_name in gate_names:
        obligations.append({
            "obligation_id": f"integration:{gate_name}",
            "category": "integration",
            "requirement": f"Independently verify integration gate {gate_name}",
            "subject": gate_name,
        })
    kind = getattr(getattr(item, "kind", None), "value", getattr(item, "kind", None))
    deliverable = getattr(item, "deliverable", None)
    if kind == TaskKind.DECOMPOSE.value and isinstance(deliverable, str):
        try:
            matrix = responsibility_matrix(
                loads_manifest(deliverable), acceptance_doc)
        except (TypeError, ValueError):
            matrix = []
        if matrix:
            obligations.append({
                "obligation_id": "acceptance-responsibility:matrix",
                "category": "acceptance-responsibility",
                "requirement": (
                    "Review the compact responsibility matrix in one pass: one "
                    "canonical full-flow owner, business-Action contribution "
                    "coverage, dependency closure, and every reported gap"
                ),
                "responsibility_matrix": matrix,
            })
    if (
        kind == TaskKind.AMENDMENT.value
        and isinstance(deliverable, str)
        and amendment_manifest is not None
    ):
        try:
            from .amendment import (
                _apply_definition, _historical_contract_corrections,
                parse_proposal,
            )

            proposal = parse_proposal(deliverable)
            after = _apply_definition(amendment_manifest, proposal)
            before_matrix = responsibility_matrix(amendment_manifest, acceptance_doc)
            after_matrix = responsibility_matrix(after, acceptance_doc)
            corrections = _historical_contract_corrections(
                amendment_manifest, proposal, amendment_evidence)
        except (KeyError, TypeError, ValueError):
            before_matrix = []
            after_matrix = []
            corrections = []
        if before_matrix or after_matrix:
            obligations.append({
                "obligation_id": "acceptance-responsibility:amendment-matrix",
                "category": "acceptance-responsibility",
                "requirement": (
                    "Disposition the before/after compact acceptance responsibility "
                    "matrix: every full owner, business-Action coverage count, "
                    "contribution owner, dependency closure, and reported gap."
                ),
                "before": before_matrix,
                "after": after_matrix,
                "historical_contract_corrections": corrections,
            })
    return obligations


def review_subject_digest(item: Any, round_index: int) -> str:
    """Bind a review cycle to the exact cross-kind delivery evidence."""
    payload = {
        "kind": getattr(getattr(item, "kind", None), "value", getattr(item, "kind", None)),
        "round": round_index,
        "deliverable": getattr(item, "deliverable", None),
        "project_rules": getattr(item, "project_rules", None),
        "artifacts": getattr(item, "artifacts", None),
        "verification": getattr(item, "verification", None),
        "delivery_identity": (
            getattr(item, "delivery_identity", None).as_dict()
            if hasattr(getattr(item, "delivery_identity", None), "as_dict")
            else getattr(item, "delivery_identity", None)
        ),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _results_by_id(values: Any, id_key: str, *, errors: list[str], prefix: str) -> dict:
    if not isinstance(values, list):
        errors.append(f"{prefix} must be a list")
        return {}
    results = {}
    for value in values:
        if not isinstance(value, dict):
            errors.append(f"{prefix} entries must be objects")
            continue
        identity = value.get(id_key)
        if not isinstance(identity, str) or not identity.strip():
            errors.append(f"{prefix}.{id_key} must be a non-empty string")
            continue
        if identity in results:
            errors.append(f"{prefix} contains duplicate {id_key}: {identity}")
            continue
        results[identity] = value
    return results


def validate_convergence_review(item: Any, verdict: str, report: Any) -> list[str]:
    """Validate finite obligation coverage and cross-round blocker disposition."""
    if not isinstance(report, dict):
        return ["review_report is required"]
    if report.get("review_protocol") != REVIEW_PROTOCOL_VERSION:
        return [f"review_report.review_protocol must be {REVIEW_PROTOCOL_VERSION}"]

    errors: list[str] = review_boundary_report_errors(report)
    nits = report.get("nits")
    valid_nits = []
    if nits is None and verdict != "pass-with-nits":
        pass
    elif not isinstance(nits, list):
        errors.append("review_report.nits must be a list")
    else:
        for index, nit in enumerate(nits):
            if not isinstance(nit, str) or not nit.strip():
                errors.append(
                    f"review_report.nits[{index}] must be a non-empty string")
            else:
                valid_nits.append(nit)
    if verdict == "pass-with-nits" and not valid_nits:
        errors.append(
            "review_report pass-with-nits verdict requires at least one non-empty nit")
    obligations = getattr(item, "review_obligations", None)
    if not isinstance(obligations, list) or not obligations:
        obligations = build_review_obligations(item)
    expected = {
        obligation.get("obligation_id")
        for obligation in obligations
        if isinstance(obligation, dict) and obligation.get("obligation_id")
    }
    results = _results_by_id(
        report.get("obligation_results"), "obligation_id",
        errors=errors, prefix="review_report.obligation_results")
    for obligation_id in sorted(expected):
        if obligation_id not in results:
            errors.append(f"review_report missing obligation result: {obligation_id}")
            continue
        result = results[obligation_id]
        if result.get("status") not in _RESULT_STATUSES:
            errors.append(
                f"review_report obligation status must be pass|fail: {obligation_id}")
        if not isinstance(result.get("evidence"), str) or not result["evidence"].strip():
            errors.append(
                f"review_report obligation evidence is required: {obligation_id}")
    for obligation_id in sorted(set(results) - expected):
        errors.append(f"review_report references unknown obligation: {obligation_id}")

    prior_results = _results_by_id(
        report.get("prior_blocker_results", []), "blocker_id",
        errors=errors, prefix="review_report.prior_blocker_results")
    ledger = getattr(item, "review_ledger", None)
    all_records = {
        blocker.get("blocker_id"): blocker
        for blocker in (ledger.get("blockers", []) if isinstance(ledger, dict) else [])
        if isinstance(blocker, dict) and blocker.get("blocker_id")
    }
    cycles = ledger.get("cycles", []) if isinstance(ledger, dict) else []
    if (
        cycles
        and cycles[-1].get("subject_digest")
        == getattr(item, "review_subject_digest", None)
    ):
        prior_ids = cycles[-1].get("prior_open_blocker_ids", [])
        open_blockers = {
            blocker_id: all_records[blocker_id]
            for blocker_id in prior_ids if blocker_id in all_records
        }
    else:
        open_blockers = {
            blocker["blocker_id"]: blocker
            for blocker in _open_blockers(ledger)
            if blocker.get("blocker_id")
        }
    for blocker_id in sorted(open_blockers):
        if blocker_id not in prior_results:
            errors.append(f"review_report missing prior blocker result: {blocker_id}")
            continue
        result = prior_results[blocker_id]
        if result.get("status") not in _PRIOR_STATUSES:
            errors.append(
                f"review_report prior blocker status is invalid: {blocker_id}")
        if not isinstance(result.get("evidence"), str) or not result["evidence"].strip():
            errors.append(
                f"review_report prior blocker evidence is required: {blocker_id}")
    for blocker_id in sorted(set(prior_results) - set(open_blockers)):
        errors.append(f"review_report references unknown open blocker: {blocker_id}")

    blockers = report.get("blockers", [])
    if not isinstance(blockers, list):
        errors.append("review_report.blockers must be a list")
        blockers = []
    valid_blockers = []
    blockers_by_root = {}
    for index, blocker in enumerate(blockers):
        prefix = f"review_report.blockers[{index}]"
        if not isinstance(blocker, dict):
            errors.append(f"{prefix} must be an object for {REVIEW_PROTOCOL_VERSION}")
            continue
        for field in (
            "root_cause_key", "obligation_id", "classification",
            "summary", "evidence", "required_fix",
        ):
            if not isinstance(blocker.get(field), str) or not blocker[field].strip():
                errors.append(f"{prefix}.{field} must be a non-empty string")
        if blocker.get("obligation_id") not in expected:
            errors.append(f"{prefix}.obligation_id references an unknown obligation")
        elif results.get(blocker.get("obligation_id"), {}).get("status") != "fail":
            errors.append(
                f"{prefix}.obligation_id must reference a failed obligation")
        if blocker.get("classification") not in _BLOCKER_CLASSIFICATIONS:
            errors.append(f"{prefix}.classification is invalid")
        root_cause_key = blocker.get("root_cause_key")
        if isinstance(root_cause_key, str) and root_cause_key.strip():
            if root_cause_key in blockers_by_root:
                errors.append(
                    "review_report contains duplicate root_cause_key: "
                    f"{root_cause_key}")
            else:
                blockers_by_root[root_cause_key] = blocker
        valid_blockers.append(blocker)

    for blocker_id, record in open_blockers.items():
        result = prior_results.get(blocker_id)
        if result is None or result.get("status") not in _PRIOR_STATUSES:
            continue
        root_cause_key = record.get("root_cause_key")
        current_blocker = blockers_by_root.get(root_cause_key)
        status = result["status"]
        if status == "fixed":
            if current_blocker is not None:
                errors.append(
                    f"review_report prior blocker {blocker_id} cannot be fixed "
                    "while its root remains blocked")
            continue
        if current_blocker is None:
            errors.append(
                "review_report unresolved prior blocker is missing from blockers: "
                f"{blocker_id}")
            continue
        if current_blocker.get("classification") != status:
            errors.append(
                f"review_report blocker classification must match prior status: "
                f"{blocker_id}")

    failed = {
        obligation_id for obligation_id, result in results.items()
        if result.get("status") == "fail"
    }
    blocked_obligations = {blocker.get("obligation_id") for blocker in valid_blockers}
    for obligation_id in sorted(failed - blocked_obligations):
        errors.append(f"review_report failed obligation has no blocker: {obligation_id}")
    if verdict in {"pass", "pass-with-nits"}:
        if failed:
            errors.append("review_report pass verdict requires all obligations to pass")
        if valid_blockers:
            errors.append("review_report pass verdict requires no blockers")
        unresolved = [
            blocker_id for blocker_id, result in prior_results.items()
            if result.get("status") != "fixed"
        ]
        if unresolved:
            errors.append("review_report pass verdict requires all prior blockers to be fixed")
    elif verdict == "reject":
        if not valid_blockers:
            errors.append("review_report reject verdict requires structured blockers")
        if not failed:
            errors.append(
                "review_report reject verdict requires at least one failed obligation")
    return errors


def _blocker_id(root_cause_key: str) -> str:
    digest = hashlib.sha256(root_cause_key.encode("utf-8")).hexdigest()[:12]
    return f"BLK-{digest}"


def _new_ledger() -> dict:
    return {"schema": REVIEW_LEDGER_SCHEMA, "cycles": [], "blockers": []}


def _review_report_digest(report: dict) -> str:
    encoded = json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def advance_review_ledger(
    ledger: Any,
    report: dict,
    *,
    verdict: str,
    subject_digest: str,
    round_index: int,
) -> dict:
    """Apply one validated review report to a durable blocker ledger."""
    report_digest = _review_report_digest(report)
    current = deepcopy(ledger) if isinstance(ledger, dict) else _new_ledger()
    current.setdefault("schema", REVIEW_LEDGER_SCHEMA)
    current.setdefault("cycles", [])
    current.setdefault("blockers", [])
    if current["cycles"]:
        latest = current["cycles"][-1]
        if (
            latest.get("subject_digest") == subject_digest
            and latest.get("verdict") == verdict
            and latest.get("report_digest") == report_digest
        ):
            return current
    prior_open_ids = sorted(
        record.get("blocker_id") for record in current["blockers"]
        if isinstance(record, dict) and record.get("status") == "open"
        and record.get("blocker_id"))
    records_by_id = {
        record.get("blocker_id"): record
        for record in current["blockers"] if isinstance(record, dict)
    }
    records_by_root = {
        record.get("root_cause_key"): record
        for record in current["blockers"] if isinstance(record, dict)
    }

    fixed_count = 0
    unchanged_count = 0
    unresolved_prior_ids = set()
    for result in report.get("prior_blocker_results", []):
        if not isinstance(result, dict):
            continue
        record = records_by_id.get(result.get("blocker_id"))
        if record is None:
            continue
        status = result.get("status")
        record["last_seen_round"] = round_index
        record["last_evidence"] = result.get("evidence")
        if status == "fixed":
            record["status"] = "fixed"
            record["classification"] = "fixed"
            fixed_count += 1
        else:
            record["status"] = "open"
            record["classification"] = status
            unresolved_prior_ids.add(record["blocker_id"])
            if status == "unchanged":
                unchanged_count += 1

    new_count = 0
    regressed_count = 0
    touched_ids = set()
    for blocker in report.get("blockers", []):
        if not isinstance(blocker, dict):
            continue
        root = blocker.get("root_cause_key")
        if not isinstance(root, str) or not root:
            continue
        record = records_by_root.get(root)
        if record is None:
            record = {
                "blocker_id": _blocker_id(root),
                "root_cause_key": root,
                "first_seen_round": round_index,
                "seen_count": 0,
            }
            current["blockers"].append(record)
            records_by_root[root] = record
            records_by_id[record["blocker_id"]] = record
            classification = "new"
            new_count += 1
        elif record.get("status") == "fixed":
            classification = "regressed"
            regressed_count += 1
        else:
            classification = blocker.get("classification") or "unchanged"
            if classification == "new":
                classification = "deeper"
        record.update({
            "obligation_id": blocker.get("obligation_id"),
            "summary": blocker.get("summary"),
            "evidence": blocker.get("evidence"),
            "required_fix": blocker.get("required_fix"),
            "status": "open",
            "classification": classification,
            "last_seen_round": round_index,
            "seen_count": int(record.get("seen_count", 0)) + 1,
        })
        touched_ids.add(record["blocker_id"])

    for blocker_id in unresolved_prior_ids - touched_ids:
        record = records_by_id[blocker_id]
        record["seen_count"] = int(record.get("seen_count", 1)) + 1

    open_ids = sorted(
        record.get("blocker_id") for record in current["blockers"]
        if record.get("status") == "open" and record.get("blocker_id"))
    obligation_results = {
        result.get("obligation_id"): result.get("status")
        for result in report.get("obligation_results", [])
        if isinstance(result, dict) and result.get("obligation_id")
    }
    current["cycles"].append({
        "round": round_index,
        "subject_digest": subject_digest,
        "report_digest": report_digest,
        "verdict": verdict,
        "obligation_results": obligation_results,
        "new_count": new_count,
        "fixed_count": fixed_count,
        "regressed_count": regressed_count,
        "unchanged_count": unchanged_count,
        "open_count": len(open_ids),
        "prior_open_blocker_ids": prior_open_ids,
        "open_blocker_ids": open_ids,
        "reported_blocker_ids": sorted(touched_ids),
    })
    return current


def review_state(ledger: Any) -> dict:
    """Return a bounded convergence summary for agents and workflow reporting."""
    cycles = ledger.get("cycles", []) if isinstance(ledger, dict) else []
    open_records = _open_blockers(ledger)
    state = {
        "mode": "normal",
        "reason": None,
        "cycle_count": len(cycles),
        "open_blocker_count": len(open_records),
        "open_blocker_ids": sorted(
            record.get("blocker_id") for record in open_records
            if record.get("blocker_id")),
    }
    decision = review_convergence_decision(ledger)
    if decision is not None:
        state.update({
            # Keep the public work-show mode stable.  The structured decision
            # carries the specific stalled/scope-expanding/exhausted reason.
            "mode": "convergence-audit",
            "reason": decision["reason_code"],
            "decision": decision,
        })
    return state
