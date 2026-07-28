"""Typed evidence and artifact boundaries shared by lint, dispatch, and review."""
from __future__ import annotations

from typing import Any

from .acceptance_responsibility import dependency_closure
from .manifest import (
    ConsumedArtifact,
    EvidenceMode,
    MISSING_CONSUMES,
    ProducedArtifact,
)
from .taskmeta import DECISION_REQUIRED_SCHEMA, TaskKind, TaskPhase


CONTRACT_BOUNDARY_SCHEMA = {
    "evidence_mode": [mode.value for mode in EvidenceMode],
    "produces": [{"artifact_id": "stable-artifact-id"}],
    "consumes": [{
        "artifact_id": "stable-artifact-id",
        "producer": "upstream-node-id",
        "evidence_mode": EvidenceMode.ARTIFACT.value,
    }],
    "consumes_semantics": {
        "omitted": "transitional upstream inputs not yet enumerated",
        "empty": "no external inputs",
        "non_empty": "strict artifact allowlist",
        "null": "invalid; consumes must be omitted or a list",
    },
}

_ALLOWLIST_RULE = (
    "Only declared consumes are allowed external inputs; outputs from "
    "non-upstream or downstream nodes are outside this contract."
)
_NO_INPUT_RULE = (
    "No external inputs are allowed; upstream, non-upstream, and downstream "
    "outputs are outside this contract."
)
_TRANSITIONAL_RULE = (
    "Consumes is omitted for transitional compatibility: only inputs from "
    "transitive upstream dependencies are allowed; non-upstream and downstream "
    "outputs remain outside this contract."
)


def _value(contract: Any, name: str, default):
    if isinstance(contract, dict):
        return contract.get(name, default)
    return getattr(contract, name, default) if contract is not None else default


def _declares(contract: Any, name: str) -> bool:
    if isinstance(contract, dict):
        return name in contract
    if contract is None:
        return False
    if name == "consumes":
        return getattr(contract, name, MISSING_CONSUMES) is not MISSING_CONSUMES
    return getattr(contract, name, None) is not None


def _mode_value(value: Any) -> str | None:
    if isinstance(value, EvidenceMode):
        return value.value
    return value if isinstance(value, str) else None


def _produced_value(value: Any) -> tuple[str | None, bool]:
    if isinstance(value, ProducedArtifact):
        return value.artifact_id, True
    if isinstance(value, dict):
        return value.get("artifact_id"), True
    return None, False


def _consumed_value(value: Any) -> tuple[str | None, str | None, str | None, bool]:
    if isinstance(value, ConsumedArtifact):
        return (
            value.artifact_id,
            value.producer,
            _mode_value(value.evidence_mode),
            True,
        )
    if isinstance(value, dict):
        return (
            value.get("artifact_id"),
            value.get("producer"),
            _mode_value(value.get("evidence_mode")),
            True,
        )
    return None, None, None, False


def has_contract_boundary(contract: Any) -> bool:
    return bool(
        _value(contract, "evidence_mode", None) is not None
        or _value(contract, "produces", [])
        or _declares(contract, "consumes")
    )


def responsibility_summary(contract: Any) -> dict[str, Any] | None:
    """Return a bounded responsibility projection without historical prose."""
    if not has_contract_boundary(contract):
        return None
    consumes_declared = _declares(contract, "consumes")
    consumes = [] if consumes_declared else None
    raw_consumes = _value(contract, "consumes", None)
    if isinstance(raw_consumes, list):
        for value in raw_consumes:
            artifact_id, producer, evidence_mode, valid = _consumed_value(value)
            if not valid:
                continue
            consumes.append({
                "artifact_id": artifact_id,
                "producer": producer,
                "evidence_mode": evidence_mode,
            })
    produces = []
    raw_produces = _value(contract, "produces", [])
    if isinstance(raw_produces, list):
        for value in raw_produces:
            artifact_id, valid = _produced_value(value)
            if valid:
                produces.append(artifact_id)
    if not consumes_declared:
        input_policy = "transitional-upstream"
        boundary_rule = _TRANSITIONAL_RULE
    elif not isinstance(raw_consumes, list):
        input_policy = "invalid"
        boundary_rule = "Consumes is declared but must be a list, not null."
    elif not consumes:
        input_policy = "none"
        boundary_rule = _NO_INPUT_RULE
    else:
        input_policy = "allowlist"
        boundary_rule = _ALLOWLIST_RULE
    return {
        "evidence_mode": _mode_value(_value(contract, "evidence_mode", None)),
        "input_policy": input_policy,
        "allowed_inputs": consumes,
        "produces": produces,
        "boundary_rule": boundary_rule,
    }


def _shape_errors(node: Any) -> list[str]:
    contract = getattr(node, "contract", None)
    if not has_contract_boundary(contract):
        return []
    prefix = f"node {node.id}: contract"
    errors = []
    mode = _mode_value(_value(contract, "evidence_mode", None))
    if mode not in {value.value for value in EvidenceMode}:
        errors.append(
            f"{prefix}.evidence_mode must be fixture|artifact|live; choose the "
            "evidence class the node can prove independently")

    produces = _value(contract, "produces", [])
    if not isinstance(produces, list):
        errors.append(
            f"{prefix}.produces must be a list of artifact_id objects; use "
            "produces: [{artifact_id: stable-id}]")
        produces = []
    produced_ids = []
    for index, value in enumerate(produces):
        artifact_id, valid = _produced_value(value)
        item_prefix = f"{prefix}.produces[{index}]"
        if not valid:
            errors.append(f"{item_prefix} must be an object with artifact_id")
            continue
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            errors.append(f"{item_prefix}.artifact_id must be a non-empty string")
            continue
        produced_ids.append(artifact_id)
    if len(produced_ids) != len(set(produced_ids)):
        errors.append(f"{prefix}.produces must not contain duplicate artifact_id values")

    if not _declares(contract, "consumes"):
        return errors
    consumes = _value(contract, "consumes", None)
    if not isinstance(consumes, list):
        errors.append(
            f"{prefix}.consumes must be a list of typed upstream artifact inputs")
        consumes = []
    consumed_keys = []
    for index, value in enumerate(consumes):
        artifact_id, producer, input_mode, valid = _consumed_value(value)
        item_prefix = f"{prefix}.consumes[{index}]"
        if not valid:
            errors.append(
                f"{item_prefix} must declare artifact_id, producer, and evidence_mode")
            continue
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            errors.append(f"{item_prefix}.artifact_id must be a non-empty string")
        if not isinstance(producer, str) or not producer.strip():
            errors.append(f"{item_prefix}.producer must be a non-empty node id")
        if input_mode not in {value.value for value in EvidenceMode}:
            errors.append(
                f"{item_prefix}.evidence_mode must be fixture|artifact|live")
        if isinstance(artifact_id, str) and isinstance(producer, str):
            consumed_keys.append((artifact_id, producer))
        if mode == EvidenceMode.FIXTURE.value and input_mode == EvidenceMode.LIVE.value:
            errors.append(
                f"{prefix} fixture evidence_mode cannot require live evidence from "
                f"{producer!r}; change the node evidence_mode or replace the input "
                "with a deterministic fixture/artifact contract")
    if len(consumed_keys) != len(set(consumed_keys)):
        errors.append(
            f"{prefix}.consumes must not contain duplicate artifact_id/producer pairs")
    return errors


def manifest_boundary_errors(
    manifest: Any, *, node_ids: set[str] | None = None,
) -> list[str]:
    """Validate typed artifact identity and transitive producer ownership."""
    selected = set(manifest.nodes) if node_ids is None else set(node_ids)
    errors = []
    produced_by: dict[str, list[str]] = {}
    selected_consumed_artifacts: set[str] = set()
    for node in manifest.nodes.values():
        if node.id in selected:
            errors.extend(_shape_errors(node))
            contract = getattr(node, "contract", None)
            consumes = _value(contract, "consumes", None)
            if isinstance(consumes, list):
                for value in consumes:
                    artifact_id, _producer, _mode, valid = _consumed_value(value)
                    if valid and isinstance(artifact_id, str) and artifact_id.strip():
                        selected_consumed_artifacts.add(artifact_id)
        produces = _value(getattr(node, "contract", None), "produces", [])
        if not isinstance(produces, list):
            continue
        for value in produces:
            artifact_id, valid = _produced_value(value)
            if valid and isinstance(artifact_id, str) and artifact_id.strip():
                produced_by.setdefault(artifact_id, []).append(node.id)

    for artifact_id, producers in sorted(produced_by.items()):
        unique = sorted(set(producers))
        if len(unique) > 1 and (
            selected.intersection(unique)
            or artifact_id in selected_consumed_artifacts
        ):
            errors.append(
                f"artifact_id '{artifact_id}' has multiple producers: "
                f"{', '.join(unique)}; assign one canonical producer or use "
                "distinct stable artifact ids")

    for node in manifest.nodes.values():
        if node.id not in selected:
            continue
        contract = getattr(node, "contract", None)
        if not _declares(contract, "consumes"):
            continue
        consumes = _value(contract, "consumes", None)
        if not isinstance(consumes, list):
            continue
        upstream = dependency_closure(manifest, node.id)
        for index, value in enumerate(consumes):
            artifact_id, producer, _input_mode, valid = _consumed_value(value)
            if not valid or not isinstance(producer, str) or not producer.strip():
                continue
            prefix = f"node {node.id}: contract.consumes[{index}]"
            if producer not in manifest.nodes:
                errors.append(
                    f"{prefix} producer '{producer}' does not exist; add the producer "
                    "node or remove the consume declaration")
                continue
            if producer not in upstream:
                errors.append(
                    f"{prefix} producer '{producer}' is not a transitive upstream "
                    "dependency; add it to blocked_by only if it is a real prerequisite, "
                    "otherwise remove the consume declaration")
            if (
                isinstance(artifact_id, str)
                and artifact_id.strip()
                and producer not in produced_by.get(artifact_id, [])
            ):
                errors.append(
                    f"{prefix} artifact_id '{artifact_id}' is not produced by "
                    f"'{producer}'; declare it in that producer's contract.produces "
                    "or correct the consume identity")
    return errors


def review_boundary_report_errors(report: Any) -> list[str]:
    """Validate optional structured boundary requirements in Reviewer blockers."""
    if not isinstance(report, dict):
        return []
    blockers = report.get("blockers", [])
    if not isinstance(blockers, list):
        return []
    errors = []
    valid_modes = {mode.value for mode in EvidenceMode}
    for blocker_index, blocker in enumerate(blockers):
        if not isinstance(blocker, dict):
            continue
        prefix = f"review_report.blockers[{blocker_index}]"
        if "required_evidence_mode" in blocker:
            mode = _mode_value(blocker.get("required_evidence_mode"))
            if mode not in valid_modes:
                errors.append(
                    f"{prefix}.required_evidence_mode must be fixture|artifact|live")
        if "required_inputs" not in blocker:
            continue
        inputs = blocker.get("required_inputs")
        if not isinstance(inputs, list):
            errors.append(f"{prefix}.required_inputs must be a list")
            continue
        for input_index, value in enumerate(inputs):
            artifact_id, producer, mode, valid = _consumed_value(value)
            item_prefix = f"{prefix}.required_inputs[{input_index}]"
            if not valid:
                errors.append(
                    f"{item_prefix} must declare artifact_id, producer, and evidence_mode")
                continue
            if not isinstance(artifact_id, str) or not artifact_id.strip():
                errors.append(
                    f"{item_prefix}.artifact_id must be a non-empty string")
            if not isinstance(producer, str) or not producer.strip():
                errors.append(
                    f"{item_prefix}.producer must be a non-empty node id")
            if mode not in valid_modes:
                errors.append(
                    f"{item_prefix}.evidence_mode must be fixture|artifact|live")
    return errors


def contract_boundary_conflicts(
    manifest: Any, node: Any, item: Any,
) -> list[dict[str, str]]:
    """Detect Reviewer requirements that cross the current typed node boundary."""
    contract = getattr(node, "contract", None)
    if not has_contract_boundary(contract):
        return []
    report = getattr(item, "review_report", None)
    blockers = report.get("blockers", []) if isinstance(report, dict) else []
    if not isinstance(blockers, list):
        return []

    upstream = dependency_closure(manifest, node.id)
    consumes_declared = _declares(contract, "consumes")
    allowed_inputs = set()
    consumes = _value(contract, "consumes", None)
    if isinstance(consumes, list):
        for value in consumes:
            artifact_id, producer, input_mode, valid = _consumed_value(value)
            if valid:
                allowed_inputs.add((artifact_id, producer, input_mode))

    conflicts = []
    current_mode = _mode_value(_value(contract, "evidence_mode", None))
    valid_modes = {mode.value for mode in EvidenceMode}
    for blocker in blockers:
        if not isinstance(blocker, dict):
            continue
        required_mode = _mode_value(blocker.get("required_evidence_mode"))
        if (
            current_mode == EvidenceMode.FIXTURE.value
            and required_mode in valid_modes
            and required_mode == EvidenceMode.LIVE.value
        ):
            conflicts.append({"reason_code": "fixture-requires-live-evidence"})

        required_inputs = blocker.get("required_inputs", [])
        if isinstance(required_inputs, list):
            for value in required_inputs:
                artifact_id, producer, input_mode, valid = _consumed_value(value)
                if (
                    not valid
                    or not isinstance(artifact_id, str)
                    or not artifact_id.strip()
                    or not isinstance(producer, str)
                    or not producer.strip()
                    or input_mode not in valid_modes
                ):
                    continue
                fixture_requires_live = (
                    current_mode == EvidenceMode.FIXTURE.value
                    and input_mode == EvidenceMode.LIVE.value
                )
                if fixture_requires_live:
                    conflicts.append({
                        "reason_code": "fixture-requires-live-evidence"})
                if (artifact_id, producer, input_mode) in allowed_inputs:
                    continue
                if producer not in upstream:
                    conflicts.append({
                        "reason_code": "review-requires-non-upstream-artifact",
                        "artifact_id": artifact_id or "",
                        "producer": producer or "",
                    })
                elif consumes_declared:
                    conflicts.append({
                        "reason_code": "review-requires-undeclared-artifact",
                        "artifact_id": artifact_id or "",
                        "producer": producer or "",
                    })

    unique = []
    seen = set()
    for conflict in conflicts:
        key = tuple(sorted(conflict.items()))
        if key in seen:
            continue
        seen.add(key)
        unique.append(conflict)
    return unique


def build_contract_boundary_decision(
    item: Any, node: Any, conflicts: list[dict[str, str]],
) -> dict[str, Any]:
    """Project a bounded boundary conflict through the existing decision schema."""
    decision = {
        "schema": DECISION_REQUIRED_SCHEMA,
        "reason_code": "contract-boundary-conflict",
        "kind": TaskKind.DEVELOP.value,
        "phase": TaskPhase.REVIEW.value,
        "gate": "review-boundary",
        "resume_issue_id": item.id,
        "node_id": node.id,
        "conflict_codes": sorted({
            conflict["reason_code"] for conflict in conflicts
        }),
        "artifact_ids": sorted({
            conflict["artifact_id"] for conflict in conflicts
            if conflict.get("artifact_id")
        }),
        "producer_nodes": sorted({
            conflict["producer"] for conflict in conflicts
            if conflict.get("producer")
        }),
    }
    review_report_ref = getattr(item, "review_report_ref", None)
    if isinstance(review_report_ref, dict) and review_report_ref:
        decision["review_report_ref"] = review_report_ref
    return decision
