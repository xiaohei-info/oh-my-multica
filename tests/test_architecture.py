"""Architecture guards: no incident-specific code, no platform CLI outside adapters, small synthetic fixtures.

Layer direction itself is enforced by ``lint-imports`` (contracts in pyproject.toml).
``TRANSITIONAL`` lists paths that still violate a rule; it may only shrink, and it is empty today.
"""
from __future__ import annotations

import functools
import re
from pathlib import Path

import pytest

from _support import arch_rules as rules

ROOT = Path(__file__).resolve().parent.parent
PACKAGED_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".txt", ".html", ".js", ".css"}
FIXTURE_FILE_MAX = 64 * 1024
FIXTURE_TOTAL_MAX = 1024 * 1024
FIXTURE_FORBIDDEN = re.compile(
    r"/Users/[A-Za-z0-9._-]+|/Volumes/[A-Za-z0-9._-]+|[A-Za-z0-9._%+-]+@(?!example\.(?:com|org|test|invalid)\b)"
    r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PLACEHOLDER_UUID = re.compile(r"^0{8}-0{4}-0{4}-0{4}-0{12}$|^[0-9a-f]{8}-0000-0000-0000-0{12}$")

# Rule name -> relative paths still violating it. Phase 1 empties all of them.
TRANSITIONAL: dict[str, set[str]] = {
    "project_names": set(),
    "captured_literals": set(),
    "id_comparisons": set(),
    "subprocess_use": set(),
    "platform_argv": set(),
    "engine_assembly": set(),
    "function_length": set(),
    "inline_effects": set(),
}
# Fixture entries (names directly under tests/fixtures) that predate the size/content rule.
TRANSITIONAL_FIXTURES: set[str] = set()
SYNTHETIC_FIXTURES = {"smoke_p1.yaml", "smoke_live_manifest.yaml"}


@functools.lru_cache(maxsize=None)
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@functools.lru_cache(maxsize=None)
def _packaged_files():
    return tuple((path.relative_to(ROOT).as_posix(), path)
                 for path in sorted((ROOT / "src" / "omac").rglob("*"))
                 if path.is_file() and "__pycache__" not in path.parts and path.suffix in PACKAGED_SUFFIXES)


def _violations():
    found = {name: [] for name in TRANSITIONAL}
    for rel, path in _packaged_files():
        source = _read(path)
        found["project_names"] += rules.project_names(rel, source)
        if path.suffix == ".py":
            for name in ("captured_literals", "id_comparisons", "subprocess_use", "platform_argv", "engine_assembly",
                         "function_length", "inline_effects"):
                found[name] += getattr(rules, name)(rel, source)
    return {n: [v for v in vs if v.split(":")[0] not in TRANSITIONAL[n]] for n, vs in found.items()}


@pytest.fixture(scope="module")
def violations():  # one tree walk in fixture setup; the tests below only assert
    return _violations()


@pytest.mark.parametrize("rule", sorted(TRANSITIONAL))
def test_source_has_no_forbidden_pattern(rule, violations):
    assert violations[rule] == []


def test_decide_modules_stay_pure():
    out, seen = [], 0
    for rel, path in _packaged_files():
        if path.suffix == ".py":
            seen += rel.rsplit("/", 1)[-1].startswith("decide")
            out += rules.decide_imports(rel, _read(path))
    assert seen, "expected at least one decide_* module"
    assert out == []


@pytest.fixture(scope="module")
def test_file_texts():  # read in setup: the call then costs a substring check, whatever the disk load
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(Path(__file__).parent.rglob("test_*.py"))}


def test_tests_do_not_assert_on_source_text(test_file_texts):
    """Tests protect behavior, not wording (AGENTS.md, Tests rule 3)."""
    import ast
    offenders = []
    for name, text in test_file_texts.items():
        if "getsource" not in text:
            continue
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Attribute) and node.attr == "getsource":
                offenders.append(f"{name}:{node.lineno}")
    assert offenders == []


def test_smoke_workflow_is_schedule_or_manual_only():
    smoke = ROOT / ".github" / "workflows" / "smoke.yml"
    if smoke.exists():
        assert rules.smoke_triggers(smoke.read_text(encoding="utf-8")) == []


def test_fixtures_are_small_and_synthetic():
    fixtures = ROOT / "tests" / "fixtures"
    problems, total = [], 0
    for path in sorted(p for p in fixtures.rglob("*") if p.is_file()):
        rel = path.relative_to(fixtures)
        if rel.parts[0] in TRANSITIONAL_FIXTURES:
            continue
        size = path.stat().st_size
        total += size
        if size > FIXTURE_FILE_MAX:
            problems.append(f"{rel}: {size} bytes exceeds {FIXTURE_FILE_MAX}")
        text = path.read_bytes().decode("utf-8", errors="ignore")
        if FIXTURE_FORBIDDEN.search(text):
            problems.append(f"{rel}: machine path or real email address")
        for uuid in rules.UUID_RE.findall(text):
            if not PLACEHOLDER_UUID.match(uuid):
                problems.append(f"{rel}: non-placeholder UUID {uuid}")
                break
    if total > FIXTURE_TOTAL_MAX:
        problems.append(f"tests/fixtures totals {total} bytes, over {FIXTURE_TOTAL_MAX}")
    assert problems == []


# --- the rules themselves: each must reject a representative violation ---

@pytest.mark.parametrize("rule,snippet", [
    ("captured_literals", 'X = "3f2b8c1e-1111-4222-8333-444455556666"\n'),
    ("captured_literals", 'X = "' + "a" * 40 + '"\n'),
    ("id_comparisons", 'if node_id != "ui-foundation":\n    pass\n'),
    ("id_comparisons", 'ok = work_item.id == "abc"\n'),
    ("project_names", "# handled for the aiteam project\n"),
    ("subprocess_use", "import subprocess\n"),
    ("subprocess_use", 'import os\nsubprocess.run(["rm", "-rf"])\n'),
    ("platform_argv", 'cmd = ["multica", "issue", "list"]\n'),
    ("engine_assembly", 'engine = create_engine(kind, config)\n'),
    ("function_length", "def f():\n" + "    x = 1\n" * 150),
])
def test_rule_rejects_violation(rule, snippet):
    assert getattr(rules, rule)("src/omac/pipeline/x.py", snippet)


@pytest.mark.parametrize("rule,snippet", [
    ("id_comparisons", 'ok = dag_key in ("", spec.dag_key)\n'),
    ("id_comparisons", 'if node_id == other_id:\n    pass\n'),
    ("captured_literals", 'X = "abc123"\n'),
    ("subprocess_use", 'import os\nsubprocess.run(["git", "status"])\n'),
])
def test_rule_accepts_generic_code(rule, snippet):
    assert getattr(rules, rule)("src/omac/core/gitsync.py", snippet) == []


def test_create_engine_is_allowed_inside_engines_only():
    assert rules.engine_assembly("src/omac/engines/__init__.py", "x = create_engine(a, b)\n") == []
    assert rules.engine_assembly("src/omac/cli/commands/dag.py", "x = engines.create_engine(a, b)\n")


def test_function_length_limit_is_inclusive_and_covers_methods():
    at_limit = "def f():\n" + "    x = 1\n" * (rules.FUNCTION_LINES_MAX - 1)
    assert rules.function_length("src/omac/x.py", at_limit) == []
    method = "class C:\n    async def m(self):\n" + "        x = 1\n" * rules.FUNCTION_LINES_MAX
    assert rules.function_length("src/omac/x.py", method)


def test_platform_cli_allowed_only_in_engines():
    snippet = 'subprocess.run(["multica", "x"])\n'
    assert rules.subprocess_use("src/omac/engines/multica.py", snippet) == []
    assert rules.subprocess_use("src/omac/core/gitsync.py", snippet)
    assert rules.platform_argv("src/omac/engines/multica.py", 'a = ["gh", "pr"]\n') == []


def test_decide_module_importing_engines_is_rejected():
    assert rules.decide_imports("src/omac/pipeline/decide_x.py", "from omac.engines.models import X\n")
    assert rules.decide_imports("src/omac/pipeline/decide_x.py", "from ..engines.models import X\n")
    assert rules.decide_imports("src/omac/pipeline/loop.py", "from omac.engines.models import X\n") == []


def test_smoke_trigger_rule():
    assert rules.smoke_triggers("on:\n  schedule: []\n  workflow_dispatch:\n") == []
    assert rules.smoke_triggers("on:\n  pull_request:\n  schedule: []\n")


def test_port_writes_are_rejected_outside_apply():
    snippet = "def f(store, c):\n    store.update_status('i', 1)\n    c.store.add_comment('i', 'x')\n"
    assert len(rules.inline_effects("src/omac/pipeline/loop.py", snippet)) == 2
    assert len(rules.inline_effects("src/omac/cli/commands/node.py", snippet)) == 2


def test_port_writes_are_allowed_in_the_executor_and_reads_are_not_writes():
    snippet = "def f(store):\n    store.update_status('i', 1)\n"
    assert rules.inline_effects("src/omac/pipeline/apply.py", snippet) == []
    assert rules.inline_effects("src/omac/engines/multica.py", snippet) == []
    assert rules.inline_effects("src/omac/pipeline/loop.py", "def f(store):\n    store.get_work_item('i')\n") == []
    assert rules.inline_effects("src/omac/pipeline/loop.py", "def f(t):\n    t.cancel()\n") == []


@pytest.fixture(scope="module")
def test_file_cross_imports():
    return [line for path in sorted((ROOT / "tests").rglob("test_*.py"))
            for line in rules.test_module_imports(path.relative_to(ROOT).as_posix(), _read(path))]


def test_no_test_file_imports_another_test_file(test_file_cross_imports):
    assert test_file_cross_imports == []


def test_the_cross_import_rule_sees_both_import_forms():
    assert rules.test_module_imports("tests/x.py", "from test_a import b\n")
    assert rules.test_module_imports("tests/x.py", "def f():\n    import test_a\n")
    assert rules.test_module_imports("tests/x.py", "from _support.a import b\nimport pytest\n") == []
