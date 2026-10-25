"""AST and text rules that keep incident-specific code and platform-CLI calls out of ``src/omac``.

Each rule takes ``(relpath, source)`` and returns ``["path:line: message", ...]`` so
``tests/test_architecture.py`` can both run it over the tree and unit-test it on snippets.
"""
from __future__ import annotations

import ast
import functools
import re

UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
HEX_DIGEST_RE = re.compile(r"\b(?:[0-9a-f]{64}|[0-9a-f]{40})\b")
PROJECT_NAME_RE = re.compile(r"open-agent-cluster|\boac\b|aiteam", re.IGNORECASE)
ID_NAMES = {"node_id", "dag_key", "workspace_id"}
LOCAL_CLIS = {"git", "bash", "lsof"}
PLATFORM_CLIS = {"multica", "gh", "curl"}  # adapters only; "curl" is the multica adapter's HTTP fallback
SUBPROCESS_ALLOWED = {
    "src/omac/engines/multica.py",
    "src/omac/engines/github.py",
    "src/omac/engines/mock.py",  # removed with mock in Phase 8
    "src/omac/core/gitsync.py",
    "src/omac/core/repository_files.py",
    "src/omac/core/review_preflight.py",
    "src/omac/pipeline/plan.py",
    "src/omac/cli/commands/init_cmd.py",
}
ENGINES_PREFIX = "src/omac/engines/"


_parse = functools.lru_cache(maxsize=None)(ast.parse)  # every rule walks the same ~80 modules


def _tail(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_id_expr(node: ast.AST) -> bool:
    tail = _tail(node)
    if tail in ID_NAMES:
        return True
    return tail == "id" and isinstance(node, ast.Attribute) and "item" in (_tail(node.value) or "")


def _str(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _special_literal(node: ast.AST) -> bool:
    return _str(node) and node.value != ""  # `x in ("", y)` is an emptiness check, not a special case


def captured_literals(path: str, source: str) -> list[str]:
    """Rule 1: no UUID or 40/64-hex digest literals in string constants."""
    out = []
    for node in ast.walk(_parse(source)):
        if _str(node) and (UUID_RE.search(node.value) or HEX_DIGEST_RE.search(node.value)):
            out.append(f"{path}:{node.lineno}: captured identifier or digest literal in source")
    return out


def id_comparisons(path: str, source: str) -> list[str]:
    """Rule 2: node_id / dag_key / work_item.id / workspace_id are never compared with a string literal."""
    out = []
    for node in ast.walk(_parse(source)):
        if not isinstance(node, ast.Compare):
            continue
        sides = [node.left, *node.comparators]
        literal = any(_special_literal(s) or (isinstance(s, (ast.Tuple, ast.List, ast.Set))
                                              and any(_special_literal(e) for e in s.elts)) for s in sides)
        if literal and any(_is_id_expr(s) for s in sides):
            out.append(f"{path}:{node.lineno}: identifier compared with a string literal (special case)")
    return out


def project_names(path: str, source: str) -> list[str]:
    """Rule 3: no known project names anywhere in packaged code, guides, or agent templates."""
    return [f"{path}:{source.count(chr(10), 0, m.start()) + 1}: project-specific name"
            for m in PROJECT_NAME_RE.finditer(source)]


def subprocess_use(path: str, source: str) -> list[str]:
    """Subprocess only in allowed modules; argv[0] literal must be git/bash/lsof (multica/gh: engines only)."""
    tree = _parse(source)
    out = []
    if path not in SUBPROCESS_ALLOWED:
        for node in ast.walk(tree):
            if (isinstance(node, ast.Import) and any(a.name == "subprocess" for a in node.names)) or (
                    isinstance(node, ast.ImportFrom) and node.module == "subprocess"):
                out.append(f"{path}:{node.lineno}: subprocess outside the allowlist")
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and _tail(node.func.value) == "subprocess" and node.args
                and isinstance(node.args[0], (ast.List, ast.Tuple)) and node.args[0].elts
                and _str(node.args[0].elts[0])):
            head = node.args[0].elts[0].value
            allowed = LOCAL_CLIS | (PLATFORM_CLIS if path.startswith(ENGINES_PREFIX) else set())
            if head not in allowed:
                out.append(f"{path}:{node.lineno}: subprocess argv[0] {head!r} not allowed here")
    return out


def platform_argv(path: str, source: str) -> list[str]:
    """Rule F6: no list literal starting with "multica" or "gh" outside engines/."""
    if path.startswith(ENGINES_PREFIX):
        return []
    return [f"{path}:{n.lineno}: platform CLI argv literal outside engines/"
            for n in ast.walk(_parse(source))
            if isinstance(n, (ast.List, ast.Tuple)) and n.elts and _str(n.elts[0])
            and n.elts[0].value in {"multica", "gh"}]


def engine_assembly(path: str, source: str) -> list[str]:
    """Engines are assembled by ``engines.engine_from_config``; ``create_engine`` stays inside engines/."""
    if path.startswith(ENGINES_PREFIX):
        return []
    return [f"{path}:{n.lineno}: create_engine called outside engines/ (use engine_from_config)"
            for n in ast.walk(_parse(source))
            if isinstance(n, ast.Call) and _tail(n.func) == "create_engine"]


def decide_imports(path: str, source: str) -> list[str]:
    """decide_* modules stay pure: no engines or subprocess imports."""
    if not path.rsplit("/", 1)[-1].startswith("decide"):
        return []
    out = []
    for node in ast.walk(_parse(source)):
        names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                 else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
        if any(n == "subprocess" or n.startswith("omac.engines") for n in names) or (
                isinstance(node, ast.ImportFrom) and node.level and (node.module or "").startswith("engines")):
            out.append(f"{path}:{node.lineno}: decide module imports engines or subprocess")
    return out


def smoke_triggers(source: str) -> list[str]:
    """smoke.yml may be triggered only by schedule and workflow_dispatch."""
    import yaml

    doc = yaml.safe_load(source) or {}
    triggers = doc.get(True, doc.get("on")) or {}  # YAML 1.1 parses the key `on` as True
    names = {triggers} if isinstance(triggers, str) else set(triggers)
    extra = names - {"schedule", "workflow_dispatch"}
    return [f"smoke.yml: forbidden trigger {t!r}" for t in sorted(extra)]
