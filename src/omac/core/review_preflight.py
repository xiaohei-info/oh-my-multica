"""Deterministic checks that run before an inferential reviewer is dispatched."""
from __future__ import annotations

import shlex
import subprocess
from typing import Any

from .contract_boundaries import manifest_boundary_errors
from .manifest import loads_manifest
from .taskmeta import TaskKind


_SHELL_OPERATORS = {"&&", "||", ";", "|"}
_GO_COMMANDS = {"build", "list", "run", "test"}
_GO_LOCAL_PREFIXES = ("cmd/", "internal/", "pkg/")


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


def _manifest_preflight(text: str) -> list[str]:
    try:
        manifest = loads_manifest(text)
    except Exception as exc:
        return [f"review preflight could not parse manifest: {exc}"]

    errors: list[str] = manifest_boundary_errors(manifest)
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


def run_review_preflight(item: Any) -> list[str]:
    """Run safe deterministic checks for the current review target."""
    kind = getattr(item, "kind", None)
    if getattr(kind, "value", kind) != TaskKind.DECOMPOSE.value:
        return []
    deliverable = getattr(item, "deliverable", None)
    if not isinstance(deliverable, str) or not deliverable.strip():
        return ["review preflight requires a non-empty manifest deliverable"]
    return _manifest_preflight(deliverable)
