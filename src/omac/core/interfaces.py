"""Interface chain (3.0): what a node provides, what its consumers require, and who must be guarded.

Three id-based checks the model cannot talk its way around:

* ``consumes[].requires`` ⊆ the ``provides`` ids of the producer's artifact (a gap is a lint error, not a review nit);
* every node that ``registers`` into a ``meta.registries`` entry is upstream of that registry's guard node;
* ``registers`` / ``requirement_refs`` / ``defect_refs`` are well-formed lists.

Every error carries a YAML snippet that can be pasted into the manifest. The module also owns the review-evidence
locator format used by the interface-chain review obligations (``core/review_convergence.py``).
"""
from __future__ import annotations

import json
import re
from typing import Any

from .acceptance_responsibility import dependency_closure
from .manifest import ConsumedArtifact, ProducedArtifact, Provided

# A repository path with a file extension (``src/api/packs.py:build``) or a test name (``test_pack_views``, ``TestPack``).
_LOCATOR = re.compile(
    r"(?:[\w.-]+/)*[\w-]+\.[A-Za-z][A-Za-z0-9]{0,7}\b|\btest_\w+|\b\w+_test\b|\bTest[A-Z]\w*")

REGISTRIES_KEY = "registries"


def has_evidence_locator(text: Any) -> bool:
    """Whether review evidence names a repository path with an extension or a test (the form is checked, not the target)."""
    return isinstance(text, str) and _LOCATOR.search(text) is not None


def _non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _get(value: Any, name: str, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def provided_items(contract: Any) -> list[tuple[str, str]]:
    """Sorted unique ``(artifact_id, provided id)`` pairs a contract (model or plain mapping) declares."""
    found = set()
    produces = _get(contract, "produces", [])
    for artifact in produces if isinstance(produces, (list, tuple)) else []:
        provides = _get(artifact, "provides", ())
        for item in provides if isinstance(provides, (list, tuple)) else []:
            artifact_id, item_id = _get(artifact, "artifact_id"), _get(item, "id")
            if _non_empty(artifact_id) and _non_empty(item_id):
                found.add((artifact_id, item_id))
    return sorted(found)


def registrations(contract: Any) -> list[str]:
    """Sorted unique registry ids a contract (model or plain mapping) registers into."""
    values = _get(contract, "registers", [])
    return sorted({value for value in values if _non_empty(value)}) if isinstance(values, list) else []


def _ref(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _list_errors(prefix: str, name: str, value: Any) -> list[str]:
    if not isinstance(value, list):
        return [f"{prefix}.{name} must be a list of non-empty strings, e.g. `{name}: [id-1]`"]
    errors = [f"{prefix}.{name}[{i}] must be a non-empty string" for i, item in enumerate(value) if not _non_empty(item)]
    texts = [item for item in value if _non_empty(item)]
    if len(texts) != len(set(texts)):
        errors.append(f"{prefix}.{name} must not contain duplicates")
    return errors


def _produced_errors(prefix: str, node: Any) -> tuple[list[str], dict[str, set[str]]]:
    errors: list[str] = []
    provided: dict[str, set[str]] = {}
    produces = _get(node.contract, "produces", [])
    for index, artifact in enumerate(produces if isinstance(produces, list) else []):
        if not isinstance(artifact, ProducedArtifact):
            continue
        provides = artifact.provides
        where = f"{prefix}.produces[{index}].provides"
        if not isinstance(provides, tuple):
            errors.append(f"{where} must be a list of objects, e.g. `provides: [{{id: \"http:GET /v1/items\", ref: \"docs/design.md#3\"}}]`")
            continue
        ids = []
        for position, item in enumerate(provides):
            if not (isinstance(item, Provided) and _non_empty(item.id) and (item.ref is None or _non_empty(item.ref))):
                errors.append(f"{where}[{position}] must be an object with a non-empty id and an optional ref string")
                continue
            ids.append(item.id)
        if len(ids) != len(set(ids)):
            errors.append(f"{where} must not contain duplicate id values")
        if _non_empty(artifact.artifact_id):
            provided.setdefault(artifact.artifact_id, set()).update(ids)
    return errors, provided


def _missing_snippet(node_id: str, index: int, requirement: str, producer: str, artifact_id: str) -> str:
    return (
        f"node {node_id}: contract.consumes[{index}] requires {requirement!r} but node {producer} does not provide it "
        f"for artifact {artifact_id!r}. Declare it on the producer (or drop it from `requires`):\n"
        f"  - id: {producer}\n"
        f"    contract:\n"
        f"      produces:\n"
        f"        - artifact_id: {artifact_id}\n"
        f"          provides:\n"
        f"            - {{id: {_ref(requirement)}, ref: \"<design anchor, e.g. docs/design.md#section>\"}}")


def _requires_errors(node: Any, prefix: str, provided: dict[str, dict[str, set[str]]]) -> list[str]:
    consumes = _get(node.contract, "consumes", None)
    errors: list[str] = []
    for index, consumed in enumerate(consumes if isinstance(consumes, list) else []):
        if not isinstance(consumed, ConsumedArtifact) or not consumed.requires:
            continue
        where = f"{prefix}.consumes[{index}]"
        if not isinstance(consumed.requires, tuple):
            errors.append(f"{where}.requires must be a list of provided ids, e.g. `requires: [\"http:GET /v1/items\"]`")
            continue
        errors.extend(_list_errors(where, "requires", list(consumed.requires)))
        offered = provided.get(consumed.producer, {}).get(consumed.artifact_id)
        if offered is None:      # unknown producer or artifact: manifest_boundary_errors already says how to fix it
            continue
        errors.extend(
            _missing_snippet(node.id, index, requirement, consumed.producer, consumed.artifact_id)
            for requirement in consumed.requires if _non_empty(requirement) and requirement not in offered)
    return errors


def _registry_entries(manifest: Any) -> tuple[dict[str, dict], list[str]]:
    raw = manifest.meta.get(REGISTRIES_KEY)
    if raw is None:
        return {}, []
    if not isinstance(raw, list):
        return {}, [f"meta.{REGISTRIES_KEY} must be a list of {{id, ref, guard}} objects"]
    entries: dict[str, dict] = {}
    errors: list[str] = []
    for index, entry in enumerate(raw):
        where = f"meta.{REGISTRIES_KEY}[{index}]"
        if not (isinstance(entry, dict) and _non_empty(entry.get("id")) and _non_empty(entry.get("guard"))):
            errors.append(f"{where} must be an object with a non-empty id and guard (the node that tests the whole registry)")
            continue
        if entry["id"] in entries:
            errors.append(f"{where}.id {entry['id']!r} is declared twice")
            continue
        if entry["guard"] not in manifest.nodes:
            errors.append(f"{where}.guard {entry['guard']!r} is not a node in the manifest; name the node that guards {entry['id']!r}")
            continue
        entries[entry["id"]] = entry
    return entries, errors


def _guard_position_error(manifest: Any, registry: dict, registrant: str) -> str | None:
    guard = manifest.nodes[registry["guard"]]
    if registrant in dependency_closure(manifest, guard.id):
        return None
    if guard.status == "done":
        return (
            f"registry {registry['id']!r}: node {registrant} registers into it but guard {guard.id} is already done and "
            f"cannot gain upstream nodes. Add a new guard node that runs after {registrant}:\n"
            f"  - id: {guard.id}-after-{registrant}\n"
            f"    worker: <worker>\n"
            f"    blocked_by: [{', '.join([*guard.blocked_by, registrant])}]\n"
            f"and point the registry at it: meta.registries[id={registry['id']}].guard: {guard.id}-after-{registrant}")
    return (
        f"registry {registry['id']!r}: guard {guard.id} must run after every node that registers into it, "
        f"but {registrant} is not upstream of it. Edit node {guard.id}:\n"
        f"    blocked_by: [{', '.join([*guard.blocked_by, registrant])}]")


def _registry_errors(manifest: Any, selected: set[str]) -> list[str]:
    registries, errors = _registry_entries(manifest)
    for node in manifest.nodes.values():
        if node.id not in selected or node.contract is None:
            continue
        prefix = f"node {node.id}: contract"
        for name in ("registers", "requirement_refs", "defect_refs"):
            value = getattr(node.contract, name)
            if value != []:
                errors.extend(_list_errors(prefix, name, value))
    for node in manifest.nodes.values():
        values = getattr(node.contract, "registers", None) if node.contract is not None else None
        for registry_id in values if isinstance(values, list) else []:
            if not _non_empty(registry_id):
                continue
            registry = registries.get(registry_id)
            if registry is None:
                if node.id in selected:
                    errors.append(
                        f"node {node.id}: contract.registers names unknown registry {registry_id!r}. Declare it in the manifest:\n"
                        f"  meta:\n    registries:\n      - {{id: {registry_id}, ref: \"<design anchor>\", guard: <guard-node-id>}}")
                continue
            if registry["guard"] == node.id:
                errors.append(f"registry {registry_id!r}: guard {node.id} must not register into the registry it guards")
            elif node.id in selected or registry["guard"] in selected:
                position = _guard_position_error(manifest, registry, node.id)
                if position:
                    errors.append(position)
    return errors


def interface_errors(manifest: Any, *, node_ids: set[str] | None = None) -> list[str]:
    """Interface-chain errors of ``manifest`` (all nodes, or only ``node_ids`` and what they are checked against)."""
    selected = set(manifest.nodes) if node_ids is None else set(node_ids)
    errors: list[str] = []
    provided: dict[str, dict[str, set[str]]] = {}
    for node in manifest.nodes.values():
        if node.contract is None:
            continue
        shape, found = _produced_errors(f"node {node.id}: contract", node)
        provided[node.id] = found
        if node.id in selected:
            errors.extend(shape)
    for node in manifest.nodes.values():
        if node.id in selected and node.contract is not None:
            errors.extend(_requires_errors(node, f"node {node.id}: contract", provided))
    errors.extend(_registry_errors(manifest, selected))
    return errors
