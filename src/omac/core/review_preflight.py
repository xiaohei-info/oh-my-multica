"""Deterministic checks that run before an inferential reviewer is dispatched."""
from __future__ import annotations

import shlex
import subprocess
from typing import Any

from .acceptance_responsibility import (
    contributions, dependency_closure, full_claims, matrix_errors, trace_refs,
)
from .config import DEFAULT_PLAN
from .contract_boundaries import manifest_boundary_errors
from .manifest import ConsumedArtifact, EvidenceMode, MISSING_CONSUMES, loads_manifest
from .requirements import technical_claim_errors
from .taskmeta import TaskKind


_SHELL_OPERATORS = {"&&", "||", ";", "|"}
_GO_COMMANDS = {"build", "list", "run", "test"}
_GO_LOCAL_PREFIXES = ("cmd/", "internal/", "pkg/")
_GENERIC_DESCRIPTION_MARKERS = (
    "smallest independently pr-able",
    "smallest independently pr able",
)


def _node_commands(node: Any) -> list[str]:
    contract = getattr(node, "contract", None)
    if contract is None:
        return []
    candidates = list(getattr(contract, "verification_commands", None) or [])
    for gate in getattr(contract, "integration_gates", None) or []:
        if isinstance(gate, dict):
            candidates.extend(gate.get("commands", []) or [])
    commands = []
    seen = set()
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        commands.append(candidate)
    return commands


def _shell_syntax_error(command: str) -> str | None:
    result = subprocess.run(
        ["bash", "-n", "-c", command], capture_output=True, text=True)
    if result.returncode == 0:
        return None
    return (result.stderr or result.stdout or "invalid shell syntax").strip()


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return []


def _go_target_errors(node_id: str, tokens: list[str]) -> list[str]:
    errors = []
    for index, token in enumerate(tokens[:-1]):
        if token != "go" or tokens[index + 1] not in _GO_COMMANDS:
            continue
        for target in tokens[index + 2:]:
            if target in _SHELL_OPERATORS:
                break
            if target.startswith("-"):
                continue
            if target.startswith(_GO_LOCAL_PREFIXES):
                errors.append(
                    f"node {node_id}: Go local package target must start with ./ or ../: {target}")
    return errors


def _command_preflight(manifest: Any) -> list[str]:
    errors: list[str] = []
    for node_id, node in manifest.nodes.items():
        for command in _node_commands(node):
            syntax_error = _shell_syntax_error(command)
            if syntax_error:
                errors.append(
                    f"node {node_id}: shell syntax is invalid: {syntax_error}")
                continue
            tokens = _tokens(command)
            errors.extend(_go_target_errors(node_id, tokens))
    return errors


def _manifest_preflight(text: str) -> list[str]:
    try:
        manifest = loads_manifest(text)
    except Exception as exc:
        return [f"review preflight could not parse manifest: {exc}"]

    errors: list[str] = manifest_boundary_errors(manifest)
    errors.extend(_command_preflight(manifest))
    return errors


SCALE_LIGHT, SCALE_STANDARD = "light", "standard"


def plan_scale(
    manifest: Any, acceptance_doc: Any = None, *, profile: str = "auto",
    light_max_nodes: int = DEFAULT_PLAN["light_max_nodes"],
    light_max_business_actions: int = DEFAULT_PLAN["light_max_business_actions"],
) -> str:
    """light or standard, decided by code from the plan's size. ``profile`` is the operator's override (``plan.profile``)."""
    if profile in (SCALE_LIGHT, SCALE_STANDARD):
        return profile
    actions = acceptance_doc.business_action_count if acceptance_doc is not None else 0
    small = len(manifest.nodes) <= light_max_nodes and actions <= light_max_business_actions
    return SCALE_LIGHT if small else SCALE_STANDARD


def _consumes_requires_errors(node: Any, producers: dict[str, set[str]]) -> list[str]:
    errors = []
    consumes = getattr(node.contract, "consumes", None)
    for index, consumed in enumerate(consumes if isinstance(consumes, list) else []):
        if not isinstance(consumed, ConsumedArtifact):
            continue
        if not consumed.requires:
            errors.append(
                f"node {node.id}: contract.consumes[{index}] ({consumed.artifact_id} from {consumed.producer}) must "
                f"declare `requires`, the provided ids this node needs (standard plan). Add "
                f"`requires: [<id>]` and declare each id under {consumed.producer}'s "
                f"`produces[artifact_id={consumed.artifact_id}].provides`")
            continue
        for requirement in consumed.requires:
            if requirement in producers.get((consumed.producer, consumed.artifact_id), ()):
                errors.append(
                    f"node {consumed.producer}: provides {requirement!r} of {consumed.artifact_id} needs a design "
                    f"`ref` (standard plan), e.g. {{id: {requirement!r}, ref: \"docs/design.md#section\"}}")
    return errors


def _standard_errors(manifest: Any) -> list[str]:
    """What a standard-scale plan adds: every consumed interface is named, and one node closes the whole graph."""
    unanchored: dict[tuple[str, str], set[str]] = {}
    for node in manifest.nodes.values():
        for artifact in getattr(node.contract, "produces", None) or []:
            for item in getattr(artifact, "provides", ()) or ():
                if getattr(item, "ref", None) is None or not str(item.ref).strip():
                    unanchored.setdefault((node.id, artifact.artifact_id), set()).add(item.id)
    errors = []
    for node in manifest.nodes.values():
        if node.contract is not None:
            errors.extend(dict.fromkeys(_consumes_requires_errors(node, unanchored)))
    closeout = manifest.meta.get("closeout_node")
    if closeout not in manifest.nodes:
        errors.append(
            "standard plan: meta.closeout_node must name the node that closes the whole graph (it depends, "
            "directly or not, on every other node), e.g. `meta: {closeout_node: closeout}`")
        return errors
    missing = sorted(set(manifest.nodes) - {closeout} - dependency_closure(manifest, closeout))
    if missing:
        errors.append(
            f"standard plan: closeout node {closeout} must be the sink of the whole graph, but it does not depend on "
            f"{', '.join(missing)}. Edit node {closeout}: blocked_by: "
            f"[{', '.join(sorted({*manifest.nodes[closeout].blocked_by, *missing}))}]")
    return errors


def plan_manifest_preflight(manifest: Any, *, acceptance_doc: Any = None, scale: str | None = None) -> list[str]:
    """Validate greenfield decomposition boundaries before Reviewer dispatch.

    Existing manifests remain readable through :func:`run_review_preflight`.
    The plan pipeline opts into this stricter gate so a new DAG cannot silently
    publish anonymous, multi-scope, or untyped nodes.  Checks are deliberately
    structural; semantic completeness still belongs to the independent review.

    ``scale`` is the plan's profile (:func:`plan_scale`; default: decided from this manifest and acceptance document).
    light adds nothing to the checks below; standard also requires named interfaces and a closeout sink.
    """
    errors: list[str] = []
    scope_owners: dict[str, list[str]] = {}
    valid_modes = {mode.value for mode in EvidenceMode}

    nodes = getattr(manifest, "nodes", None)
    if not isinstance(nodes, dict) or not nodes:
        return ["plan preflight requires at least one manifest node"]

    for node_id, node in nodes.items():
        prefix = f"node {node_id}"
        owner = getattr(node, "worker", None)
        if not isinstance(owner, str) or not owner.strip():
            errors.append(f"{prefix}: owner (worker) must be a non-empty string")

        description = getattr(node, "description", None)
        if not isinstance(description, str) or not description.strip():
            errors.append(f"{prefix}: description must state the node-specific outcome")
        else:
            normalized = " ".join(description.lower().split())
            if any(marker in normalized for marker in _GENERIC_DESCRIPTION_MARKERS):
                errors.append(
                    f"{prefix}: description must state a node-specific outcome; "
                    "do not use the generic smallest independently PR-able template"
                )

        contract = getattr(node, "contract", None)
        if contract is None:
            errors.append(f"{prefix}: contract is required for plan preflight")
            continue

        scope_paths = getattr(contract, "scope_paths", None)
        if not isinstance(scope_paths, list):
            errors.append(f"{prefix}: contract.scope_paths must be a list with one primary owner path")
        else:
            paths = [
                value.strip() for value in scope_paths
                if isinstance(value, str) and value.strip()
            ]
            if len(paths) != len(scope_paths):
                errors.append(
                    f"{prefix}: contract.scope_paths entries must be non-empty strings"
                )
            if len(paths) != 1:
                errors.append(
                    f"{prefix}: contract.scope_paths must declare exactly one primary owner path"
                )
            for path in paths:
                scope_owners.setdefault(path, []).append(str(node_id))

        mode = getattr(contract, "evidence_mode", None)
        mode_value = getattr(mode, "value", mode)
        if mode_value not in valid_modes:
            errors.append(
                f"{prefix}: contract.evidence_mode must be fixture|artifact|live"
            )

        produces = getattr(contract, "produces", None)
        if not isinstance(produces, list):
            errors.append(f"{prefix}: contract.produces must be a list")
        consumes = getattr(contract, "consumes", MISSING_CONSUMES)
        if consumes is MISSING_CONSUMES:
            errors.append(
                f"{prefix}: contract.consumes must be explicit [] or a typed allowlist"
            )
        elif not isinstance(consumes, list):
            errors.append(f"{prefix}: contract.consumes must be a list")

        if not (
            full_claims(contract) or contributions(contract) or trace_refs(contract)
        ):
            errors.append(
                f"{prefix}: acceptance responsibility (claims, contributions, or refs) is required"
            )

    for scope_path, owners in sorted(scope_owners.items()):
        if len(owners) > 1:
            errors.append(
                "plan preflight scope ownership conflict: "
                f"{scope_path} is claimed by {', '.join(sorted(owners))}"
            )

    errors.extend(_command_preflight(manifest))

    # The negative matrix is the set of conflicts that can be proven without
    # repository-specific semantics: duplicate scope ownership, duplicate typed
    # producers, and acceptance responsibility gaps.  Keep all findings so one
    # authoring round can repair the whole plan instead of discovering them one
    # Reviewer cycle at a time.
    errors.extend(manifest_boundary_errors(manifest))
    if acceptance_doc is not None:
        errors.extend(matrix_errors(manifest, acceptance_doc))
        errors.extend(technical_claim_errors(manifest, acceptance_doc, require_claimed=True))
    if (scale or plan_scale(manifest, acceptance_doc)) == SCALE_STANDARD:
        errors.extend(_standard_errors(manifest))
    return list(dict.fromkeys(errors))


def run_plan_preflight(text: str, *, acceptance_doc: Any = None, scale: str | None = None) -> list[str]:
    """Parse and run the strict preflight used by a new plan decomposition."""
    try:
        manifest = loads_manifest(text)
    except Exception as exc:
        return [f"plan preflight could not parse manifest: {exc}"]
    return plan_manifest_preflight(manifest, acceptance_doc=acceptance_doc, scale=scale)


def run_manifest_preflight(text: str) -> list[str]:
    """Run the compatibility preflight for an already-written manifest."""
    return _manifest_preflight(text)


def run_review_preflight(item: Any) -> list[str]:
    """Run safe deterministic checks for the current review target."""
    kind = getattr(item, "kind", None)
    if getattr(kind, "value", kind) != TaskKind.DECOMPOSE.value:
        return []
    deliverable = getattr(item, "deliverable", None)
    if not isinstance(deliverable, str) or not deliverable.strip():
        return ["review preflight requires a non-empty manifest deliverable"]
    return _manifest_preflight(deliverable)
