"""Architecture guards: no incident-specific code, no platform CLI outside adapters, small synthetic fixtures.

Layer direction itself is enforced by ``lint-imports`` (contracts in pyproject.toml).
``TRANSITIONAL`` lists paths that still violate a rule; it may only shrink.
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
    "project_names": {
        "src/omac/agents/backend-eng/skills/software-development/SKILL.md",
        "src/omac/agents/frontend-eng/skills/software-development/SKILL.md",
        "src/omac/core/acceptance.py",
        "src/omac/engines/models.py",
        "src/omac/guide/artifacts/acceptance.md",
        "src/omac/guide/en/artifacts/acceptance.md",
        "src/omac/guide/en/recovery.md",
        "src/omac/guide/recovery.md",
        "src/omac/pipeline/publication_review.py",
        "src/omac/pipeline/sdk_checkpoint_review.py",
        "src/omac/pipeline/sdk_publication_review.py",
    },
    "captured_literals": {
        "src/omac/core/credential_prospective.py",
        "src/omac/core/historical_review_source.py",
        "src/omac/core/owner_amendment.py",
        "src/omac/core/prospective_owner.py",
        "src/omac/pipeline/evidence_handoff.py",
        "src/omac/pipeline/sdk_checkpoint_review.py",
        "src/omac/pipeline/sdk_publication_review.py",
    },
    "id_comparisons": {
        "src/omac/core/historical_review_source.py",
        "src/omac/core/owner_amendment.py",
        "src/omac/pipeline/evidence_handoff.py",
    },
    "subprocess_use": {
        "src/omac/core/state_sync_recovery.py",
    },
    "platform_argv": {
        "src/omac/pipeline/evidence_review.py",
        "src/omac/pipeline/publication_review.py",
        "src/omac/pipeline/sdk_publication_review.py",
    },
}
# Fixture entries (names directly under tests/fixtures) that predate the size/content rule.
TRANSITIONAL_FIXTURES: set[str] = {
    "aiteam_849_legacy_convergence_snapshot.yaml",
    "amend_budget_retention",
    "completed_review_source_identity",
    "consumed_review_node_local",
    "convergence_failed_handoff.json",
    "convergence_metadata_before_handoff.json",
    "credential_prospective_source",
    "current_owner_source",
    "evidence_handoff",
    "executor_reject_ledger.yaml",
    "executor_reject_report.yaml",
    "full_state_transport",
    "harness_handoff_round3",
    "immutable_github_binary",
    "multica_issue_aiteam_812.json",
    "normal_reservation_hydration",
    "oac_legacy",
    "operator_assignment_observation",
    "operator_reservation_dispatch",
    "operator_review_recovery",
    "owner_amendment_admission",
    "preview_publication",
    "prospective_owner_assessment",
    "reject_integration_evidence",
    "sdk_command18_checkpoint",
    "sdk_native_publication",
    "system_historical_source",
    "ui_preserved_owner_assessment",
    "unconsumed_evidence_retirement",
    "untracked_preservation",
}
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
            for name in ("captured_literals", "id_comparisons", "subprocess_use", "platform_argv"):
                found[name] += getattr(rules, name)(rel, source)
    return {n: [v for v in vs if v.split(":")[0] not in TRANSITIONAL[n]] for n, vs in found.items()}


@pytest.fixture(scope="module")
def violations():  # one tree walk in fixture setup; the tests below only assert
    return _violations()


@pytest.mark.parametrize("rule", sorted(TRANSITIONAL))
def test_source_has_no_forbidden_pattern(rule, violations):
    assert violations[rule] == []


def test_decide_modules_stay_pure():
    out = []
    for rel, path in _packaged_files():
        if path.suffix == ".py":
            out += rules.decide_imports(rel, _read(path))
    assert out == []


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


def test_platform_cli_allowed_only_in_engines():
    snippet = 'subprocess.run(["multica", "x"])\n'
    assert rules.subprocess_use("src/omac/engines/multica.py", snippet) == []
    assert rules.subprocess_use("src/omac/core/gitsync.py", snippet)
    assert rules.platform_argv("src/omac/engines/multica.py", 'a = ["gh", "pr"]\n') == []


def test_decide_module_importing_engines_is_rejected():
    assert rules.decide_imports("src/omac/pipeline/decide_x.py", "from omac.engines.models import X\n")
    assert rules.decide_imports("src/omac/pipeline/loop.py", "from omac.engines.models import X\n") == []


def test_smoke_trigger_rule():
    assert rules.smoke_triggers("on:\n  schedule: []\n  workflow_dispatch:\n") == []
    assert rules.smoke_triggers("on:\n  pull_request:\n  schedule: []\n")
