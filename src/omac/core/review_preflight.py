"""Deterministic checks that run before an inferential reviewer is dispatched."""
from __future__ import annotations

from fnmatch import fnmatch
import os
import shlex
import subprocess
from typing import Any

from .manifest import loads_manifest
from .taskmeta import TaskKind


_OUTPUT_FLAGS = {
    "-o", "--output", "--output-file", "--bundle",
    "--component-manifest", "--digest-output", "--report", "--report-file",
}
_INPUT_FLAGS = {
    "--input", "--input-file", "--manifest", "--config", "--schema",
}
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


def _flag_values(tokens: list[str], flags: set[str]) -> list[str]:
    values = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        matched = False
        for flag in flags:
            if token == flag and index + 1 < len(tokens):
                values.append(tokens[index + 1])
                index += 2
                matched = True
                break
            prefix = f"{flag}="
            if token.startswith(prefix):
                values.append(token[len(prefix):])
                index += 1
                matched = True
                break
        if not matched:
            index += 1
    return [value for value in values if value]


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


def _scope_owns(path: str, scope_paths: list[str]) -> bool:
    normalized = path.removeprefix("./")
    for pattern in scope_paths:
        if not isinstance(pattern, str):
            continue
        if fnmatch(normalized, pattern):
            return True
        if pattern.endswith("/**") and normalized.startswith(pattern[:-3].rstrip("/") + "/"):
            return True
    return False


def _external_or_runtime_path(path: str) -> bool:
    return (
        not path
        or path == "-"
        or path.startswith(("/", "${", "$", "http://", "https://"))
        or "://" in path
    )


def _ancestors(node_id: str, nodes: dict[str, Any]) -> set[str]:
    found = set()
    stack = list(getattr(nodes[node_id], "blocked_by", None) or [])
    while stack:
        current = stack.pop()
        if current in found or current not in nodes:
            continue
        found.add(current)
        stack.extend(getattr(nodes[current], "blocked_by", None) or [])
    return found


def _manifest_preflight(text: str) -> list[str]:
    try:
        manifest = loads_manifest(text)
    except Exception as exc:
        return [f"review preflight could not parse manifest: {exc}"]

    errors: list[str] = []
    command_data: dict[str, list[tuple[int, str, list[str]]]] = {}
    outputs: dict[str, list[tuple[str, int]]] = {}
    for node_id, node in manifest.nodes.items():
        entries = []
        for command_index, command in enumerate(_node_commands(node), start=1):
            syntax_error = _shell_syntax_error(command)
            if syntax_error:
                errors.append(
                    f"node {node_id}: shell syntax is invalid: {syntax_error}")
                continue
            tokens = _tokens(command)
            entries.append((command_index, command, tokens))
            errors.extend(_go_target_errors(node_id, tokens))
            for path in _flag_values(tokens, _OUTPUT_FLAGS):
                if not _external_or_runtime_path(path):
                    outputs.setdefault(path.removeprefix("./"), []).append(
                        (node_id, command_index))
        command_data[node_id] = entries

    for path, producers in sorted(outputs.items()):
        unique = list(dict.fromkeys(producers))
        if len(unique) > 1:
            locations = ", ".join(
                f"{node_id} command {command_index}"
                for node_id, command_index in unique)
            errors.append(
                f"artifact output path has multiple producers: {path} ({locations})")

    for node_id, entries in command_data.items():
        node = manifest.nodes[node_id]
        scope_paths = list(getattr(getattr(node, "contract", None), "scope_paths", None) or [])
        reachable = _ancestors(node_id, manifest.nodes)
        for _, _, tokens in entries:
            for raw_path in _flag_values(tokens, _INPUT_FLAGS):
                if _external_or_runtime_path(raw_path):
                    continue
                path = raw_path.removeprefix("./")
                if os.path.exists(path) or _scope_owns(path, scope_paths):
                    continue
                producers = outputs.get(path, [])
                if any(producer in reachable for producer, _ in producers):
                    continue
                errors.append(
                    f"node {node_id}: command input has no reachable producer or owned scope: {path}")
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
