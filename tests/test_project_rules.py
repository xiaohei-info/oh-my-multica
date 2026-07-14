from pathlib import Path

import pytest

from omac.core.project_rules import (
    END_MARKER,
    START_MARKER,
    merge_project_rules,
    read_agents_snapshot,
    write_project_rules,
)
from omac.errors import NeedsDecision, ValidationError


RULES = "## Project rules\n\n- Preserve compatibility.\n"


def test_merge_creates_managed_section_for_empty_file():
    result = merge_project_rules("", RULES)

    assert result == (
        f"{START_MARKER}\n"
        "## Project rules\n\n- Preserve compatibility.\n"
        f"{END_MARKER}\n"
    )


def test_merge_appends_without_changing_user_content():
    original = "# User rules\n\nKeep this exactly.\n"

    result = merge_project_rules(original, RULES)

    assert result.startswith(original)
    assert result.count(START_MARKER) == 1
    assert "Keep this exactly." in result


def test_merge_replaces_only_managed_section_idempotently():
    original = (
        "# User rules\n\n"
        f"{START_MARKER}\nold\n{END_MARKER}\n"
        "\nFooter stays.\n"
    )

    first = merge_project_rules(original, RULES)
    second = merge_project_rules(first, RULES)

    assert first == second
    assert "old" not in first
    assert "Footer stays." in first


@pytest.mark.parametrize("content", [
    f"{START_MARKER}\nmissing end\n",
    f"{END_MARKER}\nmissing start\n",
    f"{END_MARKER}\n{START_MARKER}\n",
    f"{START_MARKER}\na\n{END_MARKER}\n{START_MARKER}\nb\n{END_MARKER}\n",
])
def test_merge_rejects_malformed_managed_markers(content):
    with pytest.raises(ValidationError):
        merge_project_rules(content, RULES)


def test_write_refuses_concurrent_agents_change(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text("# Initial\n")
    snapshot = read_agents_snapshot(str(path))
    path.write_text("# User changed this\n")

    with pytest.raises(NeedsDecision) as exc:
        write_project_rules(RULES, snapshot, str(path))

    assert exc.value.report["reason"] == "agents_changed_during_plan"
    assert path.read_text() == "# User changed this\n"


def test_snapshot_rejects_malformed_existing_file(tmp_path):
    path = Path(tmp_path) / "AGENTS.md"
    path.write_text(f"{START_MARKER}\nmissing end\n")

    with pytest.raises(ValidationError):
        read_agents_snapshot(str(path))
