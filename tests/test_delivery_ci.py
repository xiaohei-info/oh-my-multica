"""CI gate configuration and the manifest-status mapping (pure).

The gate itself — a real check command, bounces and the cap on a real store, and `collect_results` end to end —
runs on Docker Multica: tests/it/test_it_delivery_ci.py. The decisions (comment, counter, block at the cap) are the
plans of tests/test_decide_plans.py.
"""
from __future__ import annotations

import pytest

from omac.core.config import DEFAULT_GITHUB_CHECK_COMMAND, get_ci_config
from omac.core.workitem import WorkItemStatus
from omac.pipeline.delivery import MANIFEST_TO_PLATFORM_STATUS, to_platform_status


# ── 状态映射表 ─────────────────────────────────────────────────────────────

class TestStatusMapping:
    def test_ci_check_maps_to_in_progress(self):
        assert to_platform_status("ci_check") is WorkItemStatus.IN_PROGRESS

    def test_merging_maps_to_in_review(self):
        assert to_platform_status("merging") is WorkItemStatus.IN_REVIEW

    def test_full_table(self):
        assert MANIFEST_TO_PLATFORM_STATUS == {
            "todo": WorkItemStatus.TODO, "in_progress": WorkItemStatus.IN_PROGRESS,
            "ci_check": WorkItemStatus.IN_PROGRESS, "in_review": WorkItemStatus.IN_REVIEW,
            "merging": WorkItemStatus.IN_REVIEW, "done": WorkItemStatus.DONE,
            "blocked": WorkItemStatus.BLOCKED}

    def test_unknown_status_teaches(self):
        with pytest.raises(ValueError) as exc:
            to_platform_status("bogus")
        assert "Valid values" in str(exc.value)


# ── which CI command runs, if any ───────────────────────────────────────────

WORKFLOW = ".github/workflows/ci.yml"


@pytest.fixture
def repo(tmp_path):
    return tmp_path


def with_workflow(root):
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / WORKFLOW).write_text("name: ci\n", encoding="utf-8")


def test_ci_is_skipped_when_nothing_is_configured_and_there_is_no_workflow(repo):
    assert get_ci_config({}, repo) is None


def test_a_ci_block_without_a_command_is_not_a_ci_gate_when_there_is_no_workflow(repo):
    assert get_ci_config({"ci": {"timeout_minutes": 30}}, repo) is None


def test_a_github_workflow_turns_on_gh_pr_checks_with_the_default_timeout(repo):
    with_workflow(repo)

    assert get_ci_config({}, repo) == {"check_command": DEFAULT_GITHUB_CHECK_COMMAND, "timeout_minutes": 30}
    assert DEFAULT_GITHUB_CHECK_COMMAND == "gh pr checks {pr_url} --watch --fail-fast"


def test_a_configured_timeout_applies_to_the_default_workflow_command(repo):
    with_workflow(repo)

    assert get_ci_config({"ci": {"timeout_minutes": 7}}, repo)["timeout_minutes"] == 7


def test_an_explicit_command_wins_over_the_workflow(repo):
    with_workflow(repo)
    explicit = {"check_command": "sh ci.sh {pr_url}", "timeout_minutes": 5}

    assert get_ci_config({"ci": explicit}, repo) == explicit
