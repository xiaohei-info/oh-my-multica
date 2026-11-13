"""Path references that end up in committed content (manifest, state files, commit messages).

Persisted content is shared across machines and may be published, so a path in it is
repo-relative (POSIX), or a bare file name when the file lives outside the repository.
Absolute paths written by older versions are still readable (``resolve_path``).
"""
from __future__ import annotations

import os
from pathlib import Path


def project_root(manifest_path: str) -> Path:
    """The repository root a manifest belongs to: the parent of ``.omac/``, else the manifest's directory."""
    parent = Path(os.path.realpath(manifest_path)).parent
    return parent.parent if parent.name == ".omac" else parent


def relative_to_root(path: str, root: Path | str) -> str:
    """``path`` relative to ``root`` (POSIX); a bare file name when it is outside ``root``."""
    resolved = Path(os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path)))
    try:
        return resolved.relative_to(os.path.realpath(root)).as_posix()
    except ValueError:
        return resolved.name


def persist_path(path: str, manifest_path: str) -> str:
    """The form of ``path`` that may be written into the manifest, state files or commit messages."""
    return relative_to_root(path, project_root(manifest_path))


def resolve_path(value: str, manifest_path: str) -> str:
    """Open a persisted path: repo-relative against the project root; legacy absolute paths as recorded.

    A legacy absolute path that no longer exists (another machine) falls back to the same relative
    location, then to its file name next to the manifest.
    """
    if not value:
        return value
    root = project_root(manifest_path)
    if not os.path.isabs(value):
        return str(root / value)
    if os.path.exists(value):
        return value
    for candidate in (root / relative_to_root(value, "/"), Path(os.path.realpath(manifest_path)).parent / Path(value).name):
        if candidate.exists():
            return str(candidate)
    return value
