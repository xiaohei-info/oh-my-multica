"""Definition-only repair for shell placeholders damaged by old manifest loading."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

import yaml

from ..core.amendment import (
    AMENDMENT_IDENTITY_SCHEMA,
    _amendment_id,
    amendment_apply_blocker,
    parse_proposal,
)
from ..core.manifest import (
    Manifest,
    _dump_contract,
    _load_contract,
    load_manifest,
    save_manifest,
)
from ..errors import NeedsDecision, PlatformError, ValidationError

_REPAIR_SCHEMA = "omac.contract-command-repair/v1"
_EMPTY_DEFAULT_PLACEHOLDER = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-\}")
_TOTAL_RUNTIME_FIELDS = (
    "work_item_id", "status", "worker", "reviewer", "blocked_by",
    "merged", "merged_at", "merge_request_state", "recovery_marker",
)
_ITEM_RUNTIME_FIELDS = (
    "status", "phase", "worker", "reviewer", "blocked_by", "artifacts",
    "verification", "verification_ref", "review_verdict", "review_comment",
    "machine_feedback", "machine_feedback_ref", "review_report",
    "review_report_ref", "review_subject_digest", "review_obligations",
    "review_obligations_ref", "review_ledger", "review_ledger_ref",
    "review_generation", "review_ledger_generation", "review_continuation",
    "reviewer_run_baseline", "worker_handoff", "delivery_identity",
    "decision_required", "bounces", "bounce_baseline", "deliverable",
    "deliverable_ref", "project_rules", "project_rules_ref",
    "agent_run_finished_without_submit", "agent_run_failed",
    "platform_assignee_id", "unknown_persisted_fields",
    "review_nits_acceptance",
)


def _json_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _contract_source(contract: Any) -> str:
    canonical = (
        _dump_contract(_load_contract(contract))
        if isinstance(contract, dict) else _dump_contract(contract)
    )
    return yaml.safe_dump(canonical, sort_keys=False, allow_unicode=True)


def _contract_digest(contract: Any) -> str:
    return hashlib.sha256(_contract_source(contract).encode("utf-8")).hexdigest()


def _empty_default_expansion(command: Any) -> Any:
    if not isinstance(command, str):
        return command
    return _EMPTY_DEFAULT_PLACEHOLDER.sub("", command)


def _command_projection(contract: dict[str, Any]) -> dict[str, Any]:
    projected = copy.deepcopy(contract)
    commands = projected.get("verification_commands")
    if isinstance(commands, list):
        projected["verification_commands"] = [
            "__omac_repair_command__" for _ in commands
        ]
    gates = projected.get("integration_gates")
    if isinstance(gates, list):
        for gate in gates:
            if not isinstance(gate, dict):
                continue
            gate_commands = gate.get("commands")
            if isinstance(gate_commands, list):
                gate["commands"] = [
                    "__omac_repair_command__" for _ in gate_commands
                ]
    return projected


def _repairable_command_changes(
    current: dict[str, Any], approved: dict[str, Any],
) -> list[dict[str, Any]]:
    if _command_projection(current) != _command_projection(approved):
        raise ValidationError(
            "Contract drift outside verification command placeholders; refusing repair")
    changes: list[dict[str, Any]] = []
    current_commands = current.get("verification_commands")
    approved_commands = approved.get("verification_commands")
    if not isinstance(current_commands, list) or not isinstance(approved_commands, list):
        raise ValidationError("Approved contract verification_commands must be lists")
    if len(current_commands) != len(approved_commands):
        raise ValidationError("Contract verification command list length changed")
    for index, (before, after) in enumerate(zip(current_commands, approved_commands)):
        if before == after:
            continue
        if not isinstance(after, str) or not _EMPTY_DEFAULT_PLACEHOLDER.search(after):
            raise ValidationError(
                f"verification_commands[{index}] is not an empty-default placeholder repair")
        if before != _empty_default_expansion(after):
            raise ValidationError(
                f"verification_commands[{index}] differs outside the known empty-default expansion")
        changes.append({"path": f"verification_commands[{index}]", "before": before, "after": after})

    current_gates = current.get("integration_gates")
    approved_gates = approved.get("integration_gates")
    if not isinstance(current_gates, list) or not isinstance(approved_gates, list):
        raise ValidationError("Approved contract integration_gates must be lists")
    if len(current_gates) != len(approved_gates):
        raise ValidationError("Contract integration gate list length changed")
    for gate_index, (before_gate, after_gate) in enumerate(
        zip(current_gates, approved_gates)
    ):
        if not isinstance(before_gate, dict) or not isinstance(after_gate, dict):
            raise ValidationError("Integration gate entries must be objects")
        before_commands = before_gate.get("commands")
        after_commands = after_gate.get("commands")
        if not isinstance(before_commands, list) or not isinstance(after_commands, list):
            raise ValidationError("Integration gate commands must be lists")
        if len(before_commands) != len(after_commands):
            raise ValidationError("Integration gate command list length changed")
        for command_index, (before, after) in enumerate(
            zip(before_commands, after_commands)
        ):
            if before == after:
                continue
            if not isinstance(after, str) or not _EMPTY_DEFAULT_PLACEHOLDER.search(after):
                raise ValidationError(
                    f"integration_gates[{gate_index}].commands[{command_index}] "
                    "is not an empty-default placeholder repair")
            if before != _empty_default_expansion(after):
                raise ValidationError(
                    f"integration_gates[{gate_index}].commands[{command_index}] "
                    "differs outside the known empty-default expansion")
            changes.append({
                "path": f"integration_gates[{gate_index}].commands[{command_index}]",
                "before": before,
                "after": after,
            })
    if not changes:
        raise ValidationError("No known empty-default shell command damage found")
    return changes


def _runtime_value(value: Any) -> Any:
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "value") and not isinstance(value, (dict, list, tuple, str)):
        return value.value
    if isinstance(value, dict):
        return {key: _runtime_value(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_runtime_value(val) for val in value]
    return value


def _item_runtime_snapshot(item: Any) -> dict[str, Any]:
    return {
        field: _runtime_value(getattr(item, field, None))
        for field in _ITEM_RUNTIME_FIELDS
    }


def _node_runtime_snapshot(node: Any) -> dict[str, Any]:
    return {
        field: _runtime_value(getattr(node, field, None))
        for field in _TOTAL_RUNTIME_FIELDS
    }


def _receipt_path(manifest_path: str, repair_id: str) -> Path:
    path = Path(manifest_path).resolve()
    return path.parent / "contract-repair" / f"{repair_id}.json"


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _load_receipt(path: Path) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise NeedsDecision(
            f"Contract repair receipt is unreadable: {path}",
            report={
                "reason_code": "contract-repair-receipt-unreadable",
                "receipt": str(path),
                "detail": str(exc),
            },
        ) from exc
    if not isinstance(value, dict) or value.get("schema") != _REPAIR_SCHEMA:
        raise NeedsDecision(
            f"Contract repair receipt has an unsupported schema: {path}",
            report={
                "reason_code": "contract-repair-receipt-invalid",
                "receipt": str(path),
            },
        )
    return value


def _decision_report(message: str, reason_code: str, receipt: Path) -> NeedsDecision:
    manifest = "<manifest>"
    amendment = "<approved-amendment>"
    try:
        with receipt.open(encoding="utf-8") as stream:
            saved = json.load(stream)
        manifest = str(saved.get("manifest_path") or manifest)
        amendment = str(saved.get("amendment_file") or amendment)
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return NeedsDecision(message, report={
        "reason_code": reason_code,
        "receipt": str(receipt),
        "next_action": (
            "omac dag amend repair-contract-commands "
            f"{manifest} {amendment} --output json"
        ),
    })


def _validate_approved_amendment(
    manifest: Manifest,
    amendment: dict[str, Any],
    manifest_path: str,
) -> list[tuple[str, dict[str, Any], list[dict[str, Any]]]]:
    amendment_id = amendment.get("amendment_id")
    review = amendment.get("review")
    if not isinstance(amendment_id, str) or not amendment_id:
        raise ValidationError("Applied amendment repair requires amendment_id")
    if not isinstance(review, dict) or review.get("verdict") != "pass":
        raise ValidationError("Contract repair requires a Reviewer-passed amendment")
    if amendment.get("human_confirmation") != "applied":
        raise ValidationError("Contract repair requires an already-applied amendment")
    if manifest.meta.get("last_amendment_id") != amendment_id:
        raise ValidationError("Approved amendment is not the manifest's applied amendment")
    ledger = manifest.meta.get("amendment_apply")
    if (
        not isinstance(ledger, dict)
        or ledger.get("amendment_id") != amendment_id
        or not isinstance(ledger.get("nodes"), dict)
    ):
        raise ValidationError("Applied amendment repair requires its matching apply ledger")
    apply_blocker = amendment_apply_blocker(manifest, manifest_path)
    if apply_blocker is not None:
        raise NeedsDecision(
            "Contract repair is blocked until the amendment apply ledger is complete",
            report={
                **apply_blocker,
                "reason": "contract-repair-amendment-apply-incomplete",
                "next_action": apply_blocker.get("resume_command"),
            },
        )
    if amendment.get("identity_schema") != AMENDMENT_IDENTITY_SCHEMA:
        raise ValidationError(
            "Contract repair requires the v2 reviewed amendment identity")
    base = amendment.get("base")
    analysis = amendment.get("analysis")
    if not isinstance(base, dict) or not isinstance(analysis, dict):
        raise ValidationError(
            "Applied amendment repair requires its reviewed base and analysis")
    review_issue_id = review.get("issue_id")
    if not isinstance(review_issue_id, str) or not review_issue_id:
        raise ValidationError("Applied amendment repair requires review.issue_id")
    expected_id = _amendment_id(
        base.get("definition_sha256") or "",
        amendment,
        analysis.get("minimal_rerun") or {},
        analysis.get("historical_contract_corrections") or [],
        base.get("evidence_sha256") or {},
        manifest_digest_value=base.get("manifest_sha256"),
        acceptance_digest=base.get("acceptance_sha256"),
        issue_id=review_issue_id,
        reviewer_verdict=review.get("verdict"),
    )
    if expected_id != amendment_id:
        raise ValidationError(
            "Applied amendment repair identity does not match the reviewed source")

    targets = []
    for operation in amendment.get("operations") or []:
        if not isinstance(operation, dict) or operation.get("op") != "update":
            continue
        changes = operation.get("set")
        if not isinstance(changes, dict) or "contract" not in changes:
            continue
        if set(changes) != {"contract"}:
            raise ValidationError(
                "Contract command repair accepts only update.set.contract operations")
        node_id = str(operation.get("node") or "").strip()
        node = manifest.nodes.get(node_id)
        if node is None:
            raise ValidationError(f"Approved repair references unknown node {node_id}")
        if not isinstance(changes["contract"], dict):
            raise ValidationError(f"Approved contract for node {node_id} is invalid")
        approved = _dump_contract(_load_contract(changes["contract"]))
        if not isinstance(approved, dict):
            raise ValidationError(f"Approved contract for node {node_id} is invalid")
        current = _dump_contract(node.contract) if node.contract is not None else {}
        try:
            command_changes = _repairable_command_changes(current, approved)
        except ValidationError:
            if current == approved:
                command_changes = []
            else:
                raise
        apply_entry = (ledger.get("nodes") or {}).get(node_id)
        if not isinstance(apply_entry, dict) or apply_entry.get(
                "expected_contract_sha256") != _contract_digest(approved):
            raise ValidationError(
                f"Applied amendment ledger does not bind approved contract for {node_id}")
        targets.append((node_id, approved, command_changes))
    if not targets:
        raise ValidationError("Approved amendment contains no repairable contract command operations")
    return targets


def _apply_commands(contract: dict[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any]:
    repaired = copy.deepcopy(contract)
    for change in changes:
        path = change["path"]
        if path.startswith("verification_commands["):
            index = int(path.split("[", 1)[1].split("]", 1)[0])
            repaired["verification_commands"][index] = change["after"]
            continue
        match = re.fullmatch(r"integration_gates\[(\d+)\]\.commands\[(\d+)\]", path)
        if match is None:
            raise ValidationError(f"Unsupported repair path: {path}")
        gate_index, command_index = (int(value) for value in match.groups())
        repaired["integration_gates"][gate_index]["commands"][command_index] = (
            change["after"])
    return repaired


def _find_contract_publications(store: Any, item_id: str, digest: str) -> list[dict[str, Any]]:
    finder = getattr(store, "find_contract_publications", None)
    if not callable(finder):
        return []
    publications = list(finder(item_id, digest) or [])
    for ref in publications:
        if not isinstance(ref, dict):
            raise PlatformError("Store returned an invalid contract publication reference")
        if ref.get("work_item_id") != item_id:
            raise PlatformError(
                f"Contract publication is not bound to work item {item_id}")
        if str(ref.get("sha256") or "").lower() != digest.lower():
            raise PlatformError(
                f"Contract publication digest is not bound to work item {item_id}")
        if not str(ref.get("comment_id") or "").strip() or not str(
                ref.get("attachment_id") or "").strip():
            raise PlatformError("Contract publication reference identity is incomplete")
        if str(ref.get("filename") or "") != f"omac-contract-{digest[:12]}.yaml":
            raise PlatformError("Contract publication filename is not bound to digest")
        size = ref.get("bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise PlatformError("Contract publication byte size is missing or invalid")
    return publications


def _sync_contract_ref(store: Any, item_id: str, ref: dict[str, Any]) -> None:
    sync = getattr(store, "sync_contract_ref", None)
    if not callable(sync):
        raise PlatformError("Store does not support contract reference-only repair")
    sync(item_id, ref)


def _receipt_baseline_digest(
    item_id: str,
    digest: str,
    entry: dict[str, Any],
) -> str:
    return _json_digest({
        "work_item_id": item_id,
        "approved_contract_sha256": digest,
        "changes": entry.get("changes"),
        "runtime_snapshot": entry.get("runtime_snapshot"),
        "manifest_snapshot": entry.get("manifest_snapshot"),
    })


def _receipt_entry_error(
    receipt: Path, node_id: str, message: str,
) -> NeedsDecision:
    return _needs_decision(
        f"Contract repair receipt is invalid for {node_id}: {message}",
        "contract-repair-receipt-invalid", receipt)


def _validate_receipt_entry(
    entry: dict[str, Any],
    node_id: str,
    item_id: str,
    digest: str,
    receipt: Path,
    *,
    allow_pending: bool = False,
) -> None:
    if entry.get("store_state") not in {"pending", "writing", "unknown", "synced"}:
        raise _receipt_entry_error(receipt, node_id, "unknown store_state")
    if entry.get("store_state") == "pending" and not allow_pending:
        raise _receipt_entry_error(receipt, node_id, "persisted store_state is pending")
    if entry.get("manifest_state") not in {"pending", "unknown", "synced"}:
        raise _receipt_entry_error(receipt, node_id, "unknown manifest_state")
    if entry.get("work_item_id") != item_id:
        raise _receipt_entry_error(receipt, node_id, "work_item_id does not match manifest")
    if entry.get("approved_contract_sha256") != digest:
        raise _receipt_entry_error(receipt, node_id, "approved contract digest does not match")
    if not isinstance(entry.get("changes"), list):
        raise _receipt_entry_error(receipt, node_id, "changes must be a list")
    if not isinstance(entry.get("runtime_snapshot"), dict):
        raise _receipt_entry_error(receipt, node_id, "runtime_snapshot is missing")
    if set(entry["runtime_snapshot"]) != set(_ITEM_RUNTIME_FIELDS):
        raise _receipt_entry_error(receipt, node_id, "runtime_snapshot shape is invalid")
    if not isinstance(entry.get("manifest_snapshot"), dict):
        raise _receipt_entry_error(receipt, node_id, "manifest_snapshot is missing")
    if set(entry["manifest_snapshot"]) != set(_TOTAL_RUNTIME_FIELDS):
        raise _receipt_entry_error(receipt, node_id, "manifest_snapshot shape is invalid")
    if entry.get("baseline_sha256") != _receipt_baseline_digest(
            item_id, digest, entry):
        raise _receipt_entry_error(receipt, node_id, "baseline digest does not match")


def _observe_store_target(
    store: Any,
    runtime: Any,
    node_id: str,
    node: Any,
    approved: dict[str, Any],
    receipt: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    item_id = node.work_item_id
    if not item_id:
        raise ValidationError(f"Repair node {node_id} has no work_item_id")
    item = store.get_work_item(item_id)
    if str(getattr(item, "id", "")) != str(item_id):
        raise NeedsDecision(
            f"Contract repair Store returned the wrong WorkItem for {node_id}",
            report={
                "reason_code": "contract-repair-work-item-id-mismatch",
                "node_id": node_id,
                "expected_work_item_id": item_id,
                "observed_work_item_id": getattr(item, "id", None),
            },
        )
    runs = runtime.list_runs(item_id)
    unsafe = [run for run in runs if run.active or not run.terminal]
    if unsafe:
        raise NeedsDecision(
            f"Contract repair for {node_id} is blocked by active/unknown Runs",
            report={
                "reason_code": "contract-repair-run-not-terminal",
                "node_id": node_id,
                "work_item_id": item_id,
                "run_ids": [run.id for run in unsafe],
            },
        )
    if item.platform_assignee_id:
        raise NeedsDecision(
            f"Contract repair for {node_id} cannot preserve its platform assignment",
            report={
                "reason_code": "contract-repair-assignment-present",
                "node_id": node_id,
                "work_item_id": item_id,
            },
        )
    snapshot = _item_runtime_snapshot(item)
    entry = receipt["targets"][node_id]
    previous_snapshot = entry.get("runtime_snapshot")
    if previous_snapshot is not None and previous_snapshot != snapshot:
        raise _needs_decision(
            f"WorkItem runtime facts changed during contract repair for {node_id}",
            "contract-repair-store-cas-mismatch",
            _receipt_path(receipt["manifest_path"], receipt["repair_id"]),
        )
    previous_manifest = entry.get("manifest_snapshot")
    current_manifest = _node_runtime_snapshot(node)
    if previous_manifest is not None and previous_manifest != current_manifest:
        raise _needs_decision(
            f"Manifest runtime facts changed during contract repair for {node_id}",
            "contract-repair-manifest-cas-mismatch",
            _receipt_path(receipt["manifest_path"], receipt["repair_id"]),
        )
    entry["work_item_id"] = item_id
    entry.setdefault("runtime_snapshot", snapshot)
    entry.setdefault("manifest_snapshot", current_manifest)
    digest = _contract_digest(approved)
    entry["approved_contract_sha256"] = digest
    entry.setdefault("baseline_sha256", _receipt_baseline_digest(item_id, digest, entry))
    return item, snapshot


def _canonical_contract(contract: Any) -> Any:
    return (
        _dump_contract(_load_contract(contract))
        if isinstance(contract, dict) else _dump_contract(contract)
    )


def _store_contract_matches(item: Any, approved: dict[str, Any], expected_digest: str) -> bool:
    return (
        _canonical_contract(item.contract) == approved
        and isinstance(item.contract_ref, dict)
        and item.contract_ref.get("sha256") == expected_digest
    )


def _verify_store_runtime(store: Any, item_id: str, before: dict[str, Any], approved: dict[str, Any], digest: str) -> Any:
    current = store.get_work_item(item_id)
    if str(getattr(current, "id", "")) != str(item_id):
        raise PlatformError(
            f"Contract repair Store returned the wrong WorkItem for {item_id}")
    if _item_runtime_snapshot(current) != before:
        raise PlatformError(
            f"Contract repair changed runtime facts for work item {item_id}")
    if not _store_contract_matches(current, approved, digest):
        raise PlatformError(
            f"Contract repair contract/ref readback mismatch for work item {item_id}")
    return current


def _needs_decision(message: str, reason: str, receipt: Path) -> NeedsDecision:
    return _decision_report(message, reason, receipt)


def repair_contract_commands(
    engine: Any,
    manifest_path: str,
    amendment_file: str,
) -> dict[str, Any]:
    """Restore only known shell-placeholder command damage from an applied amendment."""
    try:
        amendment = parse_proposal(Path(amendment_file).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValidationError(f"Could not read approved amendment {amendment_file}: {exc}") from exc
    manifest = load_manifest(manifest_path)
    targets = _validate_approved_amendment(
        manifest, amendment, os.path.realpath(manifest_path))
    repair_id = _json_digest({
        "amendment_id": amendment["amendment_id"],
        "manifest": os.path.realpath(manifest_path),
        "targets": [
            (node_id, _contract_digest(approved))
            for node_id, approved, _changes in targets
        ],
    })[:24]
    receipt_path = _receipt_path(manifest_path, repair_id)
    receipt = _load_receipt(receipt_path)
    if receipt is None:
        receipt = {
            "schema": _REPAIR_SCHEMA,
            "repair_id": repair_id,
            "amendment_id": amendment["amendment_id"],
            "amendment_file": os.path.realpath(amendment_file),
            "manifest_path": os.path.realpath(manifest_path),
            "targets": {},
            "manifest_state": "pending",
        }
        _write_receipt(receipt_path, receipt)
    elif (
        receipt.get("repair_id") != repair_id
        or receipt.get("amendment_id") != amendment["amendment_id"]
        or receipt.get("manifest_path") != os.path.realpath(manifest_path)
        or not isinstance(receipt.get("targets"), dict)
    ):
        raise _needs_decision(
            "Contract repair receipt identity or shape conflicts with the requested source",
            "contract-repair-receipt-conflict", receipt_path)

    target_map = {node_id: (approved, changes) for node_id, approved, changes in targets}
    for node_id, (approved, changes) in target_map.items():
        digest = _contract_digest(approved)
        entry = receipt["targets"].get(node_id)
        new_entry = entry is None
        if new_entry:
            entry = receipt["targets"][node_id] = {
                "changes": changes,
                "store_state": "pending",
                "manifest_state": "pending",
            }
        elif not isinstance(entry, dict):
            raise _receipt_entry_error(receipt_path, node_id, "entry is not an object")
        else:
            _validate_receipt_entry(
                entry, node_id, manifest.nodes[node_id].work_item_id or "",
                digest, receipt_path)
        node = manifest.nodes[node_id]
        item, runtime_snapshot = _observe_store_target(
            engine.store, engine.runtime, node_id, node, approved, receipt)
        if new_entry:
            # _observe_store_target populated and bound all baseline fields.
            _validate_receipt_entry(
                entry, node_id, node.work_item_id or "", digest, receipt_path,
                allow_pending=True)
        if entry.get("store_state") == "synced":
            try:
                _verify_store_runtime(
                    engine.store, item.id, runtime_snapshot, approved, digest)
            except PlatformError as exc:
                entry["store_state"] = "unknown"
                entry["error"] = str(exc)
                _write_receipt(receipt_path, receipt)
                raise _needs_decision(
                    f"Contract repair readback is no longer safe for {node_id}",
                    "contract-repair-store-readback-mismatch", receipt_path) from exc
            continue
        if entry.get("store_state") in {"unknown", "writing"}:
            # A prior write may have published the attachment before its
            # response/ref update was lost. Observe, adopt only one exact
            # publication, and never blindly publish a second copy.
            if _store_contract_matches(item, approved, digest):
                entry["store_state"] = "synced"
                _write_receipt(receipt_path, receipt)
                continue
            publications = _find_contract_publications(
                engine.store, item.id, digest)
            if len(publications) == 1:
                try:
                    _sync_contract_ref(engine.store, item.id, publications[0])
                    _verify_store_runtime(
                        engine.store, item.id, runtime_snapshot, approved, digest)
                except PlatformError as exc:
                    entry["error"] = str(exc)
                else:
                    entry["store_state"] = "synced"
                    _write_receipt(receipt_path, receipt)
                    continue
            entry["store_state"] = "unknown"
            entry["error"] = (
                "multiple matching contract publications"
                if len(publications) > 1 else "no matching contract publication")
            _write_receipt(receipt_path, receipt)
            raise _needs_decision(
                f"Contract repair for {node_id} has an unknown Store write outcome",
                "contract-repair-store-outcome-unknown", receipt_path)
        if _store_contract_matches(item, approved, digest):
            entry["store_state"] = "synced"
            _write_receipt(receipt_path, receipt)
            continue
        publications = _find_contract_publications(
            engine.store, item.id, digest)
        if len(publications) == 1:
            try:
                _sync_contract_ref(engine.store, item.id, publications[0])
                _verify_store_runtime(
                    engine.store, item.id, runtime_snapshot, approved, digest)
            except PlatformError as exc:
                entry["store_state"] = "unknown"
                entry["error"] = str(exc)
                _write_receipt(receipt_path, receipt)
                raise _needs_decision(
                    f"Contract reference recovery for {node_id} is unknown",
                    "contract-repair-store-outcome-unknown", receipt_path) from exc
            entry["store_state"] = "synced"
            _write_receipt(receipt_path, receipt)
            continue
        if len(publications) > 1:
            entry["store_state"] = "unknown"
            entry["error"] = "multiple matching contract publications"
            _write_receipt(receipt_path, receipt)
            raise _needs_decision(
                f"Multiple matching contract publications exist for {node_id}",
                "contract-repair-publication-ambiguous", receipt_path)
        entry["store_state"] = "writing"
        _write_receipt(receipt_path, receipt)
        try:
            engine.store.set_node_contract(item.id, approved)
        except BaseException as exc:
            entry["store_state"] = "unknown"
            entry["error"] = str(exc)
            _write_receipt(receipt_path, receipt)
            # Observe once after a lost response; never blindly republish.
            current = engine.store.get_work_item(item.id)
            if str(getattr(current, "id", "")) != str(item.id):
                entry["error"] = "Store returned the wrong WorkItem after write"
                _write_receipt(receipt_path, receipt)
                raise _needs_decision(
                    f"Contract repair Store readback returned the wrong WorkItem for {node_id}",
                    "contract-repair-work-item-id-mismatch", receipt_path) from exc
            if _store_contract_matches(current, approved, digest):
                entry["store_state"] = "synced"
                _write_receipt(receipt_path, receipt)
                continue
            publications = _find_contract_publications(
                engine.store, item.id, digest)
            if len(publications) == 1:
                try:
                    _sync_contract_ref(engine.store, item.id, publications[0])
                    _verify_store_runtime(
                        engine.store, item.id, runtime_snapshot, approved, digest)
                except PlatformError as readback_exc:
                    entry["error"] = str(readback_exc)
                else:
                    entry["store_state"] = "synced"
                    _write_receipt(receipt_path, receipt)
                    continue
            _write_receipt(receipt_path, receipt)
            raise _needs_decision(
                f"Contract repair Store write outcome is unknown for {node_id}",
                "contract-repair-store-outcome-unknown", receipt_path) from exc
        try:
            _verify_store_runtime(
                engine.store, item.id, runtime_snapshot, approved, digest)
        except PlatformError as exc:
            entry["store_state"] = "unknown"
            entry["error"] = str(exc)
            _write_receipt(receipt_path, receipt)
            raise _needs_decision(
                f"Contract repair Store readback failed for {node_id}",
                "contract-repair-store-readback-mismatch", receipt_path) from exc
        entry["store_state"] = "synced"
        _write_receipt(receipt_path, receipt)

    # Re-read the durable receipt before the manifest side effect.  This
    # catches a concurrent edit/deletion and never trusts an in-memory state
    # after the Store boundary.
    persisted_receipt = _load_receipt(receipt_path)
    if (
        persisted_receipt is None
        or persisted_receipt.get("repair_id") != repair_id
        or persisted_receipt.get("amendment_id") != amendment["amendment_id"]
        or persisted_receipt.get("manifest_path") != os.path.realpath(manifest_path)
        or not isinstance(persisted_receipt.get("targets"), dict)
    ):
        raise _needs_decision(
            "Contract repair receipt changed before manifest write",
            "contract-repair-receipt-conflict", receipt_path)
    receipt = persisted_receipt
    for node_id, (approved, _changes) in target_map.items():
        entry = receipt["targets"].get(node_id)
        if not isinstance(entry, dict):
            raise _receipt_entry_error(receipt_path, node_id, "entry is not an object")
        node = manifest.nodes[node_id]
        _validate_receipt_entry(
            entry, node_id, node.work_item_id or "", _contract_digest(approved),
            receipt_path)
        if entry["store_state"] != "synced":
            raise _needs_decision(
                f"Contract repair Store state is not synced for {node_id}",
                "contract-repair-store-outcome-unknown", receipt_path)

    current_manifest = load_manifest(manifest_path)
    for node_id, (approved, changes) in target_map.items():
        entry = receipt["targets"][node_id]
        node = current_manifest.nodes[node_id]
        expected_runtime = entry.get("manifest_snapshot")
        if expected_runtime and _node_runtime_snapshot(node) != expected_runtime:
            entry["manifest_state"] = "unknown"
            entry["error"] = "manifest runtime facts changed"
            _write_receipt(receipt_path, receipt)
            raise _needs_decision(
                f"Manifest runtime facts changed during contract repair for {node_id}",
                "contract-repair-manifest-cas-mismatch", receipt_path)
        current_contract = _dump_contract(node.contract) if node.contract else {}
        if entry.get("manifest_state") == "synced":
            if current_contract != approved:
                raise _needs_decision(
                    f"Manifest contract readback failed for {node_id}",
                    "contract-repair-manifest-readback-mismatch", receipt_path)
            continue
        if current_contract == approved:
            entry["manifest_state"] = "synced"
            _write_receipt(receipt_path, receipt)
            continue
        # Revalidate the exact known-damage diff before touching the manifest.
        _repairable_command_changes(current_contract, approved)
        node.contract = _load_contract(
            _apply_commands(current_contract, changes))
        save_manifest(current_manifest, manifest_path)
        current_manifest = load_manifest(manifest_path)
        if _dump_contract(current_manifest.nodes[node_id].contract) != approved:
            entry["manifest_state"] = "unknown"
            _write_receipt(receipt_path, receipt)
            raise _needs_decision(
                f"Manifest command repair readback failed for {node_id}",
                "contract-repair-manifest-readback-mismatch", receipt_path)
        if expected_runtime and _node_runtime_snapshot(
                current_manifest.nodes[node_id]) != expected_runtime:
            entry["manifest_state"] = "unknown"
            _write_receipt(receipt_path, receipt)
            raise _needs_decision(
                f"Manifest runtime readback changed for {node_id}",
                "contract-repair-manifest-cas-mismatch", receipt_path)
        entry["manifest_state"] = "synced"
        _write_receipt(receipt_path, receipt)

    receipt["manifest_state"] = "synced"
    receipt["state"] = "synced"
    _write_receipt(receipt_path, receipt)
    return {
        "state": "synced",
        "repair_id": repair_id,
        "receipt": str(receipt_path),
        "amendment_id": amendment["amendment_id"],
        "nodes": sorted(target_map),
    }
