"""Repository-backed file enumeration for reproducible source snapshots."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..errors import ValidationError


@dataclass(frozen=True)
class RevisionFile:
    """One immutable file from an authoritative repository revision."""

    logical_path: str
    content: bytes


def revision_directory_files(
    directory: Path, *, repository_root: Path,
) -> list[RevisionFile]:
    """Read a directory from HEAD, falling back to the filesystem outside Git."""
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
        subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        return _filesystem_directory_files(
            directory, repository_root=repository_root)

    git_root = Path(top_level.stdout.strip()).resolve(strict=True)
    try:
        pathspec = directory.relative_to(git_root).as_posix()
    except ValueError as exc:
        raise ValidationError(
            f"Authoritative directory is outside the Git repository: {directory}") from exc
    try:
        result = subprocess.run(
            ["git", "--literal-pathspecs", "ls-tree", "-rz", "HEAD", "--", pathspec],
            cwd=git_root,
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, OSError) as exc:
        raise ValidationError(
            f"Could not enumerate authoritative Git directory {directory}: {exc}") from exc

    files: list[RevisionFile] = []
    for raw_entry in result.stdout.split(b"\0"):
        if not raw_entry:
            continue
        header, separator, raw_path = raw_entry.partition(b"\t")
        fields = header.split(b" ")
        if not separator or len(fields) != 3:
            raise ValidationError("Git returned an invalid authoritative file entry")
        mode, object_type, object_id = fields
        try:
            git_path = raw_path.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(
                "Authoritative Git paths must be valid UTF-8") from exc
        candidate = git_root / git_path
        try:
            candidate.relative_to(directory)
            logical_path = candidate.relative_to(repository_root).as_posix()
        except ValueError as exc:
            raise ValidationError(
                f"Git returned a file outside the authoritative directory: {git_path}") from exc
        if mode == b"120000":
            raise ValidationError(
                f"Authoritative docs input contains a symlink: {logical_path}")
        if object_type != b"blob":
            raise ValidationError(
                f"Authoritative docs input contains a non-regular Git entry: {logical_path}")
        try:
            content = subprocess.run(
                ["git", "cat-file", "blob", object_id.decode("ascii")],
                cwd=git_root,
                check=True,
                capture_output=True,
            ).stdout
        except (UnicodeDecodeError, FileNotFoundError, subprocess.CalledProcessError, OSError) as exc:
            raise ValidationError(
                f"Could not read authoritative Git blob for {logical_path}: {exc}") from exc
        files.append(RevisionFile(logical_path=logical_path, content=content))
    return sorted(files, key=lambda entry: entry.logical_path)


def _filesystem_directory_files(
    directory: Path, *, repository_root: Path,
) -> list[RevisionFile]:
    files: list[RevisionFile] = []
    try:
        descendants = sorted(directory.rglob("*"), key=lambda path: path.as_posix())
    except OSError as exc:
        raise ValidationError(
            f"Could not enumerate authoritative directory {directory}: {exc}") from exc
    for candidate in descendants:
        if candidate.is_symlink():
            raise ValidationError(
                f"Authoritative docs input contains a symlink: {candidate}")
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise ValidationError(
                f"Authoritative docs input contains a non-regular file: {candidate}")
        try:
            logical_path = candidate.relative_to(repository_root).as_posix()
            content = candidate.read_bytes()
        except (OSError, ValueError) as exc:
            raise ValidationError(
                f"Could not read authoritative docs input {candidate}: {exc}") from exc
        files.append(RevisionFile(logical_path=logical_path, content=content))
    return files
