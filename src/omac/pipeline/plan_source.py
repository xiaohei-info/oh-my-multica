"""The design source set of ``plan create --doc``: what the source issue carries for every later stage.

A directory is its inventory (path + sha256 per file) at one revision. A single file is its body, inline, plus the same
anchors: its path, the revision, and the inventory of its directory, so agents can find the appendices it refers to.
Links are not parsed. Agents clone the remote, so a source inside the repository must be committed and pushed.
"""
from __future__ import annotations

import difflib
import hashlib
from pathlib import Path

from ..core.gitsync import ensure_sources_published, repository_revision
from ..core.repository_files import revision_directory_files
from ..errors import ValidationError
from ..i18n import ui

_REVISION_LINE = "repository_revision:"
_MAX_DIFF_LINES = 200
_MAX_DIFF_BYTES = 16 * 1024


def source_identity(text: str) -> str:
    """The source without its revision line: an unrelated commit does not make a source new."""
    return "\n".join(line for line in text.splitlines() if not line.startswith(_REVISION_LINE))


def _revision_of(text: str) -> str:
    line = next((l for l in text.splitlines() if l.startswith(_REVISION_LINE)), "")
    return line[len(_REVISION_LINE):].strip().strip("`")


def source_change(previous: str, current: str) -> dict:
    """What a replaced source set changed, as facts: both identities and a bounded line diff.

    For a directory source the diff lists the files whose sha256 or presence changed (and the revisions to compare with
    git); for a single file it is the diff of its body. Nothing here interprets the text.
    """
    before, after = source_identity(previous), source_identity(current)
    lines = list(difflib.unified_diff(
        before.splitlines(), after.splitlines(), "previous source", "current source", n=1, lineterm=""))
    diff = "\n".join(lines[:_MAX_DIFF_LINES])
    if len(lines) > _MAX_DIFF_LINES or len(diff.encode("utf-8")) > _MAX_DIFF_BYTES:
        diff = diff.encode("utf-8")[:_MAX_DIFF_BYTES].decode("utf-8", "ignore") + "\n... (diff truncated)"
    return {
        "diff": diff, "previous_revision": _revision_of(previous), "revision": _revision_of(current),
        "previous_sha256": hashlib.sha256(before.encode("utf-8")).hexdigest(),
        "sha256": hashlib.sha256(after.encode("utf-8")).hexdigest(),
    }


def _instructions(language: str | None) -> str:
    return ui(
        "Recursively inspect this directory and read every design document and necessary linked material. "
        "Determine authority, currentness, scope, and conflicts from repository governance and document "
        "content. Do not assume that any filename or subdirectory has a built-in meaning. Complete only "
        "the current stage delivery required by `work show`, and preserve precise file and section "
        "references for downstream stages.",
        "递归检查该目录,阅读其中全部设计文档及其引用的必要资料。根据仓库治理规则和文档内容判断"
        "权威关系、当前有效性、适用范围与冲突,不得假设任何文件名或子目录具有内置语义。只完成 "
        "work show 当前阶段要求的交付,并为下游阶段保留精确文件与章节引用。",
        language=language)


def _inventory(directory: Path, repository_root: Path, original: str) -> str:
    files = revision_directory_files(directory, repository_root=repository_root)
    if not files:
        raise ValidationError(ui(
            f"Design directory contains no files: {original}", f"设计文档目录中没有文件:{original}"))
    return "\n".join(f"- `{f.logical_path}` sha256=`{hashlib.sha256(f.content).hexdigest()}`" for f in files)


def build_design_source(path: str, *, engine_type: str | None, language: str | None = None) -> str:
    """Render the source set for ``--doc <path>`` (file or directory)."""
    target = Path(path)
    if not target.exists():
        raise ValidationError(ui(f"File not found: {path}. Check the --doc path.", f"文件不存在: {path} —— 请确认 --doc 路径"))
    repository_root = Path.cwd().resolve()
    resolved = target.resolve()
    try:
        relative = resolved.relative_to(repository_root)
    except ValueError:
        relative = None
    if target.is_dir():
        if relative is None:
            raise ValidationError(ui(
                f"Design directory must be inside the current repository: {path}",
                f"设计文档目录必须位于当前仓库内:{path}"))
        ensure_sources_published([relative.as_posix()], engine_type=engine_type)
        return ("# OMAC design directory source set\n\n"
                f"repository_revision: `{repository_revision()}`\n\n"
                f"design_directory: `{relative.as_posix()}`\n\n{_instructions(language)}\n\n"
                f"## File inventory\n\n{_inventory(resolved, repository_root, path)}\n")
    body = resolved.read_text(encoding="utf-8")
    if relative is None:
        return _file_source(body, language, ui(
            "This file is outside the repository: files it refers to by relative path are not available. "
            "Pass the directory that holds them with `--doc <directory>` instead.",
            "该文件位于仓库之外:它以相对路径引用的文件不可见。请改用 `--doc <目录>` 传入包含它们的目录。"))
    ensure_sources_published([relative.as_posix()], engine_type=engine_type)
    header = f"repository_revision: `{repository_revision()}`\n\nprimary: `{relative.as_posix()}`\n\n"
    if relative.parent == Path("."):
        return _file_source(body, language, ui(
            "This file is at the repository root, so no directory inventory is listed. "
            "If it has appendices, put them in a directory and pass `--doc <directory>`.",
            "该文件位于仓库根目录,不列目录清单。若有附录,请放进一个目录并使用 `--doc <目录>`。"), header)
    return _file_source(body, language, None, header, _inventory(resolved.parent, repository_root, path),
                        relative.parent.as_posix())


def _file_source(body: str, language, note, header: str = "", inventory: str | None = None,
                 directory: str | None = None) -> str:
    parts = ["# OMAC design source set\n\n", header]
    if directory:
        parts.append(f"design_directory: `{directory}`\n\n")
    if note:
        parts.append(f"note: {note}\n\n")
    parts.append(f"{_instructions(language)}\n\n## Primary document\n\n{body.rstrip()}\n")
    if inventory:
        parts.append(f"\n## File inventory\n\n{inventory}\n")
    return "".join(parts)
