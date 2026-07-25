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


REVIEW_PROTOCOL_VERSION = "omac.review/v2"
REVIEW_LEDGER_SCHEMA = "omac.review-ledger/v1"

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


def build_review_obligations(item: Any) -> list[dict]:
    """Build a stable, finite review coverage set for the current work item."""
    obligations = [
        {"obligation_id": obligation_id, "category": "dimension", "requirement": requirement}
        for obligation_id, requirement in _BASE_OBLIGATIONS
    ]
    contract = getattr(item, "contract", None)
    acceptance = sorted({
        value for value in _contract_value(contract, "acceptance", [])
        if isinstance(value, str) and value.strip()
    })
    for acceptance_id in acceptance:
        obligations.append({
            "obligation_id": f"acceptance:{acceptance_id}",
            "category": "acceptance",
            "requirement": f"Verify acceptance outcome {acceptance_id}",
            "subject": acceptance_id,
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

    errors: list[str] = []
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
    if cycles and cycles[-1].get("regressed_count", 0) > 0:
        state.update({
            "mode": "convergence-audit",
            "reason": "a previously fixed blocker regressed",
        })
        return state
    if any(
        record.get("classification") == "unchanged"
        and int(record.get("seen_count", 0)) >= 3
        for record in open_records
    ):
        state.update({
            "mode": "convergence-audit",
            "reason": "a blocker remained unchanged across two rework cycles",
        })
        return state
    if len(cycles) >= 2 and all(
        cycle.get("new_count", 0) > 0 for cycle in cycles[-2:]
    ):
        state.update({
            "mode": "convergence-audit",
            "reason": "new blockers appeared in two consecutive review cycles",
        })
    return state
