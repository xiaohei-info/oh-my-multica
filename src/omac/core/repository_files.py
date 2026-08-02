"""Repository-backed file enumeration for reproducible source snapshots."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def revision_directory_files(
    directory: Path, *, repository_root: Path,
) -> list[Path]:
    """Return files owned by the current Git revision, or all files outside Git.

    Remote Agents clone a revision, so local untracked or ignored files cannot be
    part of an authoritative directory snapshot. Non-Git callers retain the
    historical recursive behavior used by local and mock workflows.
    """
    directory = directory.resolve(strict=True)
    repository_root = repository_root.resolve(strict=True)
    try:
        top_level = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
        git_root = Path(top_level.stdout.strip()).resolve(strict=True)
        pathspec = directory.relative_to(git_root).as_posix()
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--", pathspec],
            cwd=git_root,
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError, OSError):
        return sorted(
            candidate for candidate in directory.rglob("*")
            if candidate.is_file())

    paths = [
        git_root / os.fsdecode(entry)
        for entry in result.stdout.split(b"\0")
        if entry
    ]
    return sorted(paths, key=lambda path: path.as_posix())
